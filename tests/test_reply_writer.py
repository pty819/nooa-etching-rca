import asyncio
import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from factor_demo.live_events import LiveEvents, MarkdownReplies
from factor_demo.provider_stream import observe_client, reply_channel
from factor_demo.providers import Provider
from factor_demo.reply_writer import StreamingReplyWriter
from factor_demo.runtime import RuntimeSettings, build_client


def test_real_sdk_reply_stream_has_no_tools_and_no_glm_tool_stream(tmp_path):
    (tmp_path / "workspace/artifacts").mkdir(parents=True)
    events = LiveEvents(tmp_path, "writer")
    captured = []
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        body = await request.json()
        captured.append(body)

        async def chunks():
            def frame(delta, finish=None):
                return (
                    "data: "
                    + json.dumps(
                        {
                            "id": "chat-test",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": "glm-5.3-flash",
                            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )

            yield frame({"role": "assistant", "reasoning_content": "只转述证据"})
            for text in ["已验证：", "目标腔 ", "75 次 🌡️。"]:
                yield frame({"content": text})
                await asyncio.sleep(0.02)
            # Verify the user already received partial text while inference is open.
            rows = events.since(0)
            deltas = [e for e in rows if e["type"] == "reply.delta"]
            assert 2 <= len(deltas) <= 3
            assert "".join(e["data"]["text"] for e in deltas) == "已验证：目标腔 75 次 🌡️。"
            assert not any(e["type"] == "reply.commit" for e in rows)
            yield frame({}, "stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

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

    async def exercise():
        provider = Provider(
            "test", "glm-5.3-flash", f"http://127.0.0.1:{port}/v1", "openai", "fake"
        )
        client = build_client(provider, RuntimeSettings())
        observe_client(client, events)
        replies = MarkdownReplies(events)
        writer = StreamingReplyWriter(client, replies)
        try:
            result = await writer.send("answer", "目标腔75次，不能推断根因。")
            assert result["delivered"] and result["markdown"] == "已验证：目标腔 75 次 🌡️。"
            assert reply_channel.get() is None
            with pytest.raises(ValueError):
                await writer.send("bad", "bad", ["artifacts/missing.png"])
        finally:
            await client.aclose()

    try:
        asyncio.run(exercise())
    finally:
        server.should_exit = True
        thread.join(5)
    assert len(captured) == 1
    request = captured[0]
    assert request["stream"] is True
    assert not request.get("tools") and "tool_stream" not in request
    rows = events.since(0)
    assert rows[-1]["type"] == "reply.commit"
    assert not any(e["type"] == "tool.arguments.delta" for e in rows)
    assert (tmp_path / "reply-generations.jsonl").is_file()
    events.close()


def test_failed_reply_never_commits_and_clears_channel(tmp_path):
    events = LiveEvents(tmp_path, "failed")
    replies = MarkdownReplies(events)

    class Client:
        async def acall(self, *args, **kwargs):
            return SimpleNamespace(content="truncated", tool_calls=[], finish_reason="length")

    async def exercise():
        with pytest.raises(ValueError):
            await StreamingReplyWriter(Client(), replies).send("partial", "facts")
        assert reply_channel.get() is None

    asyncio.run(exercise())
    assert not replies.committed
    assert not any(e["type"] == "reply.commit" for e in events.since(0))
    events.close()
