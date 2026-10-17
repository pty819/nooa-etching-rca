import asyncio
import json

import pytest
from test_capabilities import public_case

from factor_demo.budget import BudgetExceeded, MissionBudget, TokenLedger
from factor_demo.capabilities import Capabilities
from factor_demo.live_events import LiveEvents, branch_path, public_artifact_path
from factor_demo.research_branches import BranchStore, prepare_branch


def test_conservative_ledger_and_abandoned_process(tmp_path):
    ledger = TokenLedger(tmp_path, "one", 1000)
    key = ledger.reserve(600, "child", "research")
    with pytest.raises(BudgetExceeded):
        ledger.reserve(500, "main", "reply")
    ledger.abandon_agent("child")
    state = ledger.snapshot()
    assert (
        state["reserved_tokens"] == 0
        and state["charged_tokens"] == 600
        and state["estimated_calls"] == 1
    )
    other = TokenLedger(tmp_path, "next", 1000)
    assert other.snapshot()["charged_tokens"] == 0
    key = other.reserve(100, "main", "reply")
    other.settle(key, dispatched=False)
    assert other.snapshot()["charged_tokens"] == 0


def test_root_and_child_ledger_connections_share_native_broker(tmp_path):
    root = public_case(tmp_path / "case")
    policy = Capabilities(max_model_calls=2, max_model_in_flight=1)
    parent = MissionBudget(root, "mission", policy)
    child = MissionBudget(root, "mission", policy, parent.path)
    try:
        assert parent.config.port == child.config.port
        assert parent.config.auth_token == child.config.auth_token
        assert parent.path.stat().st_mode & 0o777 == 0o600
        child.ledger.reserve(200, "child", "summary")
        assert parent.ledger.snapshot()["reserved_tokens"] == 200
        parent.ledger.abandon_agent("child")
        assert child.ledger.snapshot()["estimated_calls"] == 1
    finally:
        child.close()
        parent.close()
    assert not parent.path.exists()


def test_real_sdk_retry_is_admitted_and_unknown_attempt_usage_is_charged(tmp_path, monkeypatch):
    import httpx

    from factor_demo.providers import Provider
    from factor_demo.runtime import RuntimeSettings, build_client

    attempts = []

    async def send(client, request, **kwargs):
        attempts.append(json.loads(request.content))
        if len(attempts) == 1:
            raise ConnectionError("fixture disconnected after dispatch")
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "retry",
                "object": "chat.completion",
                "created": 0,
                "model": "glm-5.3-flash",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    root = public_case(tmp_path / "case")
    events = LiveEvents(root, "retry")
    budget = MissionBudget(root, "retry", Capabilities(max_model_calls=2))

    async def exercise():
        client = budget.client(
            build_client(
                Provider("glm", "glm-5.3-flash", "https://wire.invalid/v1", "openai", "test-only"),
                RuntimeSettings(),
            ),
            events,
            RuntimeSettings(),
        )
        try:
            await client.acall([{"role": "user", "content": "Protocol test"}])
            state = budget.snapshot()
            assert state["charged_tokens"] == 272000 + 120
            assert state["logical_calls"] == 1 and state["admitted_calls"] == 2
            assert state["estimated_calls"] == 1 and state["reserved_tokens"] == 0
            from nooa.unifiedllm.admission import AdmissionCallCapError

            with pytest.raises(AdmissionCallCapError):
                await client.acall([{"role": "user", "content": "No capacity"}])
            assert budget.snapshot()["charged_tokens"] == 272120
            assert len(attempts) == 2
        finally:
            await client.aclose()

    try:
        asyncio.run(exercise())
    finally:
        events.close()
        budget.close()


def test_branch_input_is_shared_but_workspace_and_evidence_are_scoped(tmp_path):
    root = public_case(tmp_path / "case")
    (root / "workspace/artifacts/source.csv").write_text("x\n1\n")
    branch, seeds = prepare_branch(
        root, "research-a", "main", "inspect", "A", ["artifacts/source.csv"]
    )
    assert branch_path(root, "research-a") == branch
    assert (branch / "input").resolve() == (root / "input").resolve()
    assert seeds[0]["path"] == "artifacts/seed/00-source.csv"
    target = public_artifact_path(root, "branches/research-a/artifacts/seed/00-source.csv")
    assert target.read_text() == "x\n1\n"
    outside = tmp_path / "secret.txt"
    outside.write_text("private")
    (branch / "workspace/artifacts/escape.txt").symlink_to(outside)
    for name in [
        "branches/research-a/artifacts/escape.txt",
        "branches/../artifacts/source.csv",
        "branches/research-a/../../input/runs.parquet",
    ]:
        with pytest.raises(ValueError):
            public_artifact_path(root, name)
    events = LiveEvents(branch, "mission", root=root, agent_id="research-a", parent_agent_id="main")
    events.add_guidance("scope check", source="parent:main")
    assert events.take_guidance_records() == [{"message": "scope check", "source": "parent:main"}]
    events.emit("tool.output", cell=1, text="branch output")
    events.close()
    root_events = LiveEvents(root)
    row = root_events.since(0)[0]
    root_events.close()
    assert row["agent_id"] == "research-a" and row["parent_agent_id"] == "main"
    store = BranchStore(root)
    store.create("research-a", "main", "mission", "A", "inspect")
    store.update("research-a", state="running", pid=999999, process_created=0)
    store.reconcile()
    assert store.get("research-a")["state"] == "interrupted"


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="macOS worker policy")
def test_branch_worker_cannot_read_sibling_or_parent_even_under_tmp(tmp_path):
    from factor_demo.research_workspace import PythonWorkspace

    root = public_case(tmp_path / "case")
    (root / "workspace/artifacts/parent-secret.txt").write_text("DENIED_DATA")
    branch, _ = prepare_branch(root, "research-a", "main", "isolation", "A", [])

    async def exercise():
        worker = PythonWorkspace(branch)
        try:
            result = await worker.execute(
                "from pathlib import Path\nimport polars as pl\nprint(pl.read_parquet('../input/runs.parquet').height)\ntry: Path("
                + repr(str(root / "workspace/artifacts/parent-secret.txt"))
                + ").read_text()\nexcept PermissionError: print('PARENT_DENIED')\nelse: raise AssertionError('parent became readable')"
            )
            assert result["ok"] and "PARENT_DENIED" in result["output"]
        finally:
            await worker.close()

    asyncio.run(exercise())
