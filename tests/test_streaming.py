"""Checks for real incremental delivery, replay and artifact confinement."""

import asyncio
import json
import sys

import pytest

from factor_demo.live_events import LiveEvents, MarkdownReplies, artifact_path
from factor_demo.provider_stream import ObservedStream, WireObserver
from factor_demo.research_workspace import PythonWorkspace


def run_dir(tmp_path):
    run = tmp_path / "case"
    (run / "workspace/artifacts").mkdir(parents=True)
    (run / "input").mkdir()
    return run


def test_plain_text_stream_is_incremental_and_tool_parameters_are_private(tmp_path):
    events = LiveEvents(run_dir(tmp_path), "task")
    observer = WireObserver(events, message_id="answer")
    text = "你好 🌡️ **证据**\n\n| 指标 | 值 |\n|---|---|\n| He | 7.9 |"
    wire = b""
    for fragment in text:
        value = {"choices": [{"delta": {"content": fragment}}]}
        wire += ("data: " + json.dumps(value, ensure_ascii=False) + "\r\n\r\n").encode()
    # It is never valid to leak partial executable arguments into a reply.
    wire += b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"PRIVATE_TOOL_ARGS"}}]}}]}\n\n'

    async def exercise():
        class Upstream:
            async def __aiter__(self):
                for i in range(0, len(wire), 3):
                    yield wire[i : i + 3]
                rows = events.since(0, 10000)
                assert any(e["type"] == "reply.delta" for e in rows)
                assert not any(e["type"] == "model.stream.end" for e in rows)

            async def aclose(self):
                pass

        assert b"".join([c async for c in ObservedStream(Upstream(), observer)]) == wire

    asyncio.run(exercise())
    rows = events.since(0, 10000)
    assert "".join(e["data"]["text"] for e in rows if e["type"] == "reply.delta") == text
    assert not any(e["type"] == "tool.arguments.delta" for e in rows)
    assert "PRIVATE_TOOL_ARGS" not in json.dumps(rows)
    assert rows[-1]["type"] == "model.stream.end"
    events.close()


def test_research_stream_never_creates_reply_from_tool_arguments(tmp_path):
    events = LiveEvents(run_dir(tmp_path), "research")
    observer = WireObserver(events)
    observer.data(
        {
            "choices": [
                {
                    "delta": {
                        "reasoning_content": "检查证据",
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {
                                    "arguments": 'old self.reply(markdown="DO_NOT_PREVIEW")'
                                },
                            }
                        ],
                    }
                }
            ]
        }
    )
    assert [e["type"] for e in events.since(0)] == ["model.start", "model.reasoning.delta"]
    events.close()


def test_reply_commit_table_image_confined_and_replayable(tmp_path):
    run = run_dir(tmp_path)
    root = run / "workspace/artifacts"
    (root / "table.csv").write_text('feature,value\nHe,7.9\n"a|b","<script>alert(1)</script>"\n')
    (root / "plot.png").write_bytes(b"example only")
    events = LiveEvents(run, "task")
    replies = MarkdownReplies(events)
    replies.send("answer", "**结果**", ["artifacts/plot.png"], ["artifacts/table.csv"])
    row = events.since(0)[0]
    assert row["type"] == "reply.commit"
    assert "| He | 7.9 |" in row["data"]["markdown"]
    assert "a\\|b" in row["data"]["markdown"]
    assert "![" in row["data"]["markdown"]
    marker = row["id"]
    events.add_guidance("补充指导")
    assert events.take_guidance() == ["补充指导"]
    assert events.take_guidance() == []
    events.close()
    resumed = LiveEvents(run, "task")
    assert resumed.since(marker) == []
    assert resumed.since(0)[0] == row
    outside = tmp_path / "secret.txt"
    outside.write_text("not visible")
    (root / "escape.txt").symlink_to(outside)
    for path in ["../../secret.txt", "artifacts/escape.txt"]:
        with pytest.raises(ValueError):
            artifact_path(run, path)
    resumed.close()


@pytest.mark.skipif(
    sys.platform not in {"darwin", "linux"}, reason="enforced execution backend required"
)
def test_code_and_subprocess_outputs_arrive_before_cell_finishes(tmp_path):
    run = run_dir(tmp_path)
    events = LiveEvents(run, "task")

    async def exercise():
        worker = PythonWorkspace(run, events=events)
        try:
            task = asyncio.create_task(
                worker.execute(
                    "import subprocess,sys,time\nprint('FIRST',flush=True)\nsubprocess.run([sys.executable,'-u','-c',\"print('CHILD',flush=True)\"],check=True)\ntime.sleep(1)\nprint('LAST')"
                )
            )
            async with asyncio.timeout(5):
                while True:
                    rows = events.since(0)
                    output = "".join(e["data"]["text"] for e in rows if e["type"] == "tool.output")
                    if "FIRST" in output and "CHILD" in output:
                        break
                    await asyncio.sleep(0.02)
            assert not task.done()
            result = await task
            assert result["ok"] and "LAST" in result["output"] and "CHILD" in result["output"]
        finally:
            await worker.close()

    asyncio.run(exercise())
    events.close()


def test_http_sse_reconnect_idempotency_steering_and_stop(tmp_path):
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    from factor_demo.web import create_app

    run = tmp_path / "runs/case"
    (run / "workspace/artifacts").mkdir(parents=True)
    (run / "session.json").write_text(
        json.dumps({"discovery_target_runs": 75, "discovery_control_runs": 15})
    )
    (run / "ui.json").write_text('{"title":"API test"}')
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets/providers.json").write_text('{"glm":{"model":"test-model"}}')
    payload = {
        "summary": "saved",
        "hypotheses": [],
        "artifacts": [],
        "next_experiments": [],
        "questions_for_expert": [],
    }
    conversation = [
        {"round": 1, "speaker": "user", "message": "legacy"},
        {"round": 1, "speaker": "agent", **payload},
        {"round": 2, "speaker": "user", "message": "live", "turn_id": "old-live"},
        {"round": 2, "speaker": "agent", **payload},
    ]
    (run / "conversation.jsonl").write_text("\n".join(map(json.dumps, conversation)) + "\n")
    app = create_app(tmp_path)
    starts = []

    async def runner(run, body, job):
        starts.append(body.request_id)
        events = LiveEvents(run, body.request_id)
        try:
            events.emit("reply.delta", message_id="answer", text="first", offset=0)
            while not (run / "requests" / f"{body.request_id}.cancel").exists():
                for text in events.take_guidance():
                    events.emit("steering.applied", message=text)
                await asyncio.sleep(0.01)
            events.emit(
                "reply.commit", message_id="answer", markdown="first complete", attachments=[]
            )
            events.emit("task.end", status="cancelled")
        finally:
            events.close()
            app.state.jobs.pop(run.name, None)

    app.state.runner = runner
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    until = time.monotonic() + 5
    while not server.started:
        assert time.monotonic() < until
        time.sleep(0.01)
    try:
        with httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=5, headers={"X-Research-Client": "1"}
        ) as c:
            detail = c.get("/api/sessions/case").json()
            assert len(detail["history"]) == 2
            assert all(m["round"] == 1 for m in detail["history"])
            body = {"message": "start", "request_id": "one"}
            assert c.post("/api/sessions/case/messages", json=body).json()["steering"] is False
            assert c.post("/api/sessions/case/messages", json=body).json()["duplicate"] is True
            rows = []
            with c.stream("GET", "/api/sessions/case/events") as response:
                assert response.headers["content-type"].startswith("text/event-stream")
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        rows.append(json.loads(line[6:]))
                        if rows[-1]["type"] == "reply.delta":
                            break
            assert starts == ["one"] and "case" in app.state.jobs
            cursor = rows[-1]["id"]
            steering = c.post(
                "/api/sessions/case/messages", json={"message": "focus", "request_id": "two"}
            ).json()
            assert steering["steering"] and steering["task_id"] == "one"
            assert c.post(
                "/api/sessions/case/messages", json={"message": "focus", "request_id": "two"}
            ).json()["duplicate"]
            assert c.post("/api/sessions/case/stop", json={}).json()["stopping"]
            replay = []
            with c.stream(
                "GET", "/api/sessions/case/events", headers={"Last-Event-ID": str(cursor)}
            ) as response:
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        replay.append(json.loads(line[6:]))
                        if replay[-1]["type"] == "task.end":
                            break
            assert all(e["id"] > cursor for e in replay)
            assert len({e["id"] for e in replay}) == len(replay)
            assert any(e["type"] == "reply.commit" for e in replay)
            assert c.get("/api/sessions/case/events?after=-1").status_code == 400
            assert c.get("/api/sessions/case/events?after=" + str(2**70)).status_code == 400
            assert (
                c.post(
                    "/api/sessions/case/messages",
                    json=body,
                    headers={"Origin": "https://external.invalid"},
                ).status_code
                == 403
            )
            (run / "workspace/artifacts/unsafe.html").write_text("<script>bad()</script>")
            assert c.get("/api/sessions/case/artifacts/artifacts/unsafe.html").status_code == 400
            assert starts == ["one"]
    finally:
        server.should_exit = True
        thread.join(timeout=8)


@pytest.mark.skipif(
    sys.platform not in {"darwin", "linux"}, reason="enforced execution backend required"
)
def test_cancel_keeps_partial_output_and_resets_worker(tmp_path):
    run = run_dir(tmp_path)
    events = LiveEvents(run, "cancel")

    async def exercise():
        worker = PythonWorkspace(run, events=events)
        try:
            task = asyncio.create_task(
                worker.execute("import time\nprint('BEFORE_CANCEL',flush=True)\ntime.sleep(30)")
            )
            async with asyncio.timeout(5):
                while not any(
                    e["type"] == "tool.output" and "BEFORE_CANCEL" in e["data"]["text"]
                    for e in events.since(0)
                ):
                    await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            record = json.loads((run / "execution.jsonl").read_text().splitlines()[-1])
            assert not record["ok"] and "BEFORE_CANCEL" in record["output"]
            assert worker.process is None
            assert any(e["type"] == "tool.end" and not e["data"]["ok"] for e in events.since(0))
            assert (await worker.execute("print('AFTER_RESTART')"))["ok"]
        finally:
            await worker.close()

    asyncio.run(exercise())
    events.close()
