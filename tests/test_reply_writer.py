"""Canonical publication cannot introduce new numbers or evidence statuses."""

import asyncio
import json

import pytest

from factor_demo.live_events import LiveEvents, MarkdownReplies
from factor_demo.reply_writer import StreamingReplyWriter


def test_canonical_reply_stream_preserves_every_character_and_does_not_call_model(tmp_path):
    (tmp_path / "workspace/artifacts").mkdir(parents=True)
    events = LiveEvents(tmp_path, "publication")

    class DangerousClient:
        async def acall(self, *a, **k):
            raise AssertionError("Publication must never regenerate checked prose")

    markdown = "压力为 **4 Torr**，机制尚未确认。🌡️\n" * 80

    async def run():
        writer = StreamingReplyWriter(DangerousClient(), MarkdownReplies(events))
        result = await writer.send("answer", markdown)
        assert result["markdown"] == markdown
        with pytest.raises(ValueError):
            await writer.send("bad", "facts", ["artifacts/missing.png"])

    try:
        asyncio.run(run())
        rows = events.since(0, 1000)
        deltas = [e["data"] for e in rows if e["type"] == "reply.delta"]
        assert "".join(d["text"] for d in deltas) == markdown
        assert [d["offset"] for d in deltas] == list(range(0, len(markdown), 1024))
        commit = [e for e in rows if e["type"] == "reply.commit"]
        assert len(commit) == 1 and commit[0]["data"]["markdown"] == markdown
        assert (
            json.loads((tmp_path / "reply-deliveries.jsonl").read_text())["source"] == "canonical"
        )
    finally:
        events.close()


def test_failed_attachment_never_starts_or_commits_publication(tmp_path):
    events = LiveEvents(tmp_path, "failed")
    replies = MarkdownReplies(events)

    async def run():
        with pytest.raises(ValueError):
            await StreamingReplyWriter(None, replies).send(
                "bad", "facts", ["artifacts/missing.png"]
            )

    try:
        asyncio.run(run())
        assert not replies.committed and events.since(0, 100) == []
    finally:
        events.close()
