"""Supervisor-owned immutable source revisions; no importable script files."""

import hashlib
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class CodeBuffers:
    def __init__(self, run: Path):
        self.path = run / ".execution" / "code.sqlite"

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path)
        db.execute(
            "CREATE TABLE IF NOT EXISTS versions (name TEXT, revision INTEGER, source TEXT, sha256 TEXT, PRIMARY KEY(name,revision))"
        )
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def validate(name):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", name):
            raise ValueError("Use a code buffer name: letter followed by letters, digits, _ or -")

    def get(self, name, revision=0):
        self.validate(name)
        with self.connect() as db:
            row = db.execute(
                "SELECT revision,source,sha256 FROM versions WHERE name=? AND (?=0 OR revision=?) ORDER BY revision DESC LIMIT 1",
                (name, revision, revision),
            ).fetchone()
        if row is None:
            raise ValueError("Unknown code buffer/revision")
        return {
            "name": name,
            "revision": row[0],
            "source": row[1],
            "sha256": row[2],
            "source_ref": f"cell://{name}@{row[0]}",
        }

    def write(self, name, source, expected_revision=0, *, old=None, new=None):
        self.validate(name)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT revision,source FROM versions WHERE name=? ORDER BY revision DESC LIMIT 1",
                (name,),
            ).fetchone()
            current = row[0] if row else 0
            if expected_revision != current:
                raise ValueError(
                    f"Revision conflict: expected {expected_revision}, current {current}"
                )
            if old is not None:
                if not row or not old or row[1].count(old) != 1:
                    raise ValueError("Edit requires exactly one nonempty old-text match")
                source = row[1].replace(old, new, 1)
            if len(source) > 1_000_000:
                raise ValueError("Source exceeds 1,000,000 characters")
            revision = current + 1
            digest = hashlib.sha256(source.encode()).hexdigest()
            db.execute("INSERT INTO versions VALUES(?,?,?,?)", (name, revision, source, digest))
        return {
            "name": name,
            "revision": revision,
            "sha256": digest,
            "source_ref": f"cell://{name}@{revision}",
            "executed": False,
        }

    def catalog(self):
        with self.connect() as db:
            rows = db.execute(
                "SELECT name,MAX(revision) FROM versions GROUP BY name ORDER BY name"
            ).fetchall()
        return [{"name": name, "revision": rev} for name, rev in rows]
