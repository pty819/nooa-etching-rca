"""Forward reasoning/text streams while NOOA collects complete tool calls.

Tool arguments never enter the browser event stream. Reply text comes from a
separate tool-free generation selected by a task-local context variable.
"""

import codecs
import json
import time
import uuid
from contextvars import ContextVar

import httpx

from .live_events import LiveEvents

reply_channel: ContextVar[str | None] = ContextVar("reply_channel", default=None)


class WireObserver:
    def __init__(self, events: LiveEvents, message_id: str | None = None):
        self.events = events
        self.call_id = uuid.uuid4().hex[:12]
        self.message_id = message_id
        self.text = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.pending = ""
        self.events.emit(
            "model.start", call_id=self.call_id, purpose="reply" if message_id else "research"
        )
        if message_id:
            self.events.emit("reply.start", message_id=message_id, call_id=self.call_id)

    def content(self, text):
        if not isinstance(text, str) or not text:
            return
        if self.message_id:
            self.events.emit(
                "reply.delta",
                message_id=self.message_id,
                text=text,
                offset=len(self.text),
                provisional=True,
                call_id=self.call_id,
            )
            self.text += text
        else:
            self.events.emit("model.content.delta", call_id=self.call_id, text=text)

    def data(self, value: dict):
        for choice in value.get("choices", []):
            delta = choice.get("delta", {})
            thought = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(thought, str) and thought:
                self.events.emit("model.reasoning.delta", call_id=self.call_id, text=thought)
            self.content(delta.get("content"))
            # NOOA/SDK collects delta.tool_calls, validates full args, then executes.
            # We deliberately do not parse, preview or expose those argument bytes.
        if value.get("type") == "content_block_delta":
            delta = value.get("delta", {})
            if delta.get("type") == "thinking_delta":
                self.events.emit(
                    "model.reasoning.delta", call_id=self.call_id, text=delta.get("thinking", "")
                )
            elif delta.get("type") == "text_delta":
                self.content(delta.get("text"))

    def feed(self, chunk: bytes):
        self.pending = (self.pending + self.decoder.decode(chunk)).replace("\r\n", "\n")
        while "\n\n" in self.pending:
            block, self.pending = self.pending.split("\n\n", 1)
            data = "\n".join(
                line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")
            )
            if data and data != "[DONE]":
                try:
                    self.data(json.loads(data))
                except (json.JSONDecodeError, UnicodeError):
                    self.events.emit(
                        "model.stream.warning", call_id=self.call_id, message="无法解析一帧模型事件"
                    )


class ObservedStream(httpx.AsyncByteStream):
    def __init__(self, original, observer):
        self.original, self.observer = original, observer

    async def __aiter__(self):
        try:
            async for chunk in self.original:
                self.observer.feed(chunk)
                yield chunk
        finally:
            self.observer.events.emit("model.stream.end", call_id=self.observer.call_id)

    async def aclose(self):
        await self.original.aclose()


def observe_client(client, events: LiveEvents):
    transport = getattr(getattr(client, "_http", None), "httpx_async", None)
    if transport is None:
        raise RuntimeError("Pinned NOOA client does not expose its owned HTTP transport")
    client.config["stream"] = True
    if str(client.model).startswith("openai/"):
        client.config["stream_options"] = {"include_usage": True}

    # No provider-specific tool_stream flag. Inference stream transport and
    # browser presentation policy are independent.
    async def hook(response: httpx.Response):
        if response.status_code >= 400:
            events.emit("model.http_error", status=response.status_code)
        elif "text/event-stream" in response.headers.get("content-type", ""):
            response.stream = ObservedStream(
                response.stream, WireObserver(events, reply_channel.get())
            )
        else:
            events.emit("model.stream.warning", message="模型接口未返回SSE；本次不宣称逐token流式")

    transport.event_hooks["response"].append(hook)


def observe_agent(agent, events: LiveEvents):
    def on_event(event):
        kind = event.event_type
        if kind in {"LLMCallStart", "LLMCallEnd"}:
            events.emit(
                "model.waiting" if kind.endswith("Start") else "model.call.end",
                turn=getattr(event, "turn_number", 0),
                success=getattr(event, "success", True),
            )
        elif kind == "PythonOutput":
            events.emit(
                "tool.cell.end",
                tool_call_id=event.tool_call_id,
                status=str(event.execution_status),
                error=event.error[:2000],
            )
        elif kind == "LoopGuardTriggered":
            events.emit(
                "loop.guard",
                action=event.action,
                message=event.content,
                repeats=event.repeats,
                window=event.window,
            )
        elif kind == "Summary":
            events.emit("context.summary", tag=event.tag, characters=len(event.summary_text or ""))

    agent.event_manager.on("*", on_event)

    async def code_middleware(ctx, nxt):
        key = uuid.uuid4().hex[:10]
        events.emit("tool.cell.start", tool_call_id=key, code=ctx.code)
        ok = False
        try:
            result = await nxt(ctx)
            ok = ctx.result.success if ctx.result is not None else True
            return result
        finally:
            events.emit("tool.cell.finished", tool_call_id=key, ok=ok)

    agent.event_manager.intercept("execute_python", code_middleware)

    async def tool_middleware(ctx, nxt):
        if ctx.method_name not in {
            "run_code",
            "write_file",
            "run_script",
            "reply",
            "list_artifacts",
            "read_artifact",
            "spawn_research",
            "list_research",
            "wait_research",
            "read_branch_result",
            "guide_research",
            "cancel_research",
            "resume_research",
            "inspect_candidate",
            "review",
            "revise",
        }:
            return await nxt(ctx)
        key = uuid.uuid4().hex[:10]
        started = time.monotonic()
        events.emit("agent.tool.start", tool_call_id=key, name=ctx.method_name)
        ok = False
        try:
            result = await nxt(ctx)
            value = ctx.result
            ok = value.get("ok", True) if isinstance(value, dict) else True
            return result
        finally:
            events.emit(
                "agent.tool.end", tool_call_id=key, ok=ok, elapsed_s=time.monotonic() - started
            )

    agent.event_manager.intercept("agent_call", tool_middleware)
