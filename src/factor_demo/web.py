"""Local research UI, durable SSE replay, background turns and confined artifacts."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .autoresearch import render_reply
from .live_events import LiveEvents, branch_path, public_artifact_index, public_artifact_path
from .research_branches import BranchStore
from .research_workspace import prepare_workspace
from .synthetic import write_json


class NewSession(BaseModel):
    title: str = Field(default="腔室匹配研究", max_length=80)
    dataset: str = "etching-v2"


class NewMessage(BaseModel):
    message: str = Field(min_length=1, max_length=30000)
    agent_id: str = Field(default="main", pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    provider: str = "glm"
    mode: str = "research"
    request_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex, pattern=r"^[a-zA-Z0-9_-]{1,80}$"
    )


class ReattachedProcess:
    """Observe a verified surviving worker after only the HTTP server restarted."""

    def __init__(self, process):
        self.process = process
        self.pid = process.pid

    @property
    def returncode(self):
        try:
            return (
                None
                if self.process.is_running() and self.process.status() != psutil.STATUS_ZOMBIE
                else 0
            )
        except psutil.Error:
            return 0

    async def wait(self):
        while self.returncode is None:
            await asyncio.sleep(0.2)
        return 0

    def kill(self):
        self.process.kill()


def create_app(project: Path | None = None) -> FastAPI:
    project = (project or Path.cwd()).resolve()
    runs = project / "runs"
    jobs = {}
    locks = {}

    async def stop_job(job):
        run, task, proc = job["run"], job["id"], job["process"]
        (run / "requests" / f"{task}.cancel").touch()
        if proc and proc.returncode is None:
            # Keep the descendant identities before a hard stop, since workers own process groups.
            with contextlib.suppress(psutil.Error):
                job["children"] = psutil.Process(proc.pid).children(recursive=True)
            try:
                await asyncio.wait_for(asyncio.shield(job["waiter"]), 8)
            except TimeoutError:
                for child in reversed(job.get("children", [])):
                    with contextlib.suppress(psutil.Error):
                        child.kill()
                with contextlib.suppress(ProcessLookupError, psutil.Error):
                    proc.kill()
                await proc.wait()

    @asynccontextmanager
    async def lifespan(app):
        await recover_jobs()
        yield
        await asyncio.gather(*(stop_job(j) for j in list(jobs.values())), return_exceptions=True)

    app = FastAPI(title="NOOA Research", lifespan=lifespan)
    app.state.jobs = jobs

    @app.middleware("http")
    async def local_guard(request: Request, call_next):
        if request.method in {"POST", "DELETE", "PUT", "PATCH"}:
            if request.headers.get("x-research-client") != "1":
                from fastapi.responses import JSONResponse

                return JSONResponse({"detail": "Missing local client header"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                from fastapi.responses import JSONResponse

                return JSONResponse({"detail": "Cross-origin writes are disabled"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    def session(name):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", name):
            raise HTTPException(404, "Session not found")
        path = (runs / name).resolve()
        if path.parent != runs.resolve() or not (path / "session.json").is_file():
            raise HTTPException(404, "Session not found")
        return path

    def info(run):
        ui = json.loads((run / "ui.json").read_text()) if (run / "ui.json").exists() else {}
        cfg = json.loads((run / "session.json").read_text())
        job = jobs.get(run.name)
        return {
            "id": run.name,
            "title": ui.get("title", run.name),
            "active": bool(job),
            "task_id": job["id"] if job else None,
            "active_agent_id": job.get("agent_id", "main") if job else "main",
            "targets": cfg["discovery_target_runs"],
            "controls": cfg["discovery_control_runs"],
            "updated": (run / "conversation.jsonl").stat().st_mtime
            if (run / "conversation.jsonl").exists()
            else run.stat().st_mtime,
        }

    @app.get("/api/config")
    async def config():
        from .providers import PRIVATE_CONFIG

        data = (
            json.loads((project / PRIVATE_CONFIG).read_text())
            if (project / PRIVATE_CONFIG).exists()
            else {}
        )
        providers = [{"id": k, "model": v["model"]} for k, v in data.items()]
        if (
            os.environ.get("FACTOR_MODEL")
            and os.environ.get("FACTOR_API_BASE")
            and os.environ.get("FACTOR_API_KEY")
            and not any(p["id"] == "custom" for p in providers)
        ):
            providers.append({"id": "custom", "model": os.environ["FACTOR_MODEL"]})
        return {
            "providers": providers,
            "capabilities": __import__("factor_demo.capabilities", fromlist=["Capabilities"])
            .Capabilities.load(project / "configs/capabilities.json")
            .model_dump(),
            "datasets": [
                p.name
                for p in (project / "data").glob("etching-v*")
                if (p / "recipe.json").exists()
            ],
        }

    @app.get("/api/sessions")
    async def sessions():
        items = [
            info(p)
            for p in runs.glob("*")
            if (p / "session.json").is_file()
            and ((p / "conversation.jsonl").exists() or (p / "ui.json").exists())
        ]
        return sorted(items, key=lambda x: x["updated"], reverse=True)

    @app.post("/api/sessions")
    async def new_session(body: NewSession):
        if not re.fullmatch(r"etching-v[\w-]+", body.dataset):
            raise HTTPException(400, "Unknown dataset")
        data = (project / "data" / body.dataset).resolve()
        if data.parent != (project / "data").resolve() or not (data / "recipe.json").is_file():
            raise HTTPException(400, "Unknown dataset")
        run = runs / f"case-{time.strftime('%m%d')}-{uuid.uuid4().hex[:8]}"
        await asyncio.to_thread(prepare_workspace, data, run)
        write_json(run / "ui.json", {"title": body.title})
        return info(run)

    @app.get("/api/sessions/{name}")
    async def detail(name):
        run = session(name)
        history = []
        if (run / "conversation.jsonl").exists():
            rows = [
                json.loads(line) for line in (run / "conversation.jsonl").read_text().splitlines()
            ]
            live_rounds = {r["round"] for r in rows if r.get("turn_id")}
            for row in rows:
                if row.get("turn_id") or row["round"] in live_rounds:
                    continue  # live messages replay from ui.sqlite, not twice
                if row["speaker"] != "agent":
                    history.append(
                        {
                            "id": f"legacy-user-{row['round']}",
                            "role": "user",
                            "markdown": row["message"],
                            "round": row["round"],
                        }
                    )
                else:
                    history.append(
                        {
                            "id": f"legacy-agent-{row['round']}",
                            "role": "assistant",
                            "markdown": render_reply(row),
                            "round": row["round"],
                        }
                    )
        events = LiveEvents(run)
        last_id = events.db.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
        events.close()
        return {
            **info(run),
            "history": history,
            "artifacts": public_artifact_index(run),
            "branches": BranchStore(run).list(),
            "last_event_id": last_id,
            "formula_studies": __import__("factor_demo.formula_lab", fromlist=["FormulaLab"])
            .FormulaLab(run)
            .catalog(),
        }

    @app.get("/api/sessions/{name}/formula-lab")
    async def formula_lab(name):
        from .formula_lab import FormulaLab

        return {"studies": FormulaLab(session(name)).catalog()}

    async def execute(run, body, job):
        events = LiveEvents(run, body.request_id)
        try:
            logs = (run / "requests" / f"{body.request_id}.log").open("wb")
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "factor_demo.web_worker",
                    "--run",
                    str(run),
                    "--task",
                    body.request_id,
                    cwd=project,
                    stdout=logs,
                    stderr=logs,
                    start_new_session=True,
                )
                job["process"] = proc
                write_json(
                    run / "active-mission.json",
                    {
                        "pid": proc.pid,
                        "process_created": psutil.Process(proc.pid).create_time(),
                        "task_id": body.request_id,
                        "agent_id": job.get("agent_id", "main"),
                    },
                )
                rc = await proc.wait()
            finally:
                logs.close()
            target = (
                run if job.get("agent_id", "main") == "main" else branch_path(run, job["agent_id"])
            )
            status = (
                json.loads((target / "status.json").read_text()).get("status")
                if (target / "status.json").exists()
                else "interrupted"
            )
            cancelled = (run / "requests" / f"{body.request_id}.cancel").exists() or (
                target / "requests" / f"{body.request_id}.cancel"
            ).exists()
            events.emit(
                "task.end",
                status="cancelled"
                if cancelled
                else "checkpoint"
                if rc == 0 and status == "checkpoint"
                else "interrupted",
                exit_code=rc,
            )
        except Exception as error:  # noqa: BLE001 - report worker failure without exposing provider exceptions
            events.emit(
                "task.error",
                error_type=type(error).__name__,
                message="研究进程未能完成，已有事件可回放。",
            )
            events.emit("task.end", status="interrupted")
        finally:
            jobs.pop(run.name, None)
            (run / "active-mission.json").unlink(missing_ok=True)
            events.close()

    async def watch_recovered(job):
        run = job["run"]
        events = LiveEvents(run, job["id"])
        try:
            await job["process"].wait()
            target = run if job["agent_id"] == "main" else branch_path(run, job["agent_id"])
            state = (
                json.loads((target / "status.json").read_text())
                if (target / "status.json").exists()
                else {}
            )
            cancelled = (run / "requests" / f"{job['id']}.cancel").exists() or (
                target / "requests" / f"{job['id']}.cancel"
            ).exists()
            events.emit(
                "task.end",
                status="cancelled"
                if cancelled
                else "checkpoint"
                if state.get("status") == "checkpoint" and state.get("mission") == job["id"]
                else "interrupted",
                recovered=True,
            )
        finally:
            jobs.pop(run.name, None)
            (run / "active-mission.json").unlink(missing_ok=True)
            events.close()

    async def recover_jobs():
        for marker in runs.glob("*/active-mission.json"):
            meta = {}
            run = marker.parent
            try:
                meta = json.loads(marker.read_text())
                proc = psutil.Process(meta["pid"])
                valid = (
                    abs(proc.create_time() - meta["process_created"]) < 0.01
                    and "factor_demo.web_worker" in proc.cmdline()
                    and str(run) in proc.cmdline()
                    and meta["task_id"] in proc.cmdline()
                )
                if not valid:
                    raise ValueError("Worker identity changed")
            except (ValueError, KeyError, psutil.Error):
                events = LiveEvents(run, meta.get("task_id", ""))
                events.emit("task.end", status="interrupted", recovered=True)
                events.close()
                marker.unlink(missing_ok=True)
                store = BranchStore(run)
                for row in store.list():
                    if row["state"] in {"queued", "starting", "running"}:
                        child = store.process(row)
                        if child:
                            local = branch_path(run, row["id"])
                            folder = local / "requests"
                            folder.mkdir(exist_ok=True)
                            (folder / f"{row['mission']}.cancel").touch()
                            descendants = child.children(recursive=True)
                            await asyncio.sleep(0.3)
                            if store.process(row):
                                for item in reversed(descendants):
                                    with contextlib.suppress(psutil.Error):
                                        item.kill()
                                with contextlib.suppress(psutil.Error):
                                    child.kill()
                        store.update(
                            row["id"],
                            state="interrupted",
                            error="Parent host ended; resume from saved files and snapshot",
                        )
                continue
            job = {
                "run": run,
                "id": meta["task_id"],
                "agent_id": meta.get("agent_id", "main"),
                "process": ReattachedProcess(proc),
                "children": [],
            }
            jobs[run.name] = job
            job["waiter"] = asyncio.create_task(watch_recovered(job))

    app.state.runner = execute

    @app.post("/api/sessions/{name}/messages")
    async def send(name, body: NewMessage):
        run = session(name)
        if body.mode not in {"research", "discuss"}:
            raise HTTPException(400, "Unknown mode")
        cfg = await config()
        if body.provider not in {p["id"] for p in cfg["providers"]}:
            raise HTTPException(400, "Unknown provider")
        async with locks.setdefault(name, asyncio.Lock()):
            events = LiveEvents(run, body.request_id)
            try:
                # Durable request ID prevents reconnect/retry from launching the same work twice.
                old = events.db.execute(
                    "SELECT task_id FROM requests WHERE request_id=?", (body.request_id,)
                ).fetchone()
                if old:
                    return {"task_id": old[0], "duplicate": True}
                job = jobs.get(name)
                if job and body.agent_id == "main" and job.get("agent_id", "main") != "main":
                    raise HTTPException(
                        409, "A saved branch is running; select that branch for guidance"
                    )
                target = None
                if body.agent_id != "main":
                    try:
                        target = BranchStore(run).get(body.agent_id)
                        branch_path(run, body.agent_id)
                    except ValueError:
                        raise HTTPException(404, "Unknown research branch") from None
                    if job and (
                        target["mission"] != job["id"]
                        or target["state"] not in {"queued", "starting", "running"}
                    ):
                        raise HTTPException(
                            409,
                            "This branch is inactive; continue it after the current mission finishes",
                        )
                events.agent_id = body.agent_id
                events.parent_agent_id = target["parent_id"] if target else ""
                with events.db:
                    events.db.execute(
                        "INSERT INTO requests(request_id,task_id) VALUES(?,?)",
                        (body.request_id, job["id"] if job else body.request_id),
                    )
                if job:
                    events.task_id = job["id"]
                    events.emit(
                        "user.message",
                        message=body.message,
                        guidance=True,
                        request_id=body.request_id,
                    )
                    events.add_guidance(body.message)
                    return {"task_id": job["id"], "steering": True}
                events.emit("user.message", message=body.message)
                start_events = LiveEvents(run, body.request_id)
                start_events.emit("task.start", mode=body.mode, target_agent_id=body.agent_id)
                start_events.close()
                folder = run / "requests"
                folder.mkdir(exist_ok=True)
                write_json(
                    folder / f"{body.request_id}.json",
                    {
                        "message": body.message,
                        "agent_id": body.agent_id,
                        "provider": body.provider,
                        "mode": "chat" if body.mode == "discuss" else "research",
                    },
                )
                job = {
                    "run": run,
                    "id": body.request_id,
                    "process": None,
                    "children": [],
                    "agent_id": body.agent_id,
                }
                jobs[name] = job
                job["waiter"] = asyncio.create_task(app.state.runner(run, body, job))
                return {"task_id": body.request_id, "steering": False}
            finally:
                events.close()

    @app.post("/api/sessions/{name}/stop")
    async def stop(name):
        run = session(name)
        job = jobs.get(name)
        if job:
            e = LiveEvents(run, job["id"])
            e.emit("task.stopping")
            e.close()
            asyncio.create_task(stop_job(job))
        return {"stopping": bool(job)}

    @app.get("/api/sessions/{name}/branches")
    async def branches(name):
        run = session(name)
        store = BranchStore(run)
        if name not in jobs:
            store.reconcile()
        return [
            {k: v for k, v in row.items() if k not in {"pid", "process_created"}}
            for row in store.list()
        ]

    @app.post("/api/sessions/{name}/branches/{identity}/stop")
    async def stop_branch(name, identity):
        run = session(name)
        store = BranchStore(run)
        try:
            row = store.get(identity)
            local = branch_path(run, identity)
        except ValueError:
            raise HTTPException(404, "Unknown research branch") from None
        folder = local / "requests"
        folder.mkdir(exist_ok=True)
        (folder / f"{row['mission']}.cancel").touch()
        if row["state"] == "queued":
            store.update(identity, state="cancelled", error="Cancelled before dispatch")
        events = LiveEvents(
            local, row["mission"], root=run, agent_id=identity, parent_agent_id=row["parent_id"]
        )
        if row["state"] == "queued":
            events.emit("branch.state", branch=store.get(identity))
        else:
            events.emit("branch.stopping", agent_id=identity)
        events.close()
        return {"stopping": row["state"] in {"queued", "starting", "running"}}

    @app.get("/api/sessions/{name}/events")
    async def event_stream(name, request: Request, after: int = 0):
        run = session(name)
        try:
            header_cursor = int(request.headers.get("last-event-id", "0"))
            if not 0 <= after < 2**63 or not 0 <= header_cursor < 2**63:
                raise ValueError("Invalid event cursor")
            cursor = max(after, header_cursor)
        except ValueError:
            raise HTTPException(400, "Invalid event cursor") from None
        if not 0 <= cursor < 2**63:
            raise HTTPException(400, "Invalid event cursor")

        async def generate():
            nonlocal cursor
            events = LiveEvents(run)
            last = time.monotonic()
            try:
                yield "retry: 1000\n\n"
                while not await request.is_disconnected():
                    rows = events.since(cursor)
                    for row in rows:
                        cursor = row["id"]
                        yield f"id: {cursor}\nevent: research\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                    if not rows:
                        if time.monotonic() - last > 10:
                            yield ": heartbeat\n\n"
                            last = time.monotonic()
                        await asyncio.sleep(0.05)
            finally:
                events.close()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/sessions/{name}/artifacts/{path:path}")
    async def artifact(name, path):
        try:
            target = public_artifact_path(session(name), path)
        except ValueError:
            raise HTTPException(404, "Artifact not found") from None
        images = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }
        if target.suffix.lower() in images:
            return FileResponse(target, media_type=images[target.suffix.lower()])
        if target.suffix.lower() in {".joblib", ".parquet"}:
            return FileResponse(target, media_type="application/octet-stream", filename=target.name)
        if target.suffix.lower() not in {".csv", ".json", ".md", ".txt", ".py", ".parquet"}:
            raise HTTPException(400, "Unsupported artifact type")
        return FileResponse(target, media_type="text/plain; charset=utf-8", filename=target.name)

    dist = project / "frontend/dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


def serve(host="127.0.0.1", port=8765):
    import uvicorn

    uvicorn.run(create_app(), host=host, port=port, timeout_graceful_shutdown=3)
