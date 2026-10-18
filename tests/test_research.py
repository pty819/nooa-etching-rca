import json

import numpy as np
import pandas as pd
import pytest

from factor_demo.legacy.cli import finalize
from factor_demo.legacy.factors import Factor, transform
from factor_demo.legacy.research import Research
from factor_demo.legacy.synthetic import generate, write_json


@pytest.fixture
def data(tmp_path):
    path = tmp_path / "data"
    generate(path, rows=600, seed=7)
    return path


def test_data_contract_and_batch_separation(data):
    parts = [pd.read_parquet(data / f"{s}.parquet") for s in ["train", "dev", "test"]]
    assert all(df.shape[1] == 2005 and df.columns[4] == "label" for df in parts)
    ids = [set(df.index) for df in parts]
    assert not (ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2])
    batches = [{int(i.split("_")[1]) // 20 for i in part} for part in ids]
    assert not (batches[0] & batches[1] or batches[0] & batches[2] or batches[1] & batches[2])


def test_research_does_not_read_holdout_or_truth(data, tmp_path, monkeypatch):
    real_read = pd.read_parquet

    def guarded(path, *a, **kw):
        assert path.name != "test.parquet"
        return real_read(path, *a, **kw)

    monkeypatch.setattr(pd, "read_parquet", guarded)
    (data / "truth.private.json").unlink()
    r = Research(data, tmp_path / "run", 3)
    before = r.screen(5)
    r.dev["label"] = r.dev.label.sample(frac=1, random_state=1).to_numpy()
    assert r.screen(5) == before  # screening uses training labels only


def test_signal_generalization_budget_and_resume(data, tmp_path):
    truth = json.loads((data / "truth.private.json").read_text())
    run = tmp_path / "run"
    r = Research(data, run, 3)
    factor = dict(name="delta", **truth["difference"])
    first = r.evaluate([factor], "Compare the same sensor across steps")
    assert first["metrics"]["balanced_accuracy"] > 0.90
    duplicate = r.evaluate([{**factor, "name": "renamed"}], "Same expression")
    assert duplicate["status"] == "duplicate"
    assert len(r.records) == 1
    bad = r.evaluate([{"name": "leak", "operation": "raw", "columns": ["label"]}], "Invalid")
    assert bad["status"] == "failed"
    unstable = r.evaluate(
        [{"name": "spur", "operation": "raw", "columns": [truth["unstable_signal"]]}],
        "Check generalization",
    )
    assert unstable["metrics"]["train_balanced_accuracy"] > 0.90
    assert unstable["metrics"]["balanced_accuracy"] < 0.35
    assert r.evaluate([factor], "No remaining budget")["status"] == "budget_exhausted"
    resumed = Research(data, run, 5)
    assert len(resumed.records) == 3 and resumed.best()["id"] == first["id"]
    write_json(
        run / "conclusion.json",
        {
            "selected_experiment_id": first["id"],
            "summary": "Test fixture conclusion",
            "limitations": ["Synthetic only"],
        },
    )
    final = finalize(run)
    assert final["holdout"]["balanced_accuracy"] > 0.90
    assert final["synthetic_truth_check"]["contains_injected_difference"]
    assert not final["synthetic_truth_check"]["contains_unstable_input"]
    assert finalize(run) == final
    with pytest.raises(ValueError, match="frozen"):
        Research(data, run, 10)


def test_preprocessing_is_fit_on_training_only(data, tmp_path):
    from factor_demo.legacy.research import fit_model

    x = np.array([[0.0], [2.0], [np.nan], [4.0], [3.0], [1.0]])
    y = pd.Series(["label1", "label2", "label3", "label1", "label2", "label3"])
    model = fit_model(x, y)
    assert model[0].statistics_[0] == 2.0
    model.predict(np.array([[1e9], [np.nan]]))
    assert model[0].statistics_[0] == 2.0


def test_ratio_and_metadata_rejection():
    df = pd.DataFrame({"a": [1.0, 2.0], "b": [0.0, 2.0], "label": [1, 2]})
    specs = [Factor(name="r", operation="ratio", columns=["a", "b"])]
    values = transform(df, specs, {"a", "b"})
    assert np.isnan(values[0, 0]) and values[1, 0] == 1.0
    with pytest.raises(ValueError, match="catalogued"):
        transform(df, [Factor(name="bad", operation="raw", columns=["label"])], {"a", "b"})


def test_changed_dataset_is_rejected(data, tmp_path):
    train = pd.read_parquet(data / "train.parquet")
    train.iloc[0, 5] += 100
    train.to_parquet(data / "train.parquet")
    with pytest.raises(ValueError, match="manifest"):
        Research(data, tmp_path / "run", 3)
