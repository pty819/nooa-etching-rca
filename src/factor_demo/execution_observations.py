"""Durable host observations shared by execution, correction and read-only reflection."""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

parent_execution = ContextVar("parent_execution", default=None)


class ExecutionObservations:
    def __init__(self, run):
        self.path = run / ".execution" / "observations.sqlite"

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.execute("CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, doc TEXT)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS repairs(failure TEXT, verification TEXT, PRIMARY KEY(failure,verification))"
        )
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, doc):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO observations VALUES(?,?)",
                (doc["observation_id"], json.dumps(doc)),
            )
        return doc["observation_id"]

    def record(self, kind, status, **fields):
        identity = "observation-" + uuid.uuid4().hex
        return self.save(
            {
                "observation_id": identity,
                "kind": kind,
                "status": status,
                "parent_id": parent_execution.get(),
                "created": time.time(),
                **fields,
            }
        )

    def get(self, identity):
        with self.connect() as db:
            row = db.execute("SELECT doc FROM observations WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ValueError("Unknown execution observation")
        return json.loads(row[0])

    def resolve(self, failure, verification):
        original, checked = self.get(failure), self.get(verification)
        if (
            original["status"] != "failed"
            or checked["status"] != "passed"
            or checked.get("repairs") != failure
        ):
            raise ValueError(
                "Repair requires a failed observation and its passed original-path verification"
            )
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO repairs VALUES(?,?)", (failure, verification))
            rows = [json.loads(r[0]) for r in db.execute("SELECT doc FROM observations")]
            parents = {failure}
            for _ in range(len(rows)):
                children = {r["observation_id"] for r in rows if r.get("parent_id") in parents}
                if children <= parents:
                    break
                parents |= children
            for child in rows:
                if child["observation_id"] in parents and child["status"] == "failed":
                    db.execute(
                        "INSERT OR IGNORE INTO repairs VALUES(?,?)",
                        (child["observation_id"], verification),
                    )

    def read(self, identity, offset=0, limit=12000):
        text = json.dumps(self.get(identity), ensure_ascii=False, indent=2)
        offset, limit = max(0, offset), min(32000, max(1, limit))
        return {
            "observation_id": identity,
            "content": text[offset : offset + limit],
            "next_offset": offset + limit if len(text) > offset + limit else None,
        }

    def context(self):
        if not self.path.exists():
            return {"recent": [], "unresolved_failures": []}
        with self.connect() as db:
            rows = db.execute("SELECT id,doc FROM observations ORDER BY rowid DESC").fetchall()
            resolved = {r[0] for r in db.execute("SELECT failure FROM repairs")}

        def compact(body):
            doc = json.loads(body)
            result = {
                k: doc[k]
                for k in (
                    "observation_id",
                    "parent_id",
                    "kind",
                    "status",
                    "execution_ref",
                    "experiment_id",
                    "epoch",
                    "state_effect",
                    "runtime_status",
                    "exception_type",
                    "repairs",
                    "goal",
                )
                if k in doc
            }
            for key in ("code", "source_ref", "output", "checks", "error"):
                if key in doc:
                    value = str(doc[key])
                    result[key] = (
                        value
                        if len(value) <= 2000
                        else value[:900]
                        + "\n[read_observation for full evidence]\n"
                        + value[-1000:]
                    )
            return result

        failed = [
            (key, body)
            for key, body in rows
            if json.loads(body)["status"] == "failed"
            and json.loads(body)["kind"] != "orchestration"
            and key not in resolved
        ]
        return {
            "recent": [compact(body) for _, body in rows[:3]],
            "unresolved_failures": [compact(body) for _, body in failed[:6]],
            "unresolved_count": len(failed),
            "semantics": "Supplied checks and repair-path verification are distinct from goal acceptance. Unrelated success never resolves failures.",
        }
