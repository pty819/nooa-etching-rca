"""HTTP-host recovery must observe the same worker and retire stale identities."""

import asyncio
import json
import subprocess
import sys

import psutil
from test_capabilities import public_case

from factor_demo.live_events import LiveEvents
from factor_demo.research_branches import BranchStore, prepare_branch
from factor_demo.web import create_app


def test_reading_saved_reports_never_imports_the_model_evaluation_engine(tmp_path):
    from factor_demo.study_store import StudyStore

    run = public_case(tmp_path / "runs/case")
    body = {
        "id": "study-aabbccddeeff",
        "owner": "main",
        "created": 0,
        "plan": {"title": "saved comparison"},
        "formulas": [],
        "sets": [],
        "trials": [],
        "selections": [],
        "selection_id": None,
        "revision": 1,
        "limits": "fixture",
        "internal_only": "must not reach the browser",
    }
    store = StudyStore(run / ".formula-lab")
    with store.transaction() as db:
        store.save(db, body)
    source = """
import asyncio,sys
from pathlib import Path
import httpx
class BlockScientificEngine:
    def find_spec(self, fullname, *args):
        if fullname == 'factor_demo.formula_lab' or fullname.split('.')[0] == 'sklearn':
            raise AssertionError('Viewing a saved report imported the numerical engine: '+fullname)
sys.meta_path.insert(0,BlockScientificEngine())
from factor_demo.web import create_app
app=create_app(Path(sys.argv[1]))
async def exercise():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as client:
        detail=await client.get('/api/sessions/case')
        assert detail.status_code == 200,detail.text
        listing=await client.get('/api/sessions/case/formula-lab')
        assert listing.status_code == 200,listing.text
        studies=listing.json()['studies']
        assert studies == detail.json()['formula_studies']
        assert studies[0]['plan']['title'] == 'saved comparison'
        assert studies[0]['replays'] == []
        assert 'internal_only' not in studies[0]
asyncio.run(exercise())
"""
    result = subprocess.run(
        [sys.executable, "-c", source, str(tmp_path)],
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr


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
