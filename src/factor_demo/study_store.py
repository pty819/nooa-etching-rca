"""Short SQLite transactions for durable formula protocols and attempt history."""

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class StudyStore:
    def __init__(self, base: Path):
        self.base = base

    @contextmanager
    def transaction(self, write=True):
        self.base.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.base / "registry.sqlite", timeout=30)
        try:
            db.execute("CREATE TABLE IF NOT EXISTS studies(id TEXT PRIMARY KEY,body TEXT NOT NULL)")
            if write:
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
            with self.transaction(write=False) as connection:
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

    def list(self):
        if not (self.base / "registry.sqlite").exists():
            return []
        with self.transaction(write=False) as db:
            return [
                json.loads(row[0]) for row in db.execute("SELECT body FROM studies ORDER BY id")
            ]

    def recent(self, limit=5):
        if not (self.base / "registry.sqlite").exists():
            return []
        with self.transaction(write=False) as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT body FROM studies ORDER BY rowid DESC LIMIT ?", (limit,)
                )
            ]
