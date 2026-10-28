"""Regression tests for failures reproduced in the second audit batch."""

import asyncio
import json

import httpx
import pytest
from nooa.unifiedllm import FakeLLMClient
from test_capabilities import public_case
from test_orchestration import endpoint as endpoint  # noqa: PLC0414 - fixture registration
from test_orchestration import settings

from factor_demo.autoresearch import research_turn
from factor_demo.autoresearch_agent import AutoresearchAgent
from factor_demo.live_events import LiveEvents
from factor_demo.research_workspace import PythonWorkspace
from factor_demo.web import create_app


def test_audit_failure_does_not_replace_original_exception(endpoint, tmp_path, monkeypatch):
    endpoint()
    root = public_case(tmp_path / "case")

    class OriginalError(RuntimeError):
        pass

    def audit_failure(*a):
        raise ValueError("audit failure")

    async def original_failure(*a):
        raise OriginalError("original failure")

    monkeypatch.setattr("factor_demo.research_audit.audit_session", audit_failure)
    events = LiveEvents(root, "audit-failure")
    try:
        with pytest.raises(OriginalError):
            asyncio.run(
                research_turn(
                    root,
                    "custom",
                    "probe",
                    settings_path=settings(tmp_path),
                    events=events,
                    on_agent=original_failure,
                )
            )
        assert json.loads((root / "status.json").read_text())["error_type"] == "OriginalError"
        assert (
            json.loads((root / "rounds/round_001/audit-error.json").read_text())["error_type"]
            == "ValueError"
        )
        assert any(e["type"] == "audit.warning" for e in events.since(0, 1000))
    finally:
        events.close()


@pytest.mark.parametrize("exit_code, expected", [(3, 3), (0, 0), (None, 0), ("failed", 1)])
def test_script_exit_is_structured_and_printed_text_cannot_change_it(tmp_path, exit_code, expected):
    root = public_case(tmp_path / "case")

    async def run():
        worker = PythonWorkspace(root)
        agent = AutoresearchAgent(worker, llm=FakeLLMClient())
        try:
            await agent.write_file("artifacts/fail.py", f"raise SystemExit({exit_code!r})")
            result = await agent.run_script("artifacts/fail.py")
            assert result["returncode"] == expected
            assert result["ok"] == (expected == 0)
            await agent.run_code("print('RC: 1')")
            rows = [json.loads(x) for x in (root / "execution.jsonl").read_text().splitlines()]
            failures = [
                r for r in rows if r["kind"] == "script" and r["returncode"] not in (None, 0)
            ]
            assert len(failures) == (0 if expected == 0 else 1)
            if failures:
                assert failures[0]["returncode"] == expected
        finally:
            await worker.close()
            await agent.aclose()

    asyncio.run(run())


def test_local_host_check_rejects_unexpected_host_even_with_matching_origin(tmp_path):
    app = create_app(tmp_path)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://untrusted.example:8765"
        ) as client:
            assert (await client.get("/api/config")).status_code == 400
            response = await client.post(
                "/api/sessions",
                json={"dataset": "none"},
                headers={"Origin": "http://untrusted.example:8765", "X-Research-Client": "1"},
            )
            assert response.status_code == 400 and "local application" in response.text
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8765"
        ) as client:
            assert (await client.get("/api/config")).status_code == 200

    asyncio.run(run())
