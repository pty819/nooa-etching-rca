"""Facts need protected provenance and a declared calculation, not a plausible CSV."""

import asyncio

import polars as pl
import pytest
from nooa.unifiedllm import FakeLLMClient
from test_capabilities import public_case

from factor_demo.autoresearch_agent import AutoresearchAgent
from factor_demo.dataset_models import DatasetDerivation
from factor_demo.datasets import DatasetSession
from factor_demo.evidence import EvidenceError, EvidenceVerifier
from factor_demo.research_models import (
    EvidenceCheck,
    EvidenceRef,
    Hypothesis,
    RawEvidenceSpec,
    ResearchReply,
)
from factor_demo.research_workspace import PythonWorkspace
from factor_demo.trusted_evidence import materialize_raw


def hypothesis(**updates):
    return Hypothesis(
        id="h1",
        title="candidate",
        status="supported",
        mathematical_evidence="e1",
        evidence_ids=["e1"],
        evidence_files=[],
        alternative_explanation="another mechanism remains possible",
        falsification_test="declared numerical check",
        **updates,
    )


def test_handwritten_csv_and_unrun_falsification_are_not_verified_evidence(tmp_path):
    root = public_case(tmp_path / "case")
    file = root / "workspace/artifacts/fake.csv"
    file.write_text("run_id,value\nr1,999999\n")
    ref = EvidenceRef(
        id="e1",
        claim="invented",
        artifact="artifacts/fake.csv",
        column="value",
        value=999999,
        unit="Torr",
    )
    with pytest.raises(EvidenceError, match="provenance"):
        EvidenceVerifier(root).verify(
            ResearchReply(summary="probe", evidence=[ref], hypotheses=[hypothesis()])
        )


def test_raw_fact_is_recomputed_and_completed_check_is_actually_evaluated(tmp_path):
    root = public_case(tmp_path / "case")
    s = DatasetSession(root)
    raw = materialize_raw(
        root,
        RawEvidenceSpec(
            id="e1",
            claim="raw value",
            file="runs.parquet",
            column="value",
            where={"run_id": "r1"},
            unit="V",
        ),
    )
    ref = EvidenceRef.model_validate(raw)
    passing = EvidenceCheck(
        description="value above baseline bound",
        status="completed",
        evidence_id="e1",
        operator="gt",
        threshold=3,
    )
    reply = ResearchReply(
        summary="candidate", evidence=[ref], hypotheses=[hypothesis(checks=[passing])]
    )
    result = EvidenceVerifier(root).verify(reply)
    assert result["checks"][0]["provenance"]["grade"] == "raw_recomputed"
    assert result["hypothesis_checks"]["h1"][0]["passed"]
    passing.threshold = 5
    reply.hypotheses[0].checks = [passing]
    with pytest.raises(EvidenceError, match="supported requires"):
        EvidenceVerifier(root).verify(reply)
    reply.hypotheses[0].status = "rejected"
    assert not EvidenceVerifier(root).verify(reply)["hypothesis_checks"]["h1"][0]["passed"]
    reply.hypotheses[0].status = "supported"
    reply.hypotheses[0].checks = []
    with pytest.raises(EvidenceError, match="completed"):
        EvidenceVerifier(root).verify(reply)
    data = s.path() / "runs.parquet"
    pl.read_parquet(data).with_columns((pl.col("value") + 1).alias("value")).write_parquet(data)
    with pytest.raises((ValueError, EvidenceError), match="hash changed"):
        EvidenceVerifier(root).verify(reply)


def test_arbitrary_python_retains_exploration_without_becoming_a_raw_fact(tmp_path):
    root = public_case(tmp_path / "case")

    async def run():
        worker = PythonWorkspace(root)
        agent = AutoresearchAgent(worker, llm=FakeLLMClient())
        try:
            await agent.run_code(
                "from pathlib import Path\nPath('artifacts/exploration.csv').write_text('value\\n999999\\n')"
            )
            ref = EvidenceRef(
                id="e1",
                claim="custom calculation",
                artifact="artifacts/exploration.csv",
                column="value",
                value=999999,
                unit="1",
            )
            result = EvidenceVerifier(root).verify(
                ResearchReply(summary="exploration", evidence=[ref])
            )
            assert result["checks"][0]["provenance"]["grade"] == "exploratory_reproducible"
            check = EvidenceCheck(
                description="not raw",
                status="completed",
                evidence_id="e1",
                operator="gt",
                threshold=0,
            )
            with pytest.raises(EvidenceError, match="independently recomputed"):
                EvidenceVerifier(root).verify(
                    ResearchReply(
                        summary="probe", evidence=[ref], hypotheses=[hypothesis(checks=[check])]
                    )
                )
        finally:
            await worker.close()
            await agent.aclose()

    asyncio.run(run())


def test_subset_cannot_smuggle_changed_values_or_new_raw_evidence(tmp_path):
    root = public_case(tmp_path / "case")
    s = DatasetSession(root)
    output = root / "workspace/child"
    output.mkdir()
    source = root / "workspace/child.py"
    source.write_text("# subset")
    pl.read_parquet(s.path() / "runs.parquet").write_parquet(output / "runs.parquet")
    (output / "fabricated.csv").write_text("value\n999999\n")
    spec = DatasetDerivation(
        title="fake subset",
        parents=[s.current()["id"]],
        output_dir="child",
        source="child.py",
        reason="probe",
        data_schema=s.schema(),
    )
    with pytest.raises(ValueError, match="New derived files"):
        s.derive(spec)
    (output / "fabricated.csv").unlink()
    pl.read_parquet(output / "runs.parquet").with_columns(pl.lit(999).alias("value")).write_parquet(
        output / "runs.parquet"
    )
    with pytest.raises(ValueError, match="changed original values"):
        s.derive(spec)
    transformed = s.derive(spec.model_copy(update={"relation": "transform"}))
    ref = materialize_raw(
        root,
        RawEvidenceSpec(
            id="e1",
            claim="derived",
            dataset_ref=transformed["id"],
            file="runs.parquet",
            column="value",
            where={"run_id": "r1"},
            unit="1",
        ),
    )
    verified = EvidenceVerifier(root).verify(
        ResearchReply(
            summary="derived",
            dataset_ref=transformed["id"],
            evidence=[EvidenceRef.model_validate(ref)],
        )
    )
    assert verified["checks"][0]["provenance"]["grade"] == "derived_recomputed"


def test_subset_keeps_only_selected_raw_assets(tmp_path):
    import shutil

    from test_formula_lab import fixture_case

    lab = fixture_case(tmp_path / "case")
    s = DatasetSession(lab.run)
    output = lab.run / "workspace/child"
    shutil.copytree(s.path(), output)
    chosen = pl.read_parquet(output / "runs.parquet").head(1)
    chosen.write_parquet(output / "runs.parquet")
    (lab.run / "workspace/child.py").write_text("# select the first original sample")
    doc = s.derive(
        DatasetDerivation(
            title="one sample",
            parents=[s.current()["id"]],
            output_dir="child",
            source="child.py",
            reason="asset closure",
            data_schema=s.schema(),
        )
    )
    root = s.path(doc["id"])
    assert len(list((root / "traces").glob("*.parquet"))) == 1
    assert (root / chosen["trace_path"][0]).is_file()
    assert (
        pl.read_parquet(root / "steps.parquet")["run_id"].unique().to_list()
        == chosen["run_id"].to_list()
    )


def test_trusted_step_window_product_and_declared_units(tmp_path):
    from test_formula_lab import fixture_case

    lab = fixture_case(tmp_path / "case")
    s = DatasetSession(lab.run)
    row = (
        pl.read_parquet(s.path() / "runs.parquet")
        .filter(pl.col("label") == "good")
        .row(0, named=True)
    )
    spec = RawEvidenceSpec(
        id="window",
        claim="step2 from20% product",
        file=row["trace_path"],
        column="a",
        product_column="b",
        statistic="mean",
        unit="V*V",
        step=2,
        start_fraction=0.2,
    )
    ref = materialize_raw(lab.run, spec)
    assert ref["value"] == pytest.approx(1.5)
    checked = EvidenceVerifier(lab.run).verify(
        ResearchReply(summary="window", evidence=[EvidenceRef.model_validate(ref)])
    )
    assert checked["checks"][0]["provenance"]["grade"] == "raw_recomputed"
    with pytest.raises(ValueError, match="count units"):
        materialize_raw(lab.run, spec.model_copy(update={"statistic": "count", "unit": "Torr"}))
