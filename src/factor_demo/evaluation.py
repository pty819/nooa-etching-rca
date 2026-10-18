"""Host-only, one-shot evaluation after freezing a report and selected formula.

Held-out data/truth never enter the discovery registry or agent tools. Models and
operator states are deserialized only in guarded scientific children.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import uuid
from pathlib import Path

import numpy as np
import polars as pl
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score

from .dataset_adapter import detect_schema, index_frame, safe_path
from .datasets import canonical, file_hash
from .feature_execution import FeatureExecutor
from .formula_lab import FormulaLab
from .io_utils import write_json
from .research_models import ResearchReply
from .research_workspace import PythonWorkspace

MECHANISMS = {
    "backside_he_leak": ("edge_leak_sccm_per_torr", 0.1, ["he_edge_"], [4, 8]),
    "wafer_esc_contact": ("edge_contact_factor", 0.1, ["heater_z", "he_edge_"], [4, 8]),
    "pump_restriction": ("pump_conductance_factor", 0.05, ["chamber_pressure", "pcv_"], [4, 8]),
    "surface_condition": ("wall_recombination_factor", 0.2, ["oes_", "rf_source_"], [4, 8]),
    "edge_ring": ("edge_ring_recession_mm", 0.1, ["rf_bias_", "oes_"], [4, 8]),
}


def _truth(source, metadata):
    explicit = source / "truth.private/assessment.json"
    if explicit.exists():
        return json.loads(explicit.read_text())
    hardware = json.loads((source / "truth.private/hardware.json").read_text())
    targets = {r["run_id"]: r for r in metadata.filter(~pl.col("is_control")).iter_rows(named=True)}
    grouped = {}
    for row in hardware:
        if row["run_id"] in targets:
            grouped.setdefault(targets[row["run_id"]]["label"], []).append(row["parameters"])
    labels = sorted(grouped)
    pairs = {}
    for i, a in enumerate(labels):
        for b in labels[i + 1 :]:
            pairs[a + "|" + b] = [
                name
                for name, (column, tolerance, _, _) in MECHANISMS.items()
                if abs(
                    np.mean([r[column] for r in grouped[a]])
                    - np.mean([r[column] for r in grouped[b]])
                )
                > tolerance
            ]
    return {
        "expected_by_pair": pairs,
        "localization": {
            name: {"prefixes": value[2], "steps": value[3]} for name, value in MECHANISMS.items()
        },
    }


def prepare_evaluation(source: Path, run: Path):
    """Copy a private test snapshot once; do not export it to DATASET."""
    source = source.resolve()
    base = run / ".evaluation"
    if (base / "manifest.json").exists():
        return
    if not (source / "truth.private").is_dir():
        return
    if not any(
        (source / "truth.private" / name).exists() for name in ["assessment.json", "hardware.json"]
    ):
        return
    schema = detect_schema(source)
    if not schema.split_column or not schema.label_column or not schema.asset_column:
        return
    metadata = index_frame(source, schema)
    if schema.id_column != "run_id":
        return
    targets = metadata.filter(
        (pl.col(schema.split_column) == "test")
        & (~pl.col(schema.control_column) if schema.control_column else pl.lit(True))
    )
    if targets.is_empty():
        return
    base.mkdir(parents=True, exist_ok=True)
    private = base / "input"
    private.mkdir()
    targets.write_parquet(private / "runs.parquet")
    schema = schema.model_copy(update={"index_file": "runs.parquet"})
    for name in targets[schema.asset_column]:
        target = private / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe_path(source, name), target)
    if schema.steps_file:
        step = pl.read_parquet(safe_path(source, schema.steps_file))
        step.filter(pl.col("run_id").is_in(targets["run_id"].implode())).write_parquet(
            private / schema.steps_file
        )
    write_json(private / "dataset.json", {"schema": schema.model_dump()})
    write_json(base / "truth.json", _truth(source, metadata))
    write_json(
        base / "manifest.json",
        {
            "files": {
                str(p.relative_to(base)): file_hash(p)
                for p in base.rglob("*")
                if p.is_file() and p.name != "manifest.json"
            },
            "test_ids": targets["run_id"].to_list(),
            "source": "private held-out snapshot",
        },
    )


def _validate_input(base):
    manifest = json.loads((base / "manifest.json").read_text())
    for name, digest in manifest["files"].items():
        if file_hash(safe_path(base, name)) != digest:
            raise ValueError("Private evaluation data changed")
    return manifest


def _f1(expected, predicted):
    intersection = len(expected & predicted)
    precision = intersection / len(predicted) if predicted else 1.0 if not expected else 0.0
    recall = intersection / len(expected) if expected else 1.0
    return {
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "false_positive_mechanisms": sorted(predicted - expected),
        "missed_mechanisms": sorted(expected - predicted),
    }


def truth_score(report, truth, labels=None):
    pairs = truth.get("expected_by_pair", {})
    key = "|".join(sorted(labels or []))
    expected = (
        set(pairs[key])
        if labels and key in pairs
        else set().union(*(set(v) for v in pairs.values()))
        if pairs
        else set(truth.get("expected_mechanisms", []))
    )
    candidates = {
        h["mechanism_id"]
        for h in report.get("hypotheses", [])
        if h.get("mechanism_id") and h["status"] != "rejected"
    }
    supported = {
        h["mechanism_id"]
        for h in report.get("hypotheses", [])
        if h.get("mechanism_id") and h["status"] == "supported"
    }
    rejected = {
        h["mechanism_id"]
        for h in report.get("hypotheses", [])
        if h.get("mechanism_id") and h["status"] == "rejected"
    }
    rows = []
    for h in report.get("hypotheses", []):
        if h["status"] == "rejected":
            continue
        target = truth.get("localization", {}).get(h.get("mechanism_id"), {})
        for item in h.get("sensor_steps", []):
            sensor = str(item.get("sensor", ""))
            step = item.get("step")
            rows.append(
                bool(
                    h.get("mechanism_id") in expected
                    and step in target.get("steps", [])
                    and any(sensor.startswith(p) for p in target.get("prefixes", []))
                )
            )
    return {
        "candidate_mechanisms": _f1(expected, candidates),
        "supported_mechanisms": _f1(expected, supported),
        "wrong_exclusions": sorted(rejected & expected),
        "localization_precision": sum(rows) / len(rows) if rows else None,
        "reported_sensor_steps": len(rows),
        "expected_mechanisms": sorted(expected),
        "scope": "synthetic mechanism/localization reference; not production causal certification",
    }


async def _predict(run, base, report):
    handoff = report.get("formula_comparison")
    if not handoff or handoff["status"] != "selected":
        return {
            "status": "not_requested",
            "reason": "No frozen formal model selection; mechanism scoring remains available",
        }
    lab = FormulaLab(run)
    study = lab.get(handoff["study_id"])
    lab.verify_input(study)
    if handoff["selection_id"] != study["selection_id"]:
        raise ValueError("Selection changed before finalization")
    selection = next(s for s in study["selections"] if s["id"] == study["selection_id"])
    winner = next(t for t in study["trials"] if t["id"] == selection["winner"])
    result = lab.verify_result(study, winner)
    private = base / "input"
    schema = detect_schema(private)
    rows = index_frame(private, schema).filter(
        pl.col(schema.label_column).is_in(study["plan"]["labels"])
    )
    if set(rows[schema.label_column]) != set(study["plan"]["labels"]):
        raise ValueError("Test set lacks a selected label")
    if set(rows["run_id"]) & set(study["plan"]["train"] + study["plan"]["dev"]):
        raise ValueError("Hold-out overlaps discovery")
    extractor = FeatureExecutor(lab)
    matrix = rows.select("run_id")
    for ref in winner["formulas"]:
        spec = next(f for f in study["formulas"] if f["ref"] == ref)
        state = (
            result / "operators" / ref / "state.joblib" if spec.get("kind") == "learned" else None
        )
        values = await asyncio.gather(
            *(
                extractor.value(study, spec, private, schema, row, state)
                for row in rows.iter_rows(named=True)
            )
        )
        matrix = matrix.with_columns(pl.Series(ref, values, dtype=pl.Float64))
    job = base / "predictions" / uuid.uuid4().hex
    (job / "workspace").mkdir(parents=True)
    (job / "input").mkdir()
    matrix.write_parquet(job / "input/matrix.parquet")
    shutil.copyfile(result / "model.joblib", job / "input/model.joblib")
    worker = PythonWorkspace(job)
    try:
        code = "import joblib,polars as pl\nm=pl.read_parquet(DATASET/'matrix.parquet')\nmodel=joblib.load(DATASET/'model.joblib')\np=model.predict(m.drop('run_id').to_numpy())\npl.DataFrame({'run_id':m['run_id'],'prediction':p}).write_parquet('predictions.parquet')"
        execution = await worker.execute(code, 300)
        if not execution["ok"]:
            raise ValueError("Held-out prediction failed: " + execution["output"][-1000:])
    finally:
        await worker.close()
    pred = pl.read_parquet(job / "workspace/predictions.parquet").join(
        rows.select("run_id", schema.label_column, schema.group_column), on="run_id", validate="1:1"
    )
    if pred.height != rows.height:
        raise ValueError("Incomplete held-out predictions")
    actual = pred[schema.label_column].to_list()
    prediction = pred["prediction"].to_list()
    pred.write_parquet(base / "test-predictions.parquet")
    return {
        "status": "scored",
        "accuracy": accuracy_score(actual, prediction),
        "balanced_accuracy": balanced_accuracy_score(actual, prediction),
        "macro_f1": f1_score(actual, prediction, average="macro"),
        "test_samples": pred.height,
        "independent_lots": pred[schema.group_column].n_unique(),
        "labels": study["plan"]["labels"],
        "selection_id": study["selection_id"],
        "training_ids_unchanged": True,
    }


async def _finalize_evaluation(run: Path):
    """Human/host endpoint only; no tool exposes this to the research agent."""
    base = run / ".evaluation"
    if not (base / "manifest.json").exists():
        raise ValueError("No private held-out/truth source was supplied for this case")
    payload = json.loads((run / "latest_reply.json").read_text())
    # Host publication metadata does not alter the model's frozen definition.
    report = ResearchReply.model_validate(
        {k: v for k, v in payload.items() if k in ResearchReply.model_fields}
    ).model_dump()
    fingerprint = hashlib.sha256(canonical(report)).hexdigest()
    frozen = base / "frozen.json"
    if frozen.exists():
        prior = json.loads(frozen.read_text())
        if prior["sha256"] != fingerprint:
            raise ValueError("Case was already frozen; do not adapt to its held-out score")
    else:
        write_json(frozen, {"sha256": fingerprint, "report": report})
    if (base / "result.json").exists():
        return json.loads((base / "result.json").read_text())
    _validate_input(base)
    prediction = await _predict(run, base, report)
    truth = json.loads((base / "truth.json").read_text())
    result = {
        "report_sha256": fingerprint,
        "prediction": prediction,
        "mechanisms": truth_score(report, truth, prediction.get("labels")),
        "review_status": payload.get("review_status"),
        "blind_evaluation": True,
        "warnings": [
            "Dev participates in selection; only this frozen test evaluation is held out.",
            "Synthetic truth scores do not certify production root causes.",
        ],
    }
    write_json(base / "result.json", result)
    return result


async def finalize_evaluation(run: Path):
    from .case_lease import case_lease

    with case_lease(run):
        return await _evaluation_lock(run)


async def _evaluation_lock(run: Path):
    import fcntl

    base = run / ".evaluation"
    if not (base / "manifest.json").exists():
        raise ValueError("No private held-out/truth source was supplied for this case")
    with (base / "finalize.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Evaluation is already running; reuse its saved result") from None
        try:
            return await _finalize_evaluation(run)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def evaluation_state(run):
    base = run / ".evaluation"
    if (base / "result.json").exists():
        return {"status": "completed", "result": json.loads((base / "result.json").read_text())}
    return {
        "status": "available" if (base / "manifest.json").exists() else "unavailable",
        "frozen": (base / "frozen.json").exists(),
    }
