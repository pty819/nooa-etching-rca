"""Held-out values and synthetic truth are read only after a one-shot freeze."""

import asyncio
import json
import shutil

import polars as pl
import pytest
from test_formula_lab import PRODUCT, fixture_case, register

from factor_demo.evaluation import finalize_evaluation, prepare_evaluation, truth_score
from factor_demo.formula_models import FormulaPlan
from factor_demo.research_models import ResearchReply


def private_source(lab, tmp_path):
    source = tmp_path / "source"
    shutil.copytree(lab.run / "input", source)
    meta = pl.read_parquet(source / "runs.parquet")
    steps = pl.read_parquet(source / "steps.parquet")
    extra = []
    extra_steps = []
    for label in ["good", "bad"]:
        row = meta.filter(pl.col("label") == label).row(0, named=True)
        original = row["run_id"]
        row = {
            **row,
            "run_id": "test-" + label,
            "batch_id": "test-lot-" + label,
            "split": "test",
            "trace_path": "traces/test-" + label + ".parquet",
        }
        shutil.copyfile(
            source / meta.filter(pl.col("run_id") == original)["trace_path"][0],
            source / row["trace_path"],
        )
        extra.append(row)
        extra_steps.append(
            steps.filter(pl.col("run_id") == original).with_columns(
                pl.lit(row["run_id"]).alias("run_id")
            )
        )
    pl.concat([meta, pl.DataFrame(extra)], how="vertical_relaxed").write_parquet(
        source / "runs.parquet"
    )
    pl.concat([steps, *extra_steps]).write_parquet(source / "steps.parquet")
    (source / "truth.private").mkdir()
    (source / "truth.private/assessment.json").write_text(
        json.dumps(
            {
                "expected_mechanisms": ["wafer_esc_contact"],
                "localization": {"wafer_esc_contact": {"prefixes": ["heater_"], "steps": [2]}},
            }
        )
    )
    prepare_evaluation(source, lab.run)
    return source


def test_frozen_model_uses_test_only_once_and_never_adds_test_to_discovery(tmp_path):
    lab = fixture_case(tmp_path / "case")
    private_source(lab, tmp_path)

    async def run():
        study = lab.create(
            FormulaPlan(title="frozen holdout", labels=["good", "bad"], models=["logistic"])
        )
        register(lab, study, "product", PRODUCT)
        await lab.evaluate(study["id"], ["product@1"])
        selected = await lab.freeze(study["id"])
        payload = ResearchReply(
            summary="frozen",
            formula_comparison={
                "study_id": study["id"],
                "status": "selected",
                "selection_id": selected["selection_id"],
            },
        ).model_dump()
        payload["review_status"] = "passed"
        (lab.run / "latest_reply.json").write_text(json.dumps(payload))
        result = await finalize_evaluation(lab.run)
        assert result["prediction"]["status"] == "scored"
        assert (
            result["prediction"]["test_samples"] == 2
            and result["prediction"]["independent_lots"] == 2
        )
        assert result["prediction"]["balanced_accuracy"] == 1
        assert result == await finalize_evaluation(lab.run)
        payload["summary"] = "adapted after test"
        (lab.run / "latest_reply.json").write_text(json.dumps(payload))
        with pytest.raises(ValueError, match="already frozen"):
            await finalize_evaluation(lab.run)

    asyncio.run(run())
    assert "test" not in set(pl.read_parquet(lab.run / "input/runs.parquet")["split"])
    assert not (lab.run / "workspace/test-predictions.parquet").exists()


def test_truth_score_distinguishes_missing_mechanism_and_false_positive_on_control():
    report = {
        "hypotheses": [
            {
                "mechanism_id": "pump_restriction",
                "status": "supported",
                "sensor_steps": [{"sensor": "chamber_pressure_mtorr", "step": 4}],
            }
        ]
    }
    positive = truth_score(
        report, {"expected_mechanisms": ["wafer_esc_contact"], "localization": {}}
    )
    assert positive["supported_mechanisms"]["f1"] == 0
    assert positive["supported_mechanisms"]["missed_mechanisms"] == ["wafer_esc_contact"]
    negative = truth_score(report, {"expected_mechanisms": [], "localization": {}})
    assert negative["supported_mechanisms"]["false_positive_mechanisms"] == ["pump_restriction"]
    assert (
        truth_score({"hypotheses": []}, {"expected_mechanisms": []})["supported_mechanisms"]["f1"]
        == 1
    )


def test_surface_reference_keeps_residual_change_across_cohort_sizes(tmp_path):
    from dataclasses import asdict

    from factor_demo.etch_evaluation import hardware_truth
    from factor_demo.etch_recipe import Hardware

    private = tmp_path / "truth.private"
    private.mkdir()
    for size in [1, 3, 5, 30]:
        rows = []
        hardware = []
        for label, value in [("label1", 1.0), ("label3", 1.2)]:
            for i in range(size):
                identity = f"{label}-{i}"
                rows.append({"run_id": identity, "label": label, "is_control": False})
                hardware.append(
                    {
                        "run_id": identity,
                        "parameters": asdict(Hardware(wall_recombination_factor=value)),
                    }
                )
        (private / "hardware.json").write_text(json.dumps(hardware))
        assert hardware_truth(tmp_path, pl.DataFrame(rows))["expected_by_pair"][
            "label1|label3"
        ] == ["surface_condition"]
