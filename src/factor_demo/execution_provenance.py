"""Host-owned, bounded execution/artifact ledger; never trust child-written receipts."""

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from .datasets import file_hash


def next_cell(run: Path, previous: int = 0):
    base = run / ".execution"
    base.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(base / "ledger.sqlite") as db:
        db.execute("CREATE TABLE IF NOT EXISTS sequence(value INTEGER NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT value FROM sequence").fetchone()
        value = max(previous, row[0] if row else 0) + 1
        if row:
            db.execute("UPDATE sequence SET value=?", (value,))
        else:
            db.execute("INSERT INTO sequence VALUES(?)", (value,))
    return value


def artifact_hashes(run):
    from .live_events import artifact_root

    root = artifact_root(run)
    return {
        str(p.relative_to(run / "workspace")): file_hash(p)
        for p in sorted(root.rglob("*"))
        if p.is_file()
        and not p.is_symlink()
        and p.resolve().is_relative_to(root)
        and "formula-lab" not in p.relative_to(root).parts
    }


def record_cell(run: Path, record, source: Path, data_root: Path, before=None, *, source_text=None):
    base = run / ".execution"
    base.mkdir(exist_ok=True)
    identity = f"cell-{record['cell']:06d}"
    try:
        after = artifact_hashes(run)
    except ValueError:
        after = {}
        record["ok"] = False
        record["output"] += "\nArtifact directory escaped its declared workspace"
    artifacts = {k: v for k, v in after.items() if (before or {}).get(k) != v}
    files = {}
    if data_root.exists():
        for p in sorted(data_root.rglob("*")):
            if p.is_file() and not p.is_symlink():
                files[str(p.relative_to(data_root))] = file_hash(p)
    payload = {
        k: record[k] for k in ["cell", "dataset_ref", "ok", "returncode", "started", "elapsed_s"]
    }
    payload.update(
        source_sha256=hashlib.sha256(source_text.encode()).hexdigest()
        if source_text is not None
        else file_hash(source),
        input_files=files,
        artifacts=artifacts,
    )
    with sqlite3.connect(base / "ledger.sqlite") as db:
        db.execute("CREATE TABLE IF NOT EXISTS cells(id TEXT PRIMARY KEY, doc TEXT NOT NULL)")
        db.execute("INSERT INTO cells VALUES(?,?)", (identity, json.dumps(payload)))
    snapshot = base / identity / "artifacts"
    for name in artifacts:
        original = run / "workspace" / name
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    return identity


def artifact_proof(run, name, execution_ref=None):
    path = run / ".execution/ledger.sqlite"
    if not path.exists():
        raise ValueError("Evidence has no trusted execution provenance")
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT id,doc FROM cells ORDER BY rowid DESC").fetchall()
    for identity, body in rows:
        doc = json.loads(body)
        if execution_ref and identity != execution_ref:
            continue
        expected = doc["artifacts"].get(name)
        if expected and doc["ok"]:
            from .live_events import artifact_path

            target = artifact_path(run, name)
            if file_hash(target) != expected:
                raise ValueError("Evidence changed after its recorded execution")
            return {"execution_ref": identity, "artifact_sha256": expected, **doc}
    raise ValueError("Evidence is not bound to a successful recorded cell")
