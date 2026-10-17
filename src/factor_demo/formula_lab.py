"""Host-owned formula registry, grouped validation, evaluation and replay gate.

Scientific exploration remains unrestricted. Only comparisons entered in this lab
receive the reproducible selection contract; scores supplied by an agent are never
accepted. SQLite serializes short state changes across branch hosts.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

import numpy as np
import polars as pl
import psutil
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score
from sklearn.model_selection import StratifiedGroupKFold

from .formula_models import FormulaHandoff, FormulaPlan, FormulaSpec
from .live_events import artifact_path
from .research_workspace import PythonWorkspace

MODEL_PARAMS = {
    "logistic": {"C": 1.0, "max_iter": 2000, "class_weight": "balanced"},
    "random_forest": {
        "n_estimators": 200,
        "min_samples_leaf": 2,
        "class_weight": "balanced",
        "n_jobs": 2,
    },
    "extra_trees": {
        "n_estimators": 200,
        "min_samples_leaf": 2,
        "class_weight": "balanced",
        "n_jobs": 2,
    },
    "hist_gradient_boosting": {"max_iter": 100, "max_leaf_nodes": 15, "l2_regularization": 1.0},
}
LIMITS = (
    "仅在本研究已登记且未排除的候选、固定模型和开发集上选优；开发集参与多轮选择，"
    "不是独立最终测试。lot交叉验证不能消除时段混杂；分类分数不能证明物理根因。"
    "任意Python公式的文字描述与代码语义仍需专家核对，隔离不能防止人为硬编码标签。"
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def environment():
    return {
        "python": platform.python_version(),
        **{n: importlib.metadata.version(n) for n in ["numpy", "polars", "scipy", "scikit-learn"]},
        "runner_sha256": digest(Path(__file__).with_name("formula_runner.py")),
    }


class FormulaLab:
    def __init__(self, run: Path, events=None):
        self.run = run.resolve()
        self.events = events
        self.root = events.root if events else self.run
        self.owner = events.agent_id if events else "main"
        self.base = self.root / ".formula-lab"

    @contextmanager
    def transaction(self):
        self.base.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.base / "registry.sqlite", timeout=30)
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS studies(id TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, identity, db=None):
        if not re.fullmatch(r"study-[a-f0-9]{12}", identity):
            raise ValueError("Invalid study id")
        if db is None:
            with self.transaction() as connection:
                return self.get(identity, connection)
        row = db.execute("SELECT body FROM studies WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ValueError("Unknown formula study")
        study = json.loads(row[0])
        study.setdefault("replays", [])
        return study

    def save(self, db, study):
        db.execute(
            "INSERT OR REPLACE INTO studies VALUES(?,?)",
            (study["id"], json.dumps(study, allow_nan=False)),
        )

    def emit(self, kind, study_id, **data):
        if self.events:
            self.events.emit("formula." + kind, study_id=study_id, **data)

    def catalog(self):
        if not (self.base / "registry.sqlite").exists():
            return []
        with self.transaction() as db:
            studies = [
                json.loads(row[0]) for row in db.execute("SELECT body FROM studies ORDER BY id")
            ]
        return [self.public(s) for s in studies]

    def public(self, study):
        study.setdefault("replays", [])
        return {
            k: study[k]
            for k in [
                "id",
                "owner",
                "created",
                "plan",
                "formulas",
                "sets",
                "trials",
                "selections",
                "replays",
                "selection_id",
                "revision",
                "limits",
            ]
        }

    def bound(self):
        return [s for s in self.catalog() if self.owner in s["plan"]["participants"]]

    def context_summary(self):
        return json.dumps(
            {
                "formula_studies": [
                    {
                        "id": s["id"],
                        "title": s["plan"]["title"],
                        "labels": s["plan"]["labels"],
                        "versions": [f["ref"] for f in s["formulas"]],
                        "selection_id": s["selection_id"],
                        "incomplete_trials": [
                            t["id"] for t in s["trials"] if t["status"] != "succeeded"
                        ],
                    }
                    for s in self.catalog()
                ],
                "contract": "需要公式选优时先create_formula_study；register_formula快照任意Python；evaluate_formula_set比较单公式或组合；全部完成后freeze_formula_selection。交接必须含formula_comparison，失败则incomplete+reason。",
            },
            ensure_ascii=False,
        )

    def verify_input(self, study=None):
        public = self.root / "input"
        expected = json.loads((self.root / "session.json").read_text())["public_sha256"]
        actual = {str(p.relative_to(public)): digest(p) for p in public.rglob("*") if p.is_file()}
        if expected != actual:
            raise ValueError(
                "Public input fingerprint changed; restore data before comparing formulas"
            )
        fingerprint = hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest()
        if study and (
            study["plan"]["input_sha256"] != fingerprint
            or study["plan"]["environment"] != environment()
        ):
            raise ValueError("Frozen data/software protocol changed; create a new study")
        return fingerprint

    def create(self, spec: FormulaPlan):
        fingerprint = self.verify_input()
        if len(set(spec.labels)) != 2 or len(set(spec.models)) != len(spec.models):
            raise ValueError("Choose two different labels and unique models")
        runs = (
            pl.read_parquet(self.root / "input/runs.parquet")
            .filter(~pl.col("is_control") & pl.col("label").is_in(spec.labels))
            .sort("run_id")
        )
        if set(runs["label"]) != set(spec.labels) or set(runs["split"]) - {"train", "dev"}:
            raise ValueError("Need both labels in public train/dev only")
        if runs["recipe_id"].n_unique() != 1 or runs["run_id"].n_unique() != runs.height:
            raise ValueError("Need unique runs from one fixed recipe")
        train, dev = (runs.filter(pl.col("split") == split) for split in ["train", "dev"])
        if set(train["batch_id"]) & set(dev["batch_id"]):
            raise ValueError("A lot crosses train/dev boundaries")
        if any(set(frame["label"]) != set(spec.labels) for frame in [train, dev]):
            raise ValueError("Both labels must occur in train and dev")
        if train["batch_id"].n_unique() < spec.folds:
            raise ValueError("Too few independent training lots for requested folds")
        folds = []
        splitter = StratifiedGroupKFold(n_splits=spec.folds, shuffle=True, random_state=spec.seed)
        for i, (a, b) in enumerate(
            splitter.split(
                np.zeros((train.height, 1)), train["label"].to_numpy(), train["batch_id"].to_numpy()
            )
        ):
            left, right = train[a.tolist()], train[b.tolist()]
            if any(set(f["label"]) != set(spec.labels) for f in [left, right]):
                raise ValueError("A grouped fold lacks a label; use fewer folds or more lots")
            assert not set(left["batch_id"]) & set(right["batch_id"])
            folds.append(
                {
                    "id": f"cv-{i}",
                    "train": left["run_id"].to_list(),
                    "valid": right["run_id"].to_list(),
                }
            )
        identity = "study-" + uuid.uuid4().hex[:12]
        study = {
            "id": identity,
            "owner": self.owner,
            "created": time.time(),
            "revision": 0,
            "plan": {
                **spec.model_dump(),
                "input_sha256": fingerprint,
                "environment": environment(),
                "recipe_id": runs["recipe_id"][0],
                "group": "batch_id",
                "train": train["run_id"].to_list(),
                "dev": dev["run_id"].to_list(),
                "fold_manifest": folds,
                "model_params": {m: MODEL_PARAMS[m] for m in spec.models},
                "participants": [self.owner],
                "preprocessing": "fold-fit median imputation; logistic only: fold-fit standard scaling",
                "ranking": "dev metric descending, grouped CV mean descending, CV std ascending, fewer formulas, stable result id",
            },
            "formulas": [],
            "sets": [],
            "trials": [],
            "selections": [],
            "replays": [],
            "selection_id": None,
            "limits": LIMITS,
        }
        with self.transaction() as db:
            self.save(db, study)
        self.export(study)
        self.emit("study.created", identity, title=spec.title)
        return self.public(study)

    def register(self, identity: str, spec: FormulaSpec):
        study = self.get(identity)
        self.verify_input(study)
        source = artifact_path(self.run, spec.source).read_text()
        tree = ast.parse(source)
        if not any(isinstance(n, ast.FunctionDef) and n.name == "extract" for n in tree.body):
            raise ValueError("Source must define extract(trace, steps, params)")
        if len(set(spec.sensors)) != len(spec.sensors) or len(set(spec.steps)) != len(spec.steps):
            raise ValueError("Sensors and steps must be unique")
        first = pl.read_parquet(self.root / "input/runs.parquet").row(0, named=True)
        trace = pl.read_parquet(self.root / "input" / first["trace_path"], n_rows=0)
        if set(spec.sensors) - (set(trace.columns) - {"time_s", "step_id", "run_id"}):
            raise ValueError("Unknown sensor in raw trace")
        steps = pl.read_parquet(self.root / "input/steps.parquet")
        if set(spec.steps) - set(steps["step_id"]):
            raise ValueError("Unknown step")
        json.dumps(spec.params, allow_nan=False)
        with self.transaction() as db:
            study = self.get(identity, db)
            version = 1 + max(
                (f["version"] for f in study["formulas"] if f["name"] == spec.name), default=0
            )
            if version > 1 and not spec.change_reason.strip():
                raise ValueError(
                    "Revised formula requires change_reason; previous code/results are retained"
                )
            ref = f"{spec.name}@{version}"
            folder = self.base / identity / "formulas" / ref
            folder.mkdir(parents=True, exist_ok=False)
            (folder / "source.py").write_text(source)
            row = {
                **spec.model_dump(),
                "ref": ref,
                "version": version,
                "owner": self.owner,
                "sha256": digest(folder / "source.py"),
            }
            dump(folder / "spec.json", row)
            row["spec_sha256"] = digest(folder / "spec.json")
            study["formulas"].append(row)
            study["sets"].append(
                {"id": "set-" + uuid.uuid4().hex[:10], "formulas": [ref], "excluded": None}
            )
            study["revision"] += 1
            study["selection_id"] = None
            if self.owner not in study["plan"]["participants"]:
                study["plan"]["participants"].append(self.owner)
            self.save(db, study)
        self.export(study)
        self.emit("formula.registered", identity, ref=ref)
        return row

    def feature_set(self, identity, refs):
        refs = sorted(set(refs))
        with self.transaction() as db:
            study = self.get(identity, db)
            if not refs or set(refs) - {f["ref"] for f in study["formulas"]}:
                raise ValueError("Choose registered formula versions")
            existing = next((s for s in study["sets"] if s["formulas"] == refs), None)
            if existing:
                return existing
            item = {"id": "set-" + uuid.uuid4().hex[:10], "formulas": refs, "excluded": None}
            study["sets"].append(item)
            study["revision"] += 1
            study["selection_id"] = None
            self.save(db, study)
        self.emit("set.created", identity, set_id=item["id"])
        return item

    def retire(self, identity, set_id, reason):
        if not reason.strip():
            raise ValueError("Excluding a comparison requires an explicit reason")
        with self.transaction() as db:
            study = self.get(identity, db)
            item = next(s for s in study["sets"] if s["id"] == set_id)
            item["excluded"] = {"reason": reason, "owner": self.owner, "time": time.time()}
            study["selection_id"] = None
            study["revision"] += 1
            self.save(db, study)
        self.export(study)
        self.emit("set.excluded", identity, set_id=set_id, reason=reason)
        return item

    async def worker(self, job, code):
        worker = PythonWorkspace(job, read_scope_root=self.root)
        try:
            result = await worker.execute(code, 300)
            if not result["ok"]:
                raise ValueError("Scientific job failed: " + result["output"][-3000:])
        finally:
            await worker.close()

    def new_job(self, identity, kind):
        job = self.base / identity / "jobs" / (kind + "-" + uuid.uuid4().hex)
        (job / "workspace").mkdir(parents=True)
        (job / "input").mkdir()
        shutil.copyfile(Path(__file__).with_name("formula_runner.py"), job / "workspace/runner.py")
        return job

    async def matrix(self, study, item):
        """Only declared, label-free raw columns/steps enter the formula process."""
        runs = pl.read_parquet(self.root / "input/runs.parquet")
        ids = study["plan"]["train"] + study["plan"]["dev"]
        runs = runs.filter(pl.col("run_id").is_in(ids)).sort("run_id")
        steps = pl.read_parquet(self.root / "input/steps.parquet")
        matrix = runs.select("run_id", "label", "batch_id", "split")
        for ref in item["formulas"]:
            spec = next(f for f in study["formulas"] if f["ref"] == ref)
            canonical = self.base / study["id"] / "formulas" / ref
            if (
                digest(canonical / "source.py") != spec["sha256"]
                or digest(canonical / "spec.json") != spec["spec_sha256"]
            ):
                raise ValueError("Canonical formula snapshot changed")
            job = self.new_job(study["id"], "extract")
            config = {"params": spec["params"], "runs": []}
            tokens = {}
            for row in runs.iter_rows(named=True):
                token = uuid.uuid4().hex
                tokens[token] = row["run_id"]
                frame = pl.read_parquet(self.root / "input" / row["trace_path"]).filter(
                    pl.col("step_id").is_in(spec["steps"])
                )
                frame.select("time_s", "step_id", *spec["sensors"]).write_parquet(
                    job / "input" / f"{token}.parquet"
                )
                timing = steps.filter(
                    (pl.col("run_id") == row["run_id"]) & pl.col("step_id").is_in(spec["steps"])
                )
                timing.select("step_id", "start_s", "end_s").write_parquet(
                    job / "input" / f"{token}-steps.parquet"
                )
                config["runs"].append(
                    {"id": token, "trace": f"{token}.parquet", "steps": f"{token}-steps.parquet"}
                )
            shutil.copyfile(canonical / "source.py", job / "input/formula.py")
            dump(job / "input/job.json", config)
            try:
                await self.worker(job, "import runpy\nrunpy.run_path('runner.py')['extract']()")
                features = pl.read_parquet(job / "workspace/features.parquet")
                if (
                    features.columns != ["run_id", "value"]
                    or features.height != runs.height
                    or set(features["run_id"]) != set(tokens)
                ):
                    raise ValueError("Extraction output does not cover the registered runs")
                features = features.with_columns(pl.col("run_id").replace_strict(tokens)).rename(
                    {"value": ref}
                )
                matrix = matrix.join(features, on="run_id", validate="1:1")
                if matrix.filter(pl.col("split") == "train")[ref].drop_nulls().is_empty():
                    raise ValueError("Formula has no observed training values")
                if not np.isfinite(matrix[ref].drop_nulls().to_numpy()).all():
                    raise ValueError("Formula produced nonfinite values")
            finally:
                # Keep code, journal and outputs, but do not accumulate copied traces.
                shutil.rmtree(job / "input")
        return matrix.sort("run_id")

    async def model_result(self, study, item, matrix, model):
        plan = study["plan"]
        job = self.new_job(study["id"], "evaluate")
        matrix.write_parquet(job / "input/matrix.parquet")
        dump(
            job / "input/job.json",
            {
                "features": item["formulas"],
                "folds": plan["fold_manifest"],
                "train": plan["train"],
                "dev": plan["dev"],
                "model": model,
                "seed": plan["seed"],
                "params": plan["model_params"][model],
            },
        )
        await self.worker(job, "import runpy\nrunpy.run_path('runner.py')['evaluate']()")
        predictions = pl.read_parquet(job / "workspace/predictions.parquet")
        scores = self.score(study, predictions)
        return scores, job

    def score(self, study, predictions):
        plan = study["plan"]
        runs = pl.read_parquet(self.root / "input/runs.parquet").select("run_id", "label")
        metric = {
            "balanced_accuracy": balanced_accuracy_score,
            "accuracy": accuracy_score,
            "f1_macro": lambda a, b: f1_score(a, b, average="macro", zero_division=0),
        }[plan["metric"]]
        values = {}
        expected = [*plan["fold_manifest"], {"id": "dev", "valid": plan["dev"]}]
        if set(predictions["fold"]) != {f["id"] for f in expected}:
            raise ValueError("Prediction fold coverage differs from frozen plan")
        for fold in expected:
            selected = predictions.filter(pl.col("fold") == fold["id"])
            if selected.height != len(fold["valid"]) or set(selected["run_id"]) != set(
                fold["valid"]
            ):
                raise ValueError("Missing/duplicate prediction runs")
            if set(selected["prediction"]) - set(plan["labels"]):
                raise ValueError("Unknown predicted label")
            selected = selected.join(runs, on="run_id", validate="1:1")
            values[fold["id"]] = float(
                metric(selected["label"].to_numpy(), selected["prediction"].to_numpy())
            )
        cv = [values[f["id"]] for f in plan["fold_manifest"]]
        return {
            "dev_score": values["dev"],
            "cv_mean": float(np.mean(cv)),
            "cv_std": float(np.std(cv)),
            "fold_scores": values,
        }

    def update_trial(self, identity, trial_id, **updates):
        with self.transaction() as db:
            study = self.get(identity, db)
            row = next(t for t in study["trials"] if t["id"] == trial_id)
            row.update(updates)
            study["selection_id"] = None
            self.save(db, study)
        self.emit("trial.updated", identity, trial_id=trial_id, status=row["status"])

    async def evaluate(self, identity, refs):
        item = self.feature_set(identity, refs)
        study = self.get(identity)
        self.verify_input(study)
        if item["excluded"]:
            raise ValueError(
                "This set was explicitly excluded; create a new formula version to reconsider"
            )
        # Claim the entire feature set before any await. Another process cannot duplicate it.
        with self.transaction() as db:
            study = self.get(identity, db)
            previous = [t for t in study["trials"] if t["set_id"] == item["id"]]
            for row in previous:
                if row["status"] == "running":
                    try:
                        process = psutil.Process(row["pid"])
                        if process.create_time() != row["process_created"]:
                            raise psutil.NoSuchProcess(row["pid"])
                    except psutil.NoSuchProcess:
                        row.update(
                            status="interrupted", error="Host process exited", finished=time.time()
                        )
                    else:
                        raise ValueError("Comparison is already running; read its saved state")
            completed = {t["model"] for t in previous if t["status"] == "succeeded"}
            pending = [m for m in study["plan"]["models"] if m not in completed]
            if not pending:
                return self.public(study)
            new = [
                {
                    "id": "trial-" + uuid.uuid4().hex[:12],
                    "set_id": item["id"],
                    "formulas": item["formulas"],
                    "model": m,
                    "status": "running",
                    "owner": self.owner,
                    "pid": os.getpid(),
                    "process_created": psutil.Process().create_time(),
                    "started": time.time(),
                }
                for m in pending
            ]
            study["trials"].extend(new)
            study["selection_id"] = None
            self.save(db, study)
        self.emit("evaluation.start", identity, set_id=item["id"])
        try:
            matrix = await self.matrix(study, item)
            for row in new:
                try:
                    scores, job = await self.model_result(study, item, matrix, row["model"])
                    folder = self.base / identity / "results" / row["id"]
                    folder.mkdir(parents=True)
                    matrix.write_parquet(folder / "matrix.parquet")
                    for filename in ["predictions.parquet", "model.joblib", "model_details.json"]:
                        shutil.copyfile(job / "workspace" / filename, folder / filename)
                    hashes = {p.name: digest(p) for p in folder.iterdir()}
                    self.update_trial(
                        identity,
                        row["id"],
                        status="succeeded",
                        finished=time.time(),
                        **scores,
                        hashes=hashes,
                        model_details=json.loads((folder / "model_details.json").read_text()),
                    )
                except Exception as error:  # noqa: BLE001 - persist scientific failures for retry
                    self.update_trial(
                        identity,
                        row["id"],
                        status="failed",
                        error=str(error)[-3500:],
                        finished=time.time(),
                    )
        except BaseException as error:
            for row in self.get(identity)["trials"]:
                if row["id"] in {r["id"] for r in new} and row["status"] == "running":
                    self.update_trial(
                        identity,
                        row["id"],
                        status="interrupted"
                        if isinstance(error, asyncio.CancelledError)
                        else "failed",
                        error=f"{type(error).__name__}: {str(error)[-3000:]}",
                        finished=time.time(),
                    )
            if isinstance(error, asyncio.CancelledError):
                raise
        result = self.get(identity)
        self.export(result)
        return self.public(result)

    def ranking(self, study):
        active = {s["id"] for s in study["sets"] if not s["excluded"]}
        rows = [t for t in study["trials"] if t["status"] == "succeeded" and t["set_id"] in active]
        return sorted(
            rows,
            key=lambda r: (
                -r["dev_score"],
                -r["cv_mean"],
                r["cv_std"],
                len(r["formulas"]),
                r["id"],
            ),
        )

    def complete(self, study):
        active = [s for s in study["sets"] if not s["excluded"]]
        if not active:
            raise ValueError("No active formula candidates")
        missing = [
            (s["id"], m)
            for s in active
            for m in study["plan"]["models"]
            if not any(
                t["set_id"] == s["id"] and t["model"] == m and t["status"] == "succeeded"
                for t in study["trials"]
            )
        ]
        if missing or any(t["status"] == "running" for t in study["trials"]):
            raise ValueError(
                f"Comparison incomplete: {missing}; finish or explicitly exclude sets with a reason"
            )

    def verify_result(self, study, row):
        folder = self.base / study["id"] / "results" / row["id"]
        for name, expected in row["hashes"].items():
            if digest(folder / name) != expected:
                raise ValueError("Canonical result artifact changed")
        if self.score(study, pl.read_parquet(folder / "predictions.parquet")) != {
            k: row[k] for k in ["dev_score", "cv_mean", "cv_std", "fold_scores"]
        }:
            raise ValueError("Saved prediction metrics disagree")
        return folder

    async def freeze(self, identity):
        attempt = {
            "id": "replay-" + uuid.uuid4().hex[:12],
            "owner": self.owner,
            "started": time.time(),
        }
        try:
            result = await self._freeze(identity)
            attempt.update(status="passed", selection_id=result["selection_id"])
            return result
        except BaseException as error:
            attempt.update(
                status="interrupted" if isinstance(error, asyncio.CancelledError) else "failed",
                error=f"{type(error).__name__}: {str(error)[-3000:]}",
            )
            raise
        finally:
            attempt["finished"] = time.time()
            with self.transaction() as db:
                current = self.get(identity, db)
                current["replays"].append(attempt)
                self.save(db, current)
            self.export(current)
            self.emit("replay.finished", identity, status=attempt["status"])

    async def _freeze(self, identity):
        study = self.get(identity)
        self.verify_input(study)
        self.complete(study)
        ranked = self.ranking(study)
        for row in ranked:
            self.verify_result(study, row)
        best = ranked[0]
        item = next(s for s in study["sets"] if s["id"] == best["set_id"])
        self.emit("replay.start", identity, trial_id=best["id"])
        matrix = await self.matrix(study, item)
        saved = pl.read_parquet(self.base / identity / "results" / best["id"] / "matrix.parquet")
        if matrix.columns != saved.columns or not matrix.equals(saved):
            raise ValueError("Best formula replay changed feature values; selection refused")
        scores, job = await self.model_result(study, item, matrix, best["model"])
        old_predictions = pl.read_parquet(
            self.base / identity / "results" / best["id"] / "predictions.parquet"
        )
        predictions = pl.read_parquet(job / "workspace/predictions.parquet")
        if not predictions.equals(old_predictions) or any(scores[k] != best[k] for k in scores):
            raise ValueError("Best model replay changed predictions/scores; selection refused")
        selection = {
            "id": "selection-" + uuid.uuid4().hex[:12],
            "revision": study["revision"],
            "winner": best["id"],
            "ranking": [r["id"] for r in ranked],
            "replay": {
                "status": "passed",
                "time": time.time(),
                "features_equal": True,
                "predictions_equal": True,
            },
            "created": time.time(),
        }
        with self.transaction() as db:
            current = self.get(identity, db)
            if current["revision"] != study["revision"] or current["trials"] != study["trials"]:
                raise ValueError("Study changed during replay; freeze again")
            current["selections"].append(selection)
            current["selection_id"] = selection["id"]
            self.save(db, current)
        self.export(current)
        self.emit("selection.frozen", identity, selection_id=selection["id"], winner=best["id"])
        return {
            "study_id": identity,
            "status": "selected",
            "selection_id": selection["id"],
            "winner": best,
            "limits": LIMITS,
        }

    def export(self, study):
        with self.transaction() as db:
            return self._export(self.get(study["id"], db))

    def _export(self, study):
        folder = self.root / "workspace/artifacts/formula-lab" / study["id"]
        public_root = (self.root / "workspace/artifacts").resolve()
        if not folder.resolve().is_relative_to(public_root):
            raise ValueError("Formula export escaped public artifacts")
        folder.mkdir(parents=True, exist_ok=True)

        def publish(name, content):
            target = folder / name
            if not target.parent.resolve().is_relative_to(public_root):
                raise ValueError("Formula export escaped public artifacts")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(".publish-" + uuid.uuid4().hex)
            temporary.write_bytes(content)
            temporary.replace(target)

        publish("study.json", json.dumps(self.public(study), ensure_ascii=False, indent=2).encode())
        ranking = self.ranking(study)
        if ranking:
            table = pl.DataFrame(
                [
                    {k: t[k] for k in ["id", "set_id", "model", "dev_score", "cv_mean", "cv_std"]}
                    for t in ranking
                ]
            ).write_csv()
            publish("ranking.csv", table.encode())
        for formula in study["formulas"]:
            publish(
                formula["ref"] + ".py",
                (self.base / study["id"] / "formulas" / formula["ref"] / "source.py").read_bytes(),
            )
        for row in ranking:
            for name in row["hashes"]:
                publish(
                    row["id"] + "/" + name,
                    (self.base / study["id"] / "results" / row["id"] / name).read_bytes(),
                )
        return folder

    def handoff(self, handoff: FormulaHandoff | None):
        bound = self.bound()
        if not bound and handoff is None:
            return None
        if handoff is None:
            raise ValueError(
                "Formula study active: ResearchReply.formula_comparison must select a replayed result or explicitly report incomplete"
            )
        study = self.get(handoff.study_id)
        self.verify_input(study)
        if handoff.status == "incomplete":
            self.export(study)
            if not handoff.reason.strip() or handoff.selection_id:
                raise ValueError("Incomplete handoff requires a reason and no selection_id")
            return {
                "status": "incomplete",
                "reason": handoff.reason,
                "study": self.public(study),
                "other_studies": [s["id"] for s in bound if s["id"] != study["id"]],
            }
        if not study["selection_id"] or handoff.selection_id != study["selection_id"]:
            raise ValueError(
                "Selection missing or stale; freeze_formula_selection after completing all comparisons"
            )
        self.complete(study)
        selection = next(s for s in study["selections"] if s["id"] == handoff.selection_id)
        ranking = self.ranking(study)
        if selection["revision"] != study["revision"] or selection["ranking"] != [
            r["id"] for r in ranking
        ]:
            raise ValueError("Selected comparison universe changed")
        for row in ranking:
            self.verify_result(study, row)
        for formula in study["formulas"]:
            canonical = self.base / study["id"] / "formulas" / formula["ref"]
            if (
                digest(canonical / "source.py") != formula["sha256"]
                or digest(canonical / "spec.json") != formula["spec_sha256"]
            ):
                raise ValueError("Canonical formula snapshot changed")
        self.export(study)
        return {
            "status": "selected",
            "study": self.public(study),
            "selection": selection,
            "winner": ranking[0],
            "ranking": ranking,
            "other_studies": [s["id"] for s in bound if s["id"] != study["id"]],
        }

    def markdown(self, report):
        if not report:
            return ""
        study = report["study"]
        plan = study["plan"]
        base = f"/api/sessions/{quote(self.root.name)}/artifacts/"
        prefix = ""
        url = base + prefix + "artifacts/formula-lab/" + study["id"] + "/"
        lines = [
            "### 公式比较 · 程序核验记录",
            "",
            f"{plan['title']} · {', '.join(plan['labels'])} · 固定 recipe `{plan['recipe_id']}`",
            "",
            f"主指标：`{plan['metric']}`；训练 {len(plan['train'])} 片，开发集 {len(plan['dev'])} 片；按 lot 分组 {plan['folds']} 折。输入摘要 `{plan['input_sha256'][:16]}`。",
            "",
        ]
        if report["status"] == "incomplete":
            lines += ["**比较未完成，尚未确认最佳公式。** " + report["reason"], ""]
            pending = [t for t in study["trials"] if t["status"] != "succeeded"]
            if pending:
                lines += [
                    "未完成尝试："
                    + "；".join(
                        f"{'+'.join(t['formulas'])} / {t['model']}：{t['status']}" for t in pending
                    ),
                    "",
                ]
        else:
            winner = report["winner"]
            lines += [
                f"**当前候选中最佳：{' + '.join(winner['formulas'])}，模型 {winner['model']}。** 重算特征和预测一致。",
                "",
                "| 公式版本 | 模型 | 开发集分数 | lot CV均值 ± 标准差 |",
                "| --- | --- | ---: | ---: |",
            ]
            for row in report["ranking"]:
                lines.append(
                    f"| {' + '.join(row['formulas'])} | {row['model']} | {row['dev_score']:.4f} | {row['cv_mean']:.4f} ± {row['cv_std']:.4f} |"
                )
            lines += [
                "",
                "模型参数：`"
                + json.dumps(plan["model_params"][winner["model"]], ensure_ascii=False)
                + "`。",
                "",
            ]
            for ref in winner["formulas"]:
                formula = next(f for f in study["formulas"] if f["ref"] == ref)
                lines += [
                    f"**{ref}**：{formula['expression']}",
                    "",
                    formula["description"],
                    "",
                    f"传感器：{', '.join(formula['sensors'])}；step：{formula['steps']}；单位：{formula['unit']}；参数：`{json.dumps(formula['params'], ensure_ascii=False)}`。",
                    "",
                    f"[提取代码]({url + quote(ref + '.py')}) · SHA256 `{formula['sha256']}`",
                    "",
                ]
                if formula["change_reason"]:
                    lines += ["修订原因：" + formula["change_reason"], ""]
            lines += [
                f"[逐片预测]({url + winner['id'] + '/predictions.parquet'}) · [模型系数与预处理参数]({url + winner['id'] + '/model_details.json'}) · [已拟合模型]({url + winner['id'] + '/model.joblib'})",
                "",
            ]
        excluded = [s for s in study["sets"] if s["excluded"]]
        if excluded:
            lines += [
                "已明确排除的比较："
                + "；".join(
                    f"{'+'.join(s['formulas'])}：{s['excluded']['reason']}" for s in excluded
                ),
                "",
            ]
        record_link = f"[完整协议、版本与失败记录]({url}study.json)"
        if self.ranking(study):
            record_link += f" · [比较表]({url}ranking.csv)"
        lines += [
            record_link,
            "",
            LIMITS,
        ]
        if report["other_studies"]:
            lines += ["", "其他研究仍单独保存，未合并排名：" + ", ".join(report["other_studies"])]
        return "\n".join(lines)
