"""HTTP-host recovery must observe the same worker and retire stale identities."""

import asyncio
import json
import sys

import psutil
from test_capabilities import public_case

from factor_demo.live_events import LiveEvents
from factor_demo.research_branches import BranchStore, prepare_branch
from factor_demo.web import create_app


def test_server_reconnects_surviving_worker_without_new_research(tmp_path):
    run = public_case(tmp_path / "runs/case")
    (run / "requests").mkdir()
    app = create_app(tmp_path)

    async def exercise():
        source = "import time,json,sys;from pathlib import Path;time.sleep(.6);(Path(sys.argv[2])/'status.json').write_text(json.dumps({'status':'checkpoint','mission':'saved-mission'}))"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", source, "factor_demo.web_worker", str(run), "saved-mission"
        )
        (run / "active-mission.json").write_text(
            json.dumps(
                {
                    "pid": proc.pid,
                    "process_created": psutil.Process(proc.pid).create_time(),
                    "task_id": "saved-mission",
                    "agent_id": "main",
                }
            )
        )
        try:
            async with app.router.lifespan_context(app):
                assert app.state.jobs["case"]["process"].pid == proc.pid
                await proc.wait()
                async with asyncio.timeout(3):
                    while app.state.jobs:
                        await asyncio.sleep(0.02)
                assert not (run / "active-mission.json").exists()
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()

    asyncio.run(exercise())
    events = LiveEvents(run)
    try:
        endings = [e for e in events.since(0) if e["type"] == "task.end"]
        assert len(endings) == 1
        assert endings[0]["data"] == {"status": "checkpoint", "recovered": True}
    finally:
        events.close()


def test_stale_server_marker_marks_orphan_branch_interrupted(tmp_path):
    run = public_case(tmp_path / "runs/case")
    prepare_branch(run, "research-a", "main", "recover", "A", [])
    store = BranchStore(run)
    store.create("research-a", "main", "old", "A", "recover")
    (run / "active-mission.json").write_text(
        json.dumps({"pid": 999999999, "process_created": 0, "task_id": "old"})
    )
    app = create_app(tmp_path)

    async def exercise():
        async with app.router.lifespan_context(app):
            assert not app.state.jobs
            assert store.get("research-a")["state"] == "interrupted"
            assert not (run / "active-mission.json").exists()

    asyncio.run(exercise())
