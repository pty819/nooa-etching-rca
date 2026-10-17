"""Training-fold isolation includes arbitrary learned feature code, not just sklearn."""

import asyncio

import polars as pl
from test_formula_lab import fixture_case, refresh_hashes, register

from factor_demo.formula_models import FormulaPlan, FormulaSpec

LEARNED = """from pathlib import Path
import polars as pl
class State:
    def __init__(self,value): self.value=value
def fit(samples,steps,params,labels):
    assert len(samples)==len(labels)
    assert len(list(Path('../input').glob('*.parquet')))==2*len(samples)
    return State(sum(x['a'].mean() for x in samples.values())/len(samples))
def transform(state,sample,steps,params):
    assert len(list(Path('../input').glob('*.parquet')))==2
    return float((sample['a']>state.value).mean())
"""


def register_learned(lab, study):
    (lab.run / "workspace/artifacts/learned.py").write_text(LEARNED)
    return lab.register(
        study["id"],
        FormulaSpec(
            name="cohort",
            expression="fraction(a>training_mean)",
            description="threshold learned only on this fold training data",
            source="artifacts/learned.py",
            sensors=["a"],
            steps=[2],
            unit="1",
            kind="learned",
        ),
    )


def test_dev_distribution_cannot_change_training_features_for_learned_or_pure_operators(tmp_path):
    async def exercise():
        lab = fixture_case(tmp_path / "case")
        study = lab.create(
            FormulaPlan(title="fold fit", labels=["good", "bad"], models=["logistic"])
        )
        register_learned(lab, study)
        before = await lab.matrix(lab.get(study["id"]), lab.get(study["id"])["sets"][0])
        assert set(before["fold"]) == {"cv-0", "cv-1", "cv-2", "dev"}
        meta = pl.read_parquet(lab.run / "input/runs.parquet")
        chosen = meta.filter(pl.col("split") == "dev").row(0, named=True)
        path = lab.run / "input" / chosen["trace_path"]
        pl.read_parquet(path).with_columns((pl.col("a") + 100).alias("a")).write_parquet(path)
        refresh_hashes(lab.run)
        newer = lab.create(
            FormulaPlan(title="changed dev", labels=["good", "bad"], models=["logistic"])
        )
        register_learned(lab, newer)
        after = await lab.matrix(lab.get(newer["id"]), lab.get(newer["id"])["sets"][0])
        assert before.filter(pl.col("split") == "train").equals(
            after.filter(pl.col("split") == "train")
        )
        assert set(before.filter(pl.col("split") == "train")["cohort@1"]) == {0.4}
        result = await lab.evaluate(newer["id"], ["cohort@1"])
        assert result["trials"][0]["status"] == "succeeded", result["trials"]
        frozen = await lab.freeze(newer["id"])
        assert frozen["status"] == "selected"
        # A per-sample function attempting cohort fitting can now see only itself.
        pure = """from pathlib import Path
import polars as pl
x=[pl.read_parquet(p) for p in Path('../input').glob('*.parquet')]
threshold=sum(f['a'].mean() for f in x if 'a' in f.columns)
def extract(trace,steps,params):return float((trace['a']>threshold).mean())
"""
        register(lab, newer, "pure", pure)
        matrix = await lab.matrix(
            lab.get(newer["id"]),
            next(s for s in lab.get(newer["id"])["sets"] if s["formulas"] == ["pure@1"]),
        )
        assert set(matrix["pure@1"]) == {0.4}

    asyncio.run(exercise())
