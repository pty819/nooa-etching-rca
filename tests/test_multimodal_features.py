"""The same DatasetRef/formula contract executes table, image and text operators."""

import asyncio
import json

import polars as pl
import pytest
from PIL import Image

from factor_demo.dataset_models import DatasetSchema
from factor_demo.datasets import DatasetSession, prepare_generic_workspace
from factor_demo.formula_lab import FormulaLab
from factor_demo.formula_models import FormulaPlan, FormulaSpec


@pytest.mark.parametrize("modality", ["table", "image", "text"])
def test_formula_operators_work_on_non_etching_modalities(tmp_path, modality):
    source = tmp_path / "input"
    source.mkdir()
    (source / "assets").mkdir()
    rows = []
    for split, lots in [("train", 6), ("dev", 2)]:
        for label, value in [("low", 0), ("high", 255)]:
            for lot in range(lots):
                for wafer in range(2):
                    rid = f"{split}-{label}-{lot}-{wafer}"
                    row = {
                        "sample_id": rid,
                        "label": label,
                        "lot": f"{split}-{label}-{lot}",
                        "split": split,
                        "x": value,
                    }
                    if modality == "image":
                        row["asset_path"] = f"assets/{rid}.png"
                        Image.new("L", (2, 2), value).save(source / row["asset_path"])
                    elif modality == "text":
                        row["asset_path"] = f"assets/{rid}.txt"
                        (source / row["asset_path"]).write_text("x" * (1 + value))
                    rows.append(row)
    pl.DataFrame(rows).write_parquet(source / "samples.parquet")
    schema = DatasetSchema(
        modality=modality,
        label_column="label",
        group_column="lot",
        split_column="split",
        asset_column=None if modality == "table" else "asset_path",
    )
    (source / "dataset.json").write_text(json.dumps({"schema": schema.model_dump()}))
    root = tmp_path / "case"
    prepare_generic_workspace(source, root)
    session = DatasetSession(root)
    lab = FormulaLab(root)
    lab.data_session = session
    study = lab.create(
        FormulaPlan(title="non-etching operator", labels=["low", "high"], models=["logistic"])
    )
    code = {
        "table": "def extract(sample,steps,params):\n    return sample['x'][0]\n",
        "image": "from PIL import Image,ImageStat\ndef extract(sample,steps,params):\n    return ImageStat.Stat(Image.open(sample)).mean[0]\n",
        "text": "def extract(sample,steps,params):\n    return len(sample.read_text())\n",
    }[modality]
    (root / "workspace/artifacts/operator.py").write_text(code)
    lab.register(
        study["id"],
        FormulaSpec(
            name="signal",
            expression="sample intensity/length",
            description="sample-local measurement",
            source="artifacts/operator.py",
            sensors=["x"] if modality == "table" else [],
            unit="1",
        ),
    )

    async def exercise():
        result = await lab.evaluate(study["id"], ["signal@1"])
        assert result["trials"][0]["status"] == "succeeded", result["trials"]
        assert result["trials"][0]["dev_score"] == 1
        frozen = await lab.freeze(study["id"])
        assert frozen["status"] == "selected"

    asyncio.run(exercise())
