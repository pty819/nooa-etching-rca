"""Real HTTP/NOOA/worker/process integration, without paid provider calls."""

import asyncio
import json
import socket
import threading
import time
from collections import Counter

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from test_capabilities import public_case

from factor_demo.autoresearch import research_turn
from factor_demo.capabilities import Capabilities
from factor_demo.live_events import LiveEvents, public_artifact_path
from factor_demo.research_branches import BranchStore
from factor_demo.runtime import RuntimeSettings


class FakeProvider:
    def __init__(
        self,
        parallel=False,
        revision=False,
        fail_branch=False,
        cancel_branch=False,
        invalid_review=False,
    ):
        self.invalid_review = invalid_review
        self.fail_branch = fail_branch
        self.cancel_branch = cancel_branch
        self.failed_identity = None
        self.parallel = parallel
        self.revision = revision
        self.calls = []
        self.tools = Counter()
        self.reviews = Counter()
        self.active = 0
        self.peak = 0
        self.app = FastAPI()
        self.app.post("/v1/chat/completions")(self.complete)

    async def complete(self, request: Request):
        body = await request.json()
        identity = request.headers.get("x-research-agent", "main")
        self.calls.append((identity, body))
        self.active += 1
        self.peak = max(self.peak, self.active)
        is_tool = bool(body.get("tools"))
        schema = body.get("response_format", {}).get("json_schema", {}).get("name", "")
        if is_tool:
            index = self.tools[identity]
            self.tools[identity] += 1
            if self.parallel and identity == "main":
                code = "a=await self.spawn_research('DIRECTION_A','A')\nb=await self.spawn_research('DIRECTION_B','B')\nprint(await self.guide_research(a['id'],'GUIDANCE_PROOF'))\nresults=await self.wait_research([a['id'],b['id']],timeout_s=60)\nprint(results)\nreturn_result({'summary':'两个独立方向完成，等待人工解释。','hypotheses':[],'evidence':[],'artifacts':[]})"
            else:
                value = 6 if index else 4
                if self.parallel and index == 0:
                    value = 4 if len(self.tools) % 2 else 6
                candidate = {
                    "summary": "公开数据的数值检查，不能推断根因。",
                    "hypotheses": [],
                    "artifacts": ["artifacts/evidence.csv"],
                    "evidence": [
                        {
                            "id": "e1",
                            "claim": "r1 value",
                            "artifact": "artifacts/evidence.csv",
                            "column": "value",
                            "where": {"run_id": "r1"},
                            "value": value,
                            "unit": "V",
                            "run_ids": ["r1"],
                        }
                    ],
                }
                code = f"print(await self.write_file('artifacts/evidence.csv', 'run_id,value\\nr1,{value}\\n'))\nprint(await self.run_code(\"import polars as pl, time\\nprint(pl.read_parquet('../input/runs.parquet').shape)\\ntime.sleep(.15)\\nprint('WORKER_DONE')\"))\nreturn_result({candidate!r})"
            if self.cancel_branch and identity == "main":
                code = code.replace(
                    "results=await", "print(await self.cancel_research(a['id']))\nresults=await"
                )
            if self.fail_branch and identity != "main":
                self.failed_identity = self.failed_identity or identity
                if identity == self.failed_identity:
                    code = "raise RuntimeError('isolated fixture failure')"
            delta = {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"call-{identity}-{index}",
                        "type": "function",
                        "function": {
                            "name": "python_cell",
                            "arguments": json.dumps({"code": code}),
                        },
                    }
                ]
            }
            finish = "tool_calls"
            text = None
        elif schema:
            self.reviews[identity] += 1
            rejected = (
                (self.revision or self.invalid_review)
                and identity == "main"
                and self.reviews[identity] == 1
            )
            text = json.dumps(
                {
                    "issues": [
                        {
                            "category": "evidence",
                            "severity": "blocking",
                            "claim_id": "e1/e2" if self.invalid_review else "e1",
                            "evidence_ids": ["e1"],
                            "problem": "Add the requested independent check",
                            "counterevidence": "fixture controlled review",
                            "suggested_check": "Revise the evidence",
                        }
                    ]
                    if rejected
                    else [],
                    "strengths": ["saved evidence is traceable"],
                    "limitations": ["not a causal certification"],
                }
            )
            delta = None
            finish = "stop"
        else:
            text = "本轮证据已保存，结论仍需设备专家核实。"
            delta = None
            finish = "stop"

        async def stream():
            def frame(d, stop=None):
                return (
                    "data: "
                    + json.dumps(
                        {
                            "id": "fixture",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": "glm-5.3-flash",
                            "choices": [{"index": 0, "delta": d, "finish_reason": stop}],
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )

            try:
                yield frame({"role": "assistant", "reasoning_content": "核对公开证据"})
                if identity != "main":
                    await asyncio.sleep(0.4)
                if delta:
                    yield frame(delta)
                else:
                    for start in range(0, len(text), 12):
                        yield frame({"content": text[start : start + 12]})
                        await asyncio.sleep(0.002)
                yield frame({}, finish)
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "id": "fixture",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": "glm-5.3-flash",
                            "choices": [],
                            "usage": {
                                "prompt_tokens": 100,
                                "completion_tokens": 40,
                                "total_tokens": 140,
                            },
                        }
                    )
                    + "\n\n"
                )
                yield "data: [DONE]\n\n"
            finally:
                self.active -= 1

        return StreamingResponse(stream(), media_type="text/event-stream")


@pytest.fixture
def endpoint(monkeypatch):
    running = []

    def create(
        parallel=False, revision=False, fail_branch=False, cancel_branch=False, invalid_review=False
    ):
        fake = FakeProvider(parallel, revision, fail_branch, cancel_branch, invalid_review)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(fake.app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        monkeypatch.setenv("FACTOR_MODEL", "glm-5.3-flash")
        monkeypatch.setenv("FACTOR_API_BASE", f"http://127.0.0.1:{port}/v1")
        monkeypatch.setenv("FACTOR_API_STYLE", "openai")
        monkeypatch.setenv("FACTOR_API_KEY", "fake-test-only")
        running.append((server, thread))
        return fake

    yield create
    for server, thread in running:
        server.should_exit = True
        thread.join(8)


def settings(tmp_path):
    file = tmp_path / "runtime.json"
    file.write_text(RuntimeSettings().model_dump_json())
    return file


def test_review_revises_and_keeps_immutable_evidence(endpoint, tmp_path):
    fake = endpoint(revision=True)
    run = public_case(tmp_path / "case")
    events = LiveEvents(run, "revision")
    try:
        result = asyncio.run(
            research_turn(
                run,
                "custom",
                "Verify fixture evidence",
                settings_path=settings(tmp_path),
                events=events,
                live=True,
                policy=Capabilities(max_revisions=1),
            )
        )
    finally:
        events.close()
    assert result["review_status"] == "passed" and result["revision"] == 1
    base = run / "rounds/round_001/revisions"
    assert (base / "attempt_00/artifacts/evidence.csv").read_text().endswith("r1,4\n")
    assert (base / "attempt_01/artifacts/evidence.csv").read_text().endswith("r1,6\n")
    assert fake.reviews["main"] == 2
    budget = json.loads((run / "rounds/round_001/budget.json").read_text())
    assert (
        budget["peak_active"] <= 2
        and budget["charged_tokens"] > 0
        and budget["reserved_tokens"] == 0
    )
    assert any(not body.get("tools") and not body.get("response_format") for _, body in fake.calls)
    assert "model.start" in {e["type"] for e in LiveEvents(run).since(0, 10000)}


def test_two_real_branch_processes_share_budget_and_keep_state_separate(endpoint, tmp_path):
    fake = endpoint(parallel=True)
    run = public_case(tmp_path / "case")
    events = LiveEvents(run, "parallel")
    try:
        result = asyncio.run(
            research_turn(
                run,
                "custom",
                "Run two independent fixture directions",
                settings_path=settings(tmp_path),
                events=events,
                live=True,
                policy=Capabilities(max_revisions=0),
            )
        )
    finally:
        events.close()
    store = BranchStore(run)
    rows = store.list()
    assert result["review_status"] == "passed"
    assert len(rows) == 2 and all(row["state"] == "completed" for row in rows)
    assert all(row["review_status"] == "passed" for row in rows)
    assert all((run / "branches" / row["id"] / "conversation.sqlite").is_file() for row in rows)
    assert all((run / "branches" / row["id"] / "execution.jsonl").is_file() for row in rows)
    assert set(fake.tools) == {"main", *(row["id"] for row in rows)}
    assert fake.peak == 2
    values = []
    for row in rows:
        assert store.process(row) is None
        path = public_artifact_path(run, f"branches/{row['id']}/artifacts/evidence.csv")
        values.append(path.read_text())
        assert (run / "branches" / row["id"] / "input").resolve() == (run / "input").resolve()
    assert values[0] and values[1]
    events = LiveEvents(run)
    entries = events.since(0, 100000)
    events.close()
    assert {e["agent_id"] for e in entries} == {"main", *(row["id"] for row in rows)}
    assert any(
        e["type"] == "reply.commit" and "/branches/research-" in e["data"]["markdown"]
        for e in entries
    )
    budget = json.loads((run / "rounds/round_001/budget.json").read_text())
    assert budget["peak_active"] == 2 and budget["admitted_calls"] == len(fake.calls)


@pytest.mark.parametrize("behavior", ["failure", "cancellation"])
def test_one_branch_can_fail_or_be_cancelled_without_destroying_sibling(
    endpoint, tmp_path, behavior
):
    endpoint(
        parallel=True, fail_branch=behavior == "failure", cancel_branch=behavior == "cancellation"
    )
    run = public_case(tmp_path / "case")
    events = LiveEvents(run, "isolated-" + behavior)
    try:
        result = asyncio.run(
            research_turn(
                run,
                "custom",
                "Independent directions with one isolated failure",
                settings_path=settings(tmp_path),
                events=events,
                live=True,
                policy=Capabilities(max_revisions=0),
            )
        )
    finally:
        events.close()
    rows = BranchStore(run).list()
    assert len(rows) == 2
    assert sorted(r["state"] for r in rows) == (
        ["completed", "failed"] if behavior == "failure" else ["cancelled", "completed"]
    )
    assert result["review_status"] == "passed"
    assert all(BranchStore(run).process(r) is None for r in rows)
    entries = LiveEvents(run).since(0, 100000)
    assert (
        any(
            e["type"] == "steering.applied" and e["data"].get("source") == "parent:main"
            for e in entries
        )
        or behavior == "cancellation"
    )


def test_call_cap_preserves_checked_checkpoint_with_explicit_unreviewed_status(endpoint, tmp_path):
    fake = endpoint()
    run = public_case(tmp_path / "case")
    events = LiveEvents(run, "call-cap")
    try:
        result = asyncio.run(
            research_turn(
                run,
                "custom",
                "Budget fixture",
                settings_path=settings(tmp_path),
                events=events,
                live=True,
                policy=Capabilities(max_model_calls=1, max_revisions=0),
            )
        )
    finally:
        events.close()
    assert len(fake.calls) == 1 and result["review_status"] == "budget_exhausted"
    assert json.loads((run / "status.json").read_text())["status"] == "checkpoint"
    assert json.loads((run / "latest_review.json").read_text())["reason"] == "shared_model_budget"
    entries = LiveEvents(run).since(0, 10000)
    assert any(
        e["type"] == "reply.commit" and e["data"]["review_status"] == "budget_exhausted"
        for e in entries
    )
    assert any(e["type"] == "budget.delivery_fallback" for e in entries)


def test_budget_before_first_report_delivers_honest_checkpoint(endpoint, tmp_path):
    fake = endpoint()
    run = public_case(tmp_path / "case")
    events = LiveEvents(run, "too-small")
    try:
        result = asyncio.run(
            research_turn(
                run,
                "custom",
                "Budget fixture",
                settings_path=settings(tmp_path),
                events=events,
                live=True,
                policy=Capabilities(max_total_tokens=1000),
            )
        )
    finally:
        events.close()
    assert not fake.calls
    assert result["review_status"] == "budget_exhausted" and not result["evidence"]
    assert "尚未形成通过审查的主报告" in result["summary"]
    assert json.loads((run / "status.json").read_text())["status"] == "checkpoint"


def test_invalid_reviewer_reference_is_repaired_without_scientist_rework(endpoint, tmp_path):
    fake = endpoint(invalid_review=True)
    run = public_case(tmp_path / "case")
    events = LiveEvents(run, "invalid-review")
    try:
        result = asyncio.run(
            research_turn(
                run,
                "custom",
                "Reference fixture",
                settings_path=settings(tmp_path),
                events=events,
                live=True,
            )
        )
    finally:
        events.close()
    assert result["review_status"] == "passed" and result["revision"] == 0
    assert fake.tools["main"] == 1 and fake.reviews["main"] == 2
    saved = run / "rounds/round_001/revisions/attempt_00"
    assert json.loads((saved / "review.json").read_text())["issues"][0]["claim_id"] == "e1/e2"
    assert json.loads((saved / "review_repaired.json").read_text())["issues"] == []


def test_http_branch_resume_restores_history_without_replaying_the_parent(endpoint, tmp_path):
    import httpx

    from factor_demo.web import create_app

    fake = endpoint(parallel=True)
    run = public_case(tmp_path / "runs/case")
    (run / "ui.json").write_text('{"title":"API branch recovery"}')
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/runtime.json").write_text(RuntimeSettings().model_dump_json())
    app = create_app(tmp_path)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", headers={"X-Research-Client": "1"}, timeout=10
        ) as c:
            body = {
                "message": "Parallel API fixture",
                "provider": "custom",
                "request_id": "mission-one",
            }
            assert c.post("/api/sessions/case/messages", json=body).status_code == 200
            assert c.post("/api/sessions/case/messages", json=body).json()["duplicate"]
            deadline = time.monotonic() + 30
            while c.get("/api/sessions/case").json()["active"]:
                assert time.monotonic() < deadline
                time.sleep(0.1)
            branches = c.get("/api/sessions/case/branches").json()
            assert len(branches) == 2 and all(b["state"] == "completed" for b in branches)
            target = branches[0]["id"]
            assert (
                c.post(
                    "/api/sessions/case/messages",
                    json={
                        "message": "Continue this branch only",
                        "provider": "custom",
                        "request_id": "mission-two",
                        "agent_id": target,
                    },
                ).status_code
                == 200
            )
            deadline = time.monotonic() + 30
            while c.get("/api/sessions/case").json()["active"]:
                assert time.monotonic() < deadline
                time.sleep(0.1)
            row = next(b for b in c.get("/api/sessions/case/branches").json() if b["id"] == target)
            assert row["state"] == "completed" and row["mission"] == "mission-two"
            assert len(list((run / "branches" / target / "rounds").glob("round_*"))) == 2
            assert fake.tools["main"] == 1 and fake.tools[target] == 2
            content = c.get(
                f"/api/sessions/case/artifacts/branches/{target}/artifacts/evidence.csv"
            )
            assert content.status_code == 200 and "r1,6" in content.text
            detail = c.get("/api/sessions/case").json()
            assert not detail["history"] and any(
                a.get("agent_id") == target for a in detail["artifacts"]
            )
    finally:
        server.should_exit = True
        thread.join(8)
