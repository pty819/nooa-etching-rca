"""Deterministic scope and saved-table verification; never mines numbers from prose."""

import hashlib
import json
import math
from pathlib import Path

import polars as pl
from nooa.strategy_validation import InvariantError

from .live_events import public_artifact_path as artifact_path
from .research_models import ResearchReply


class EvidenceError(InvariantError):
    pass


class EvidenceVerifier:
    def __init__(self, run: Path, events=None):
        self.run = run.resolve()
        self.events = events

    def fail(self, message):
        if self.events:
            self.events.emit("validation.issue", message=message)
        raise EvidenceError(message)

    def scope(self):
        public = self.run / "input"
        if not (public / "runs.parquet").is_file():
            self.fail("Public runs.parquet is missing")
        runs = pl.read_parquet(public / "runs.parquet")
        if "split" not in runs.columns or set(runs["split"]) - {"train", "dev"}:
            self.fail("Only public train/dev runs are permitted")
        if "recipe_id" not in runs.columns or runs["recipe_id"].n_unique() != 1:
            self.fail("All discovery runs must share one fixed recipe")
        config = json.loads((self.run / "session.json").read_text())
        for name in ["recipe.json", "runs.parquet"]:
            expected = config.get("public_sha256", {}).get(name)
            if expected and hashlib.sha256((public / name).read_bytes()).hexdigest() != expected:
                self.fail(f"Public scope changed: {name}")
        return runs

    def verify(self, report: ResearchReply):
        runs = self.scope()
        public_ids = set(runs["run_id"])
        recipe = str(runs["recipe_id"][0])
        paths = set(report.artifacts)
        for h in report.hypotheses:
            paths.update(h.evidence_files)
        for path in paths:
            try:
                artifact_path(self.run, path)
            except ValueError:
                self.fail(f"Evidence artifact is missing or outside artifacts/: {path}")
        ids = [e.id for e in report.evidence]
        if len(ids) != len(set(ids)):
            self.fail("Evidence IDs must be unique")
        checks = []
        for e in report.evidence:
            if not e.unit.strip():
                self.fail(f"{e.id}: specify units (use 1 for dimensionless or runs for counts)")
            try:
                path = artifact_path(self.run, e.artifact)
            except ValueError:
                self.fail(f"{e.id}: artifact must exist under artifacts/")
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if e.sha256 and e.sha256 != sha:
                self.fail(f"{e.id}: evidence file hash changed")
            if set(e.run_ids) - public_ids:
                self.fail(f"{e.id}: contains a run outside the public discovery set")
            if e.recipe_id and e.recipe_id != recipe:
                self.fail(f"{e.id}: recipe mismatch")
            try:
                if path.suffix.lower() == ".csv":
                    frame = pl.read_csv(path)
                elif path.suffix.lower() == ".parquet":
                    frame = pl.read_parquet(path)
                else:
                    self.fail(f"{e.id}: numerical evidence must be CSV or Parquet")
                if "run_id" in frame.columns and set(frame["run_id"].drop_nulls()) - public_ids:
                    self.fail(f"{e.id}: table contains withheld/unknown runs")
                if "recipe_id" in frame.columns and set(frame["recipe_id"].drop_nulls()) - {recipe}:
                    self.fail(f"{e.id}: table includes another recipe")
                for key, value in e.where.items():
                    frame = frame.filter(pl.col(key) == value)
                if frame.is_empty():
                    self.fail(f"{e.id}: row selector matches no evidence")
                if e.statistic == "count":
                    actual = frame.height
                else:
                    series = frame[e.column]
                    if e.statistic == "value":
                        if len(series) != 1:
                            self.fail(f"{e.id}: value requires exactly one selected row")
                        actual = series[0]
                    else:
                        actual = getattr(series, e.statistic)()
            except EvidenceError:
                raise
            except Exception as error:  # noqa: BLE001 - return a model-correctable evidence error
                self.fail(f"{e.id}: cannot evaluate table selection ({type(error).__name__})")
            if (
                isinstance(actual, (int, float))
                and not isinstance(actual, bool)
                and isinstance(e.value, (int, float))
                and not isinstance(e.value, bool)
            ):
                match = (
                    math.isfinite(actual)
                    and math.isfinite(e.value)
                    and math.isclose(actual, e.value, rel_tol=1e-6, abs_tol=1e-8)
                )
            else:
                match = type(actual) == type(e.value) and actual == e.value
            if not match:
                self.fail(f"{e.id}: claimed {e.value!r}, saved-table calculation is {actual!r}")
            checks.append(
                {
                    "id": e.id,
                    "claim": e.claim,
                    "artifact": e.artifact,
                    "column": e.column,
                    "where": e.where,
                    "statistic": e.statistic,
                    "actual": actual,
                    "unit": e.unit,
                    "sha256": sha,
                }
            )
        for h in report.hypotheses:
            if set(h.evidence_ids) - set(ids):
                self.fail(f"{h.id}: unknown evidence_ids")
            if h.status in {"supported", "weak"} and not h.evidence_ids:
                self.fail(f"{h.id}: supporting claims need structured evidence_ids")
        if self.events:
            self.events.emit("validation.passed", checks=len(checks), files=len(paths))
        return {
            "recipe_id": recipe,
            "public_runs": len(public_ids),
            "checks": checks,
            "hypotheses": len(report.hypotheses),
            "limits": "Checks verify public scope and saved-table references, not the correctness of arbitrary transformations, sensor calibration or causal truth.",
        }


def research_precondition(agent, call):
    workspace = getattr(agent, "_workspace", None)
    if workspace is not None and call.method_name in {"research", "revise"}:
        EvidenceVerifier(workspace.run, workspace.events).scope()


def research_postcondition(agent, result, call):
    workspace = getattr(agent, "_workspace", None)
    if (
        workspace is not None
        and isinstance(result, ResearchReply)
        and call.method_name in {"research", "revise"}
    ):
        manager = getattr(agent, "_manager", None)
        if manager is not None and any(
            row["mission"] == manager.mission
            and row["state"] not in {"completed", "cancelled", "failed", "interrupted"}
            for row in manager.store.list()
            if row["parent_id"] == manager.identity
        ):
            raise EvidenceError(
                "Research branches are still active; wait for their results or cancel them explicitly before the final checkpoint"
            )
        agent._last_verification = EvidenceVerifier(workspace.run, workspace.events).verify(result)


def snapshot_ignore(directory, names):
    return [
        name for name in names if name == "__pycache__" or (Path(directory) / name).is_symlink()
    ]
