"""Deterministic data and experiment tools exposed to the NOOA agent."""

from __future__ import annotations

import hashlib
import json
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.feature_selection import f_classif
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, confusion_matrix, f1_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .factors import Factor, transform
from .synthetic import METADATA, write_json


def fingerprint(specs: list[Factor]) -> str:
    parts = []
    for s in specs:
        cols = sorted(s.columns) if s.operation == "product" else s.columns
        parts.append({"operation": s.operation, "columns": cols})
    canonical = sorted(parts, key=lambda x: json.dumps(x, sort_keys=True))
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def fit_model(x: np.ndarray, y: pd.Series):
    if np.any(np.all(np.isnan(x), axis=0)):
        raise ValueError("A factor is entirely missing on training data")
    model = make_pipeline(
        SimpleImputer(strategy="median"),
        StandardScaler(),
        LogisticRegression(C=1.0, max_iter=1000, class_weight="balanced"),
    )
    return model.fit(x, y)


def metrics(y, pred) -> dict:
    labels = ["label1", "label2", "label3"]
    return {
        "balanced_accuracy": float(balanced_accuracy_score(y, pred)),
        "macro_f1": float(f1_score(y, pred, labels=labels, average="macro", zero_division=0)),
        "confusion_matrix": confusion_matrix(y, pred, labels=labels).tolist(),
    }


def score_candidate(
    train: pd.DataFrame, dev: pd.DataFrame, specs: list[Factor], allowed: set[str]
) -> dict:
    x_train = transform(train, specs, allowed)
    x_dev = transform(dev, specs, allowed)
    model = fit_model(x_train, train.label)
    pred = model.predict(x_dev)
    result = metrics(dev.label, pred)
    result["train_balanced_accuracy"] = float(
        balanced_accuracy_score(train.label, model.predict(x_train))
    )
    result["missing_fraction"] = float(np.isnan(x_dev).mean())
    result["by_group"] = {}
    for column in ["recipe_id", "chamber_id"]:
        groups = {}
        for name, idx in dev.groupby(column).indices.items():
            y = dev.label.iloc[idx]
            groups[name] = {
                "n": len(idx),
                "evidence": "sufficient" if y.nunique() == 3 and len(idx) >= 20 else "insufficient",
            }
            if groups[name]["evidence"] == "sufficient":
                groups[name]["balanced_accuracy"] = float(balanced_accuracy_score(y, pred[idx]))
        result["by_group"][column] = groups
    return result


class Research:
    def __init__(self, data: Path, run: Path, budget: int):
        self.data, self.run = data.resolve(), run.resolve()
        self.run.mkdir(parents=True, exist_ok=True)
        if (self.run / "frozen.json").exists():
            raise ValueError("This run is frozen; start a new run for further research")
        manifest = json.loads((self.data / "manifest.json").read_text())
        for name in ["train", "dev"]:
            digest = hashlib.sha256((self.data / f"{name}.parquet").read_bytes()).hexdigest()
            if digest != manifest["sha256"][name]:
                raise ValueError("Dataset file does not match its manifest")
        # Test data and generator truth are deliberately not loaded here.
        self.train = pd.read_parquet(self.data / "train.parquet")
        self.dev = pd.read_parquet(self.data / "dev.parquet")
        self.catalog = json.loads((self.data / "catalog.json").read_text())
        self.allowed = {r["name"] for r in self.catalog}
        self.records = self._load_records()
        self.budget = budget
        self.notes = (
            (self.run / "notes.txt").read_text() if (self.run / "notes.txt").exists() else ""
        )
        if (self.run / "config.json").exists():
            old = json.loads((self.run / "config.json").read_text())
            if old["dataset_sha256"] != manifest["sha256"]:
                raise ValueError("Dataset changed since this run started")
        write_json(
            self.run / "config.json",
            {
                "data": str(self.data),
                "dataset_sha256": manifest["sha256"],
                "max_experiments": budget,
                "model": "median/standardize/logistic-regression C=1",
            },
        )
        self._save_state("ready")

    def _load_records(self) -> list[dict]:
        path = self.run / "experiments.jsonl"
        return (
            [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
            if path.exists()
            else []
        )

    def event(self, name: str, payload: object) -> None:
        with (self.run / "events.jsonl").open("a") as f:
            f.write(
                json.dumps(
                    {"time": time.time(), "event": name, "data": payload},
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            )

    def _save_state(self, status: str) -> None:
        write_json(
            self.run / "state.json",
            {
                "status": status,
                "completed_experiments": len(self.records),
                "remaining": max(0, self.budget - len(self.records)),
                "best": self.best(),
            },
        )

    def best(self) -> dict | None:
        valid = [r for r in self.records if r["status"] == "success"]
        return (
            max(valid, key=lambda r: (r["metrics"]["balanced_accuracy"], -len(r["factors"])))
            if valid
            else None
        )

    def describe(self) -> dict:
        result = {
            "rows": {"train": len(self.train), "dev": len(self.dev)},
            "feature_count": len(self.allowed),
            "label_counts": {
                s: frame.label.value_counts().to_dict()
                for s, frame in [("train", self.train), ("dev", self.dev)]
            },
            "metadata": METADATA,
            "target": "label",
            "split": "chronological, disjoint runs and batches",
            "operations": ["raw", "difference", "ratio", "product"],
            "remaining_experiments": max(0, self.budget - len(self.records)),
            "expert_notes": self.notes,
            "best": self.best(),
            "dummy_dev_balanced_accuracy": float(
                balanced_accuracy_score(
                    self.dev.label, np.repeat(self.train.label.mode()[0], len(self.dev))
                )
            ),
        }
        self.event("describe", result)
        return result

    def screen(self, top_k: int = 12) -> list[dict]:
        columns = list(self.train.columns[5:])
        x = self.train[columns].to_numpy(dtype=float)
        medians = np.nanmedian(x, axis=0)
        x = np.where(np.isnan(x), medians, x)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            scores, _ = f_classif(x, self.train.label)
        order = np.argsort(np.nan_to_num(scores, nan=-1.0))[::-1][: max(1, min(top_k, 30))]
        result = [
            {"feature": columns[i], "train_f_score": round(float(scores[i]), 3)} for i in order
        ]
        self.event("screen", result)
        return result

    def related(self, feature: str) -> list[dict]:
        if feature not in self.allowed:
            raise ValueError("Unknown feature")
        sensor = feature.split("__")[0]
        result = [r for r in self.catalog if r["sensor"] == sensor]
        self.event("related", {"feature": feature, "features": result})
        return result

    def inspect(self, columns: list[str]) -> dict:
        if not 1 <= len(columns) <= 8 or not set(columns) <= self.allowed:
            raise ValueError("Inspect 1-8 catalogued feature columns")
        result = {}
        for name, df in [("train", self.train), ("dev", self.dev)]:
            # JSON null replaces missing correlations instead of emitting NaN.
            summary = {
                "class_means": df.groupby("label")[columns].mean().round(3).to_dict(),
                "correlation": df[columns].corr().round(3).to_dict(),
                "missing": df[columns].isna().mean().round(3).to_dict(),
            }
            result[name] = json.loads(json.dumps(summary).replace("NaN", "null"))
        self.event("inspect", {"columns": columns, "result": result})
        return result

    def evaluate(self, factors: list[dict], rationale: str) -> dict:
        start = time.monotonic()
        if len(self.records) >= self.budget:
            return {"status": "budget_exhausted", "best": self.best()}
        if len(rationale) > 2000:
            raise ValueError("Keep rationale within 2000 characters")
        # Failed proposals also consume budget and are persisted.
        record = {
            "id": f"exp_{len(self.records) + 1:03d}",
            "factors": factors,
            "rationale": rationale,
            "status": "running",
        }
        try:
            specs = [Factor.model_validate(s) for s in factors]
            key = fingerprint(specs)
            duplicate = next((r for r in self.records if r.get("fingerprint") == key), None)
            if duplicate:
                result = {"status": "duplicate", "previous": duplicate}
                self.event("duplicate", result)
                return result
            record["fingerprint"] = key
            self.event("experiment_started", record)
            record["metrics"] = score_candidate(self.train, self.dev, specs, self.allowed)
            record["status"] = "success"
        except (ValueError, KeyError, TypeError) as e:
            record.update(status="failed", error=str(e)[:1500])
        record["duration_seconds"] = round(time.monotonic() - start, 3)
        with (self.run / "experiments.jsonl").open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        self.records.append(record)
        self._save_state("researching")
        self.event("experiment_completed", record)
        print(
            f"{record['id']}: {record['status']} "
            f"dev={record.get('metrics', {}).get('balanced_accuracy', '-')}",
            flush=True,
        )
        return record

    def add_note(self, note: str) -> None:
        self.notes += "\n" + note
        (self.run / "notes.txt").write_text(self.notes)
        self.event("expert_note", note)

    def finding(self, text: str) -> dict:
        self.event("finding", text[:4000])
        return {"saved": True}
