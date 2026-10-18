import asyncio
import json
import sys
from pathlib import Path

import httpx
import polars as pl
import pytest

from factor_demo.providers import Provider
from factor_demo.research_workspace import PythonWorkspace, prepare_workspace
from factor_demo.runtime import RuntimeSettings, build_client


@pytest.mark.skipif(
    sys.platform not in {"darwin", "linux"}, reason="enforced execution backend required"
)
def test_free_code_persists_and_fails_recoverably_with_real_os_boundaries(tmp_path, monkeypatch):
    run = tmp_path / "run"
    work = run / "workspace"
    work.mkdir(parents=True)
    (run / "input").mkdir()
    import factor_demo.research_worker

    (work / "_worker.py").write_text(Path(factor_demo.research_worker.__file__).read_text())
    # This secret is placed in the denied user tree, not an allowed system temp path.
    secret = Path(__file__).parent / ".worker-test-secret"
    secret.write_text("unit-test-only")
    monkeypatch.setenv("DEMO_PRIVATE_TEST_SECRET", "not-for-worker")

    async def exercise():
        worker = PythonWorkspace(run)
        try:
            first = await worker.execute(
                "import numpy as np, polars as pl\nfrom pathlib import Path\n"
                "x=np.arange(5)\nPath('own.py').write_text('print(123)')\nprint(x.sum())"
            )
            assert first["ok"] and "10" in first["output"]
            assert (await worker.execute("print(x.mean())"))["output"].strip() == "2.0"
            from factor_demo.autoresearch_agent import AutoresearchAgent

            provider = Provider(
                "test", "glm-5.3-flash", "https://wire.invalid/v1", "openai", "fake"
            )
            agent = AutoresearchAgent(worker, llm=build_client(provider, RuntimeSettings()))
            source = '"""Nested docstring"""\ntext = "\'\'\'"\n'
            try:
                assert (await agent.write_file("artifacts/quoted.py", source))["ok"]
                assert (work / "artifacts/quoted.py").read_text() == source
                await agent.write_file(
                    "artifacts/exit.py", "print('SUBPROCESS'); raise SystemExit(3)"
                )
                failed = await agent.run_script("artifacts/exit.py")
                assert not failed["ok"] and "SUBPROCESS" in failed["output"]
                await agent.write_file("artifacts/ok.py", "print('SCRIPT_OK'); raise SystemExit(0)")
                assert (await agent.run_script("artifacts/ok.py"))["ok"]
                worker.research_enabled = False
                with pytest.raises(PermissionError):
                    await agent.run_script("artifacts/ok.py")
                with pytest.raises(PermissionError):
                    await worker.execute("print('must not run')")
                worker.research_enabled = True
                with pytest.raises(ValueError, match="within the research workspace"):
                    await agent.write_file("../outside.py", "not allowed")
            finally:
                await agent.aclose()
            assert not (await worker.execute("raise ValueError('scientific failure')"))["ok"]
            assert (await worker.execute("print(x.size)"))["output"].strip() == "5"
            code = (
                f"try:\n Path({str(secret)!r}).read_text()\n"
                "except PermissionError: print('SECRET_DENIED')\n"
                "else: raise AssertionError('secret became readable')\n"
                "import os, socket\nassert 'DEMO_PRIVATE_TEST_SECRET' not in os.environ\n"
                "s=None\ntry:\n s=socket.socket(); s.connect(('127.0.0.1',9))\n"
                "except PermissionError: print('NETWORK_DENIED')\n"
                "finally:\n if s is not None: s.close()"
            )
            result = await worker.execute(code)
            assert result["ok"] and "SECRET_DENIED" in result["output"], result["output"]
            assert "NETWORK_DENIED" in result["output"]
            timed = await worker.execute("while True: pass", timeout_s=1)
            assert not timed["ok"] and "timeout" in timed["output"]
            recovered = await worker.execute(
                "from pathlib import Path\nprint('x' in globals()); print(Path('own.py').exists())"
            )
            assert recovered["output"].strip().endswith("True")
            assert "False" in recovered["output"]
        finally:
            await worker.close()

    try:
        asyncio.run(exercise())
    finally:
        secret.unlink(missing_ok=True)


def test_discovery_export_really_excludes_test_lots(tmp_path):
    # Use the already validated local demo as a read-only integration fixture.
    source = Path("data/etching-v2")
    if not source.exists():
        pytest.skip("generated etching fixture absent")
    run = tmp_path / "session"
    prepare_workspace(source, run)
    meta = pl.read_parquet(run / "input/runs.parquet")
    assert set(meta["split"]) == {"train", "dev"}
    assert meta.height == 90
    ids = set(meta["run_id"])
    assert {p.stem for p in (run / "input/traces").glob("*.parquet")} == ids
    for file in ["features", "control_features", "steps", "outcomes"]:
        assert set(pl.read_parquet(run / f"input/{file}.parquet")["run_id"]) <= ids
    assert not (run / "input/truth.private").exists()
    assert not (run / "input/test.parquet").exists()


def test_nooa_history_survives_restart_and_discuss_has_no_code_tool(tmp_path, monkeypatch):
    captured = []

    async def send(client, request, **kwargs):
        assert request.url.host == "wire.invalid"
        body = json.loads(request.content)
        captured.append(body)
        payload = {
            "summary": "REMEMBER_EVIDENCE_314",
            "hypotheses": [],
            "artifacts": [],
            "next_experiments": [],
            "questions_for_expert": [],
        }
        if body.get("tools"):
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"cell-{len(captured)}",
                        "type": "function",
                        "function": {
                            "name": "python_cell",
                            "arguments": json.dumps({"code": f"return_result({payload!r})"}),
                        },
                    }
                ],
            }
            finish = "tool_calls"
        else:
            message = {"role": "assistant", "content": json.dumps(payload)}
            finish = "stop"
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "glm-5.3-flash",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)

    async def exercise():
        from nooa.storage.sqlite import SQLiteStorageManager

        from factor_demo.autoresearch_agent import AutoresearchAgent

        provider = Provider("test", "glm-5.3-flash", "https://wire.invalid/v1", "openai", "fake")
        path = tmp_path / "conversation.sqlite"
        for index in [0, 1]:
            storage = SQLiteStorageManager(path)
            agent = AutoresearchAgent(
                None, llm=build_client(provider, RuntimeSettings()), storage=storage
            )
            if index:
                assert storage.restore_latest_snapshot(agent)
                assert agent.round_number == 1
                reply = await agent.discuss(
                    "Discuss the earlier evidence without another experiment"
                )
            else:
                agent.round_number = 1
                reply = await agent.research("Remember this measured-evidence checkpoint")
            assert reply.summary == "REMEMBER_EVIDENCE_314"
            storage.save_snapshot(agent)
            await agent.aclose()
            storage.close()

    asyncio.run(exercise())
    assert len(captured) == 2
    assert [t["function"]["name"] for t in captured[0]["tools"]] == ["python_cell"]
    assert not captured[1].get("tools")
    assert "REMEMBER_EVIDENCE_314" in json.dumps(captured[1]["messages"])
