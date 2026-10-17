"""Immutable, content-addressed datasets with agent-local durable selection stacks.

The registry records arbitrary CodeAct transformations, not a filter DSL. Only
host methods publish versions; scientific workers see a selected read-only tree.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

import polars as pl

from .dataset_adapter import detect_schema, discovery_copy, index_frame, safe_path
from .dataset_models import AcquisitionRequest, AcquisitionResult, DatasetDerivation, DatasetSchema
from .synthetic import write_json


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode()


class AcquisitionProvider(Protocol):
    """Future unified data-entry provider. No network/KG implementation is installed."""

    async def acquire(self, request: AcquisitionRequest) -> AcquisitionResult: ...


class DatasetRegistry:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.base = self.root / ".datasets"
        self.base.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY,doc TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS scopes(owner TEXT PRIMARY KEY,root_id TEXT NOT NULL,current_id TEXT NOT NULL,stack TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS acquisition(owner TEXT,request_id TEXT,request TEXT,result TEXT,PRIMARY KEY(owner,request_id));
            """)

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.base / "registry.sqlite", timeout=15)
        db.execute("PRAGMA journal_mode=WAL")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, identity: str) -> dict:
        if (
            not identity.startswith("ds-")
            or len(identity) != 35
            or any(c not in "0123456789abcdef" for c in identity[3:])
        ):
            raise ValueError("Invalid DatasetRef")
        with self.connect() as db:
            row = db.execute("SELECT doc FROM datasets WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ValueError("Unknown DatasetRef")
        return json.loads(row[0])

    def path(self, identity: str, verify=False) -> Path:
        doc = self.get(identity)
        root = self.base / "versions" / identity
        if verify:
            for name, expected in doc["files"].items():
                if file_hash(safe_path(root, name)) != expected:
                    raise ValueError("Dataset content hash changed: " + name)
            if json.loads((root / "_dataset_ref.json").read_text()) != doc:
                raise ValueError("Dataset manifest changed")
        return root

    def list(self, offset=0, limit=50) -> dict:
        with self.connect() as db:
            rows = db.execute(
                "SELECT doc FROM datasets ORDER BY created DESC,id LIMIT ? OFFSET ?",
                (min(100, max(1, limit)), max(0, offset)),
            ).fetchall()
            total = db.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]
        return {"datasets": [json.loads(r[0]) for r in rows], "total": total, "offset": offset}

    def lineage(self, identity: str) -> dict:
        nodes = {}
        pending = [identity]
        while pending and len(nodes) < 1000:
            current = pending.pop()
            if current in nodes:
                continue
            doc = self.get(current)
            nodes[current] = doc
            pending.extend(doc["parents"])
        if pending:
            raise ValueError("Lineage exceeds display limit; query a narrower branch")
        return {
            "root": identity,
            "nodes": list(nodes.values()),
            "edges": [
                {"parent": p, "child": d["id"]} for d in nodes.values() for p in d["parents"]
            ],
        }

    def register(
        self,
        source: Path,
        schema: DatasetSchema,
        title: str,
        *,
        parents=None,
        reason="",
        code: Path | None = None,
        params=None,
        relation="import",
        provenance=None,
    ) -> dict:
        parents = parents or []
        parent_docs = [self.get(p) for p in parents]
        source = source.resolve()
        frame = index_frame(source, schema)
        if (
            schema.split_column
            and frame.filter(pl.col(schema.split_column).is_in(schema.excluded_splits)).height
        ):
            raise ValueError("Discovery datasets cannot contain held-out entries")
        if relation == "subset":
            allowed = set().union(
                *(
                    set(
                        index_frame(self.path(p), DatasetSchema.model_validate(d["schema"]))[
                            d["schema"]["id_column"]
                        ]
                    )
                    for p, d in zip(parents, parent_docs, strict=True)
                )
            )
            if set(frame[schema.id_column]) - allowed:
                raise ValueError("Subset introduces identities outside its declared parents")
        staging = self.base / "staging" / uuid.uuid4().hex
        staging.mkdir(parents=True)
        try:
            for path in sorted(source.rglob("*")):
                if path.is_symlink():
                    raise ValueError(
                        "Dataset publication rejects symlinks; use parent asset inheritance"
                    )
                if path.is_file():
                    relative = path.relative_to(source)
                    if any(
                        part.startswith(".") or part in {"__pycache__", "truth.private"}
                        for part in relative.parts
                    ):
                        continue
                    dest = staging / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, dest)
            # A row subset may keep immutable asset paths without physically recopying parents.
            if schema.asset_column:
                for name in frame[schema.asset_column].drop_nulls():
                    if Path(name).is_absolute() or ".." in Path(name).parts:
                        raise ValueError("Asset paths must be relative to their dataset")
                    target = staging / name
                    if not target.exists():
                        candidates = [
                            self.path(p) / name for p in parents if (self.path(p) / name).exists()
                        ]
                        if not candidates:
                            raise ValueError("Referenced asset is missing: " + name)
                        if len(candidates) > 1 and any(
                            c.is_file() and file_hash(c) != file_hash(candidates[0])
                            for c in candidates[1:]
                        ):
                            raise ValueError(
                                "Ambiguous parent asset; materialize the intended version"
                            )
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if candidates[0].is_dir():
                            shutil.copytree(candidates[0], target)
                        else:
                            shutil.copyfile(candidates[0], target)
            # All indexes and step metadata are included in the same version.
            if schema.steps_file and not (staging / schema.steps_file).exists():
                inherited = []
                for parent, doc in zip(parents, parent_docs, strict=True):
                    ps = DatasetSchema.model_validate(doc["schema"])
                    if ps.steps_file:
                        step = pl.read_parquet(self.path(parent) / ps.steps_file)
                        column = ps.id_column if ps.id_column in step.columns else "run_id"
                        step = step.filter(
                            pl.col(column).cast(pl.String).is_in(frame[schema.id_column].implode())
                        )
                        if column != schema.id_column:
                            step = step.rename({column: schema.id_column})
                        inherited.append(step)
                if not inherited:
                    raise ValueError("Declared steps file is missing")
                steps = pl.concat(inherited, how="diagonal_relaxed").unique(maintain_order=True)
                keys = [schema.id_column, schema.step_column or "step_id"]
                if steps.select(keys).is_duplicated().any():
                    raise ValueError(
                        "Ambiguous parent step boundaries; materialize the intended version"
                    )
                destination_steps = staging / schema.steps_file
                destination_steps.parent.mkdir(parents=True, exist_ok=True)
                steps.write_parquet(destination_steps)
            if schema.asset_column:
                for name in frame[schema.asset_column].drop_nulls():
                    safe_path(staging, name, directory=True)
            code_hash = file_hash(code) if code else None
            if code:
                shutil.copyfile(code, staging / "_transform.py")
            write_json(staging / "dataset.json", {"schema": schema.model_dump()})
            files = {
                str(p.relative_to(staging)): file_hash(p)
                for p in sorted(staging.rglob("*"))
                if p.is_file() and p.name != "_dataset_ref.json"
            }
            definition = {
                "title": title,
                "schema": schema.model_dump(),
                "parents": parents,
                "reason": reason,
                "relation": relation,
                "params": params or {},
                "provenance": provenance or {},
                "files": files,
                "transform_sha256": code_hash,
            }
            content_hash = hashlib.sha256(canonical(definition)).hexdigest()
            identity = "ds-" + content_hash[:32]
            with self.connect() as db:
                existing = db.execute("SELECT doc FROM datasets WHERE id=?", (identity,)).fetchone()
            if existing:
                return json.loads(existing[0])
            family = parent_docs[0]["family"] if relation == "version" and parent_docs else identity
            with self.connect() as db:
                versions = [
                    json.loads(r[0])["version"]
                    for r in db.execute("SELECT doc FROM datasets")
                    if json.loads(r[0])["family"] == family
                ]
            doc = {
                **definition,
                "id": identity,
                "content_sha256": content_hash,
                "family": family,
                "version": max(versions, default=0) + 1,
                "created": time.time(),
                "rows": frame.height,
                "columns": {n: str(t) for n, t in frame.schema.items()},
                "transform_path": "_transform.py" if code else None,
            }
            destination = self.base / "versions" / identity
            destination.mkdir(parents=True, exist_ok=True)
            for name, sha in files.items():
                blob = self.base / "objects" / sha[:2] / sha
                blob.parent.mkdir(parents=True, exist_ok=True)
                if not blob.exists():
                    temp = blob.with_name("." + uuid.uuid4().hex)
                    shutil.copyfile(staging / name, temp)
                    temp.replace(blob)
                output = destination / name
                output.parent.mkdir(parents=True, exist_ok=True)
                if not output.exists():
                    output.hardlink_to(blob)
            if code:
                (self.base / "transforms").mkdir(exist_ok=True)
                saved = self.base / "transforms" / code_hash
                if not saved.exists():
                    shutil.copyfile(code, saved)
            with self.connect(write=True) as db:
                existing = db.execute("SELECT doc FROM datasets WHERE id=?", (identity,)).fetchone()
                if existing:
                    return json.loads(existing[0])
                versions = [
                    json.loads(r[0])["version"]
                    for r in db.execute("SELECT doc FROM datasets")
                    if json.loads(r[0])["family"] == family
                ]
                doc["version"] = max(versions, default=0) + 1
                write_json(destination / "_dataset_ref.json", doc)
                db.execute(
                    "INSERT OR IGNORE INTO datasets VALUES(?,?,?)",
                    (identity, json.dumps(doc, ensure_ascii=False), doc["created"]),
                )
            return self.get(identity)
        finally:
            shutil.rmtree(staging)

    def bootstrap(self) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key='root'").fetchone()
        if row:
            return self.get(row[0])
        source = self.root / "input"
        schema = detect_schema(source)
        doc = self.register(
            source,
            schema,
            "主数据集",
            reason="Session discovery input",
            provenance={"kind": "session_input"},
        )
        with self.connect(write=True) as db:
            db.execute("INSERT OR IGNORE INTO meta VALUES('root',?)", (doc["id"],))
        return doc

    def bind(self, owner: str, root_ref: str):
        self.get(root_ref)
        with self.connect(write=True) as db:
            db.execute(
                "INSERT OR IGNORE INTO scopes VALUES(?,?,?,?)", (owner, root_ref, root_ref, "[]")
            )

    def scope(self, owner: str) -> dict:
        with self.connect() as db:
            row = db.execute(
                "SELECT root_id,current_id,stack FROM scopes WHERE owner=?", (owner,)
            ).fetchone()
        if not row:
            raise ValueError("Dataset scope is not initialized")
        return {"root": row[0], "current": row[1], "stack": json.loads(row[2])}

    def select(self, owner: str, identity: str, back: str = "") -> dict:
        self.path(identity, verify=True) if not back else None
        with self.connect(write=True) as db:
            row = db.execute(
                "SELECT root_id,current_id,stack FROM scopes WHERE owner=?", (owner,)
            ).fetchone()
            root, current, stack = row[0], row[1], json.loads(row[2])
            if back == "root":
                identity = root
                stack = []
            elif back == "parent":
                identity = stack.pop() if stack else root
            elif identity != current:
                stack.append(current)
            db.execute(
                "UPDATE scopes SET current_id=?,stack=? WHERE owner=?",
                (identity, json.dumps(stack), owner),
            )
        return self.get(identity)


class DatasetSession:
    def __init__(self, run: Path, events=None, provider: AcquisitionProvider | None = None):
        self.run = run.resolve()
        self.events = events
        branch = (
            json.loads((self.run / "branch.json").read_text())
            if (self.run / "branch.json").is_file()
            else None
        )
        self.root = events.root if events else self.run.parent.parent if branch else self.run
        self.owner = events.agent_id if events else branch["agent_id"] if branch else "main"
        self.registry = DatasetRegistry(self.root)
        self.provider = provider
        root = self.registry.bootstrap()
        self.registry.bind(self.owner, (branch or {}).get("dataset_ref") or root["id"])

    def authorize(self, identity):
        doc = self.registry.get(identity)
        if self.owner != "main":
            bound = self.registry.scope(self.owner)["root"]
            if identity != bound and bound not in {
                n["id"] for n in self.registry.lineage(identity)["nodes"]
            }:
                raise PermissionError("Dataset is outside this branch bound root")
        return doc

    def current(self):
        return self.registry.get(self.registry.scope(self.owner)["current"])

    def path(self, identity=""):
        identity = identity or self.current()["id"]
        self.authorize(identity)
        return self.registry.path(identity)

    def schema(self, identity=""):
        return DatasetSchema.model_validate(
            self.registry.get(identity or self.current()["id"])["schema"]
        )

    def emit(self, kind, **data):
        if self.events:
            self.events.emit("dataset." + kind, **data)

    def describe(self, identity=""):
        doc = self.registry.get(identity or self.current()["id"])
        return {
            **doc,
            "scope": self.registry.scope(self.owner),
            "worker_path": str(self.path(doc["id"])),
        }

    def context_summary(self):
        d = self.current()
        return json.dumps(
            {
                "dataset_ref": d["id"],
                "title": d["title"],
                "modality": d["schema"]["modality"],
                "rows": d["rows"],
                "parents": d["parents"],
                "schema": d["schema"],
                "scope": self.registry.scope(self.owner),
                "worker_input": "DATASET (read-only pathlib.Path); DATASET_ID. Use these instead of hardcoded ../input.",
                "history": "list_datasets/inspect_dataset/read_dataset_lineage",
            },
            ensure_ascii=False,
        )

    def select(self, identity="", back=""):
        if not back:
            self.authorize(identity)
        d = self.registry.select(self.owner, identity, back)
        self.emit(
            "selected",
            dataset_ref=d["id"],
            scope=self.registry.scope(self.owner),
            agent_id=self.owner,
        )
        return self.describe(d["id"])

    def derive(self, spec: DatasetDerivation):
        for parent in spec.parents:
            self.authorize(parent)
        work = self.run / "workspace"
        source = safe_path(work, spec.output_dir, directory=True)
        code = safe_path(work, spec.source)
        if not source.is_dir():
            raise ValueError("Derivation output must be a directory")
        doc = self.registry.register(
            source,
            spec.data_schema,
            spec.title,
            parents=spec.parents,
            reason=spec.reason,
            code=code,
            params=spec.params,
            relation=spec.relation,
            provenance=spec.provenance,
        )
        self.emit("registered", dataset_ref=doc["id"], parents=doc["parents"], rows=doc["rows"])
        return doc

    async def acquire(self, request: AcquisitionRequest):
        request = request.model_copy(
            update={"parent_refs": request.parent_refs or [self.current()["id"]]}
        )
        for parent in request.parent_refs:
            self.authorize(parent)
        with self.registry.connect() as db:
            old = db.execute(
                "SELECT request,result FROM acquisition WHERE owner=? AND request_id=?",
                (self.owner, request.request_id),
            ).fetchone()
        if old:
            if json.loads(old[0]) != request.model_dump():
                raise ValueError("Acquisition request_id reused with different parameters")
            return json.loads(old[1])
        result = (
            await self.provider.acquire(request)
            if self.provider
            else AcquisitionResult(
                status="unavailable",
                message="Unified acquisition provider is not configured; no external data or knowledge graph was accessed.",
            )
        )
        payload = result.model_dump()
        if result.status == "supplied":
            if not result.local_path or not result.data_schema:
                raise ValueError("Supplied acquisition must include a local path and schema")
            doc = self.registry.register(
                Path(result.local_path),
                result.data_schema,
                request.reason,
                parents=request.parent_refs,
                reason=request.reason,
                relation="acquisition",
                provenance={
                    "source": request.source,
                    "criteria": request.criteria,
                    "lead_refs": request.lead_refs,
                    "request_id": request.request_id,
                    **result.provenance,
                },
            )
            payload["dataset_ref"] = doc["id"]
        with self.registry.connect(write=True) as db:
            db.execute(
                "INSERT INTO acquisition VALUES(?,?,?,?)",
                (self.owner, request.request_id, request.model_dump_json(), json.dumps(payload)),
            )
        self.emit("acquisition", request_id=request.request_id, **payload)
        return payload


def prepare_generic_workspace(data: Path, run: Path, schema: DatasetSchema | None = None):
    if run.exists() and any(run.iterdir()):
        raise ValueError("Choose a new session directory")
    public = run / "input"
    (run / "workspace/artifacts").mkdir(parents=True)
    schema = discovery_copy(data, public, schema)
    frame = index_frame(public, schema)
    write_json(
        run / "session.json",
        {
            "mode": "dynamic_datasets",
            "discovery_target_runs": frame.filter(~pl.col(schema.control_column)).height
            if schema.control_column
            else frame.height,
            "discovery_control_runs": frame.filter(pl.col(schema.control_column)).height
            if schema.control_column
            else 0,
            "public_sha256": {
                str(p.relative_to(public)): file_hash(p) for p in public.rglob("*") if p.is_file()
            },
        },
    )
    return DatasetSession(run).describe()
