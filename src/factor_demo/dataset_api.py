"""Local supplied-data APIs; acquisition backends and KG are intentionally deferred."""

import asyncio
import json
import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .case_lease import case_lease
from .dataset_adapter import discovery_copy, index_frame, safe_path
from .dataset_models import DatasetSchema
from .datasets import DatasetSession
from .live_events import LiveEvents, branch_path


class DatasetChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_ref: str = ""
    agent_id: str = "main"
    back: str = ""


class LocalDatasetImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: str
    data_schema: DatasetSchema | None = None
    title: str = Field(default="补充的本地数据", max_length=200)
    reason: str = Field(min_length=1, max_length=8000)
    agent_id: str = "main"


def supplied_path(project: Path, name: str):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", name):
        raise HTTPException(400, "Choose a supplied item from the data directory")
    path = (project / "data" / name).resolve()
    if path.parent != (project / "data").resolve() or not path.exists():
        raise HTTPException(400, "Unknown supplied input")
    return path


def available_inputs(project):
    result = []
    extensions = {".parquet", ".csv", ".jsonl", ".json", ".png", ".jpg", ".jpeg", ".txt", ".md"}
    for path in sorted((project / "data").glob("*")):
        if path.name.startswith("."):
            continue
        if (
            path.is_file()
            and path.suffix.lower() in extensions
            or path.is_dir()
            and (
                any(
                    (path / n).exists()
                    for n in [
                        "runs.parquet",
                        "dataset.json",
                        "samples.parquet",
                        "samples.csv",
                        "samples.jsonl",
                    ]
                )
                or any(
                    p.suffix.lower() in {".png", ".jpg", ".jpeg", ".txt", ".md"}
                    for p in path.iterdir()
                    if p.is_file()
                )
            )
        ):
            result.append(path.name)
    return result


def dataset_state(run, agent_id="main", offset=0, limit=50):
    local = run if agent_id == "main" else branch_path(run, agent_id)
    session = DatasetSession(local)
    scope = session.registry.scope(session.owner)
    compact = lambda d: (
        {
            k: d[k]
            for k in [
                "id",
                "title",
                "rows",
                "parents",
                "version",
                "family",
                "relation",
                "created",
                "reason",
            ]
        }
        | {"modality": d["schema"]["modality"]}
    )
    page = session.registry.list(offset=offset, limit=limit)
    items = []
    for doc in page["datasets"]:
        try:
            session.authorize(doc["id"])
        except PermissionError:
            continue
        items.append(compact(doc))
    graph = session.registry.lineage(scope["current"])
    return {
        "datasets": items,
        "total": page["total"],
        "next_offset": offset + len(page["datasets"]),
        "scope": scope,
        "current": compact(session.current()),
        "lineage": {"nodes": [compact(d) for d in graph["nodes"]], "edges": graph["edges"]},
        "acquisition": {
            "status": "unavailable",
            "message": "Only the abstract unified acquisition interface is provided; no external data or knowledge graph connector is configured.",
        },
    }


def router(project, session_path, jobs):
    api = APIRouter()

    @api.get("/api/sessions/{name}/datasets")
    async def listing(name: str, agent_id: str = "main", offset: int = 0, limit: int = 50):
        try:
            return await asyncio.to_thread(
                dataset_state, session_path(name), agent_id, max(0, offset), limit
            )
        except (ValueError, PermissionError) as error:
            raise HTTPException(400, str(error)) from None

    @api.post("/api/sessions/{name}/datasets/select")
    async def select(name: str, body: DatasetChoice):
        run = session_path(name)
        if name in jobs:
            raise HTTPException(409, "Use agent guidance to change scope during an active task")
        if body.back not in {"", "parent", "root"}:
            raise HTTPException(400, "Choose parent or root")
        try:
            local = run if body.agent_id == "main" else branch_path(run, body.agent_id)
            s = DatasetSession(local)

            def perform_select():
                with case_lease(run):
                    return s.select(body.dataset_ref, body.back)

            result = await asyncio.to_thread(perform_select)
            e = LiveEvents(local, root=run, agent_id=body.agent_id)
            e.emit("dataset.selected", dataset_ref=result["id"], scope=s.registry.scope(s.owner))
            e.close()
            return await asyncio.to_thread(dataset_state, run, body.agent_id)
        except (ValueError, PermissionError) as error:
            raise HTTPException(400, str(error)) from None

    @api.post("/api/sessions/{name}/datasets/import")
    async def import_data(name: str, body: LocalDatasetImport):
        run = session_path(name)
        if name in jobs:
            raise HTTPException(409, "Import supplied data while this session is idle")
        source = supplied_path(project, body.dataset)
        local = run if body.agent_id == "main" else branch_path(run, body.agent_id)
        s = DatasetSession(local)

        def perform_unlocked():
            stage = s.registry.base / "supplied" / uuid.uuid4().hex
            try:
                schema = discovery_copy(source, stage, body.data_schema)
                return s.registry.register(
                    stage,
                    schema,
                    body.title,
                    parents=[s.current()["id"]],
                    reason=body.reason,
                    relation="local_import",
                    provenance={"kind": "supplied_local_input", "source": body.dataset},
                )
            finally:
                shutil.rmtree(stage, ignore_errors=True)

        def perform():
            with case_lease(run):
                return perform_unlocked()

        try:
            doc = await asyncio.to_thread(perform)
        except (ValueError, OSError) as error:
            raise HTTPException(400, str(error)) from None
        e = LiveEvents(local, root=run, agent_id=body.agent_id)
        e.emit(
            "dataset.registered", dataset_ref=doc["id"], parents=doc["parents"], rows=doc["rows"]
        )
        e.close()
        return {"dataset": doc, "state": await asyncio.to_thread(dataset_state, run, body.agent_id)}

    @api.get("/api/sessions/{name}/datasets/{identity}")
    async def inspect(name: str, identity: str):
        s = DatasetSession(session_path(name))
        try:
            doc = s.describe(identity)
            frame = await asyncio.to_thread(index_frame, s.path(identity), s.schema(identity))
            source = (
                (s.path(identity) / doc["transform_path"]).read_text()
                if doc["transform_path"]
                else ""
            )
            return {
                **doc,
                "preview": json.loads(frame.head(20).write_json()),
                "transform_source": source[:32000],
                "source_truncated": len(source) > 32000,
            }
        except (ValueError, OSError) as error:
            raise HTTPException(400, str(error)) from None

    @api.get("/api/sessions/{name}/datasets/{identity}/files/{path:path}")
    async def asset(name: str, identity: str, path: str):
        s = DatasetSession(session_path(name))
        try:
            root = await asyncio.to_thread(s.registry.path, identity, True)
            target = safe_path(root, path)
        except (ValueError, OSError):
            raise HTTPException(404, "Dataset asset not found") from None
        images = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        if target.suffix.lower() in images:
            return FileResponse(target, media_type=images[target.suffix.lower()])
        return FileResponse(target, media_type="application/octet-stream", filename=target.name)

    return api
