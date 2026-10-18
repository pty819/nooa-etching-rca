"""Deterministic Markdown publication: no second model may alter checked facts."""

import asyncio
import hashlib
import json
import re
import time


class StreamingReplyWriter:
    """Compatibility name for canonical SSE publication, not model generation."""

    def __init__(self, client, replies):
        # No model client is retained or called. Research and review generation
        # still stream through their real provider observers.
        self.replies = replies

    async def send(self, message_id, brief, image_paths=None, table_paths=None):
        if not re.fullmatch(r"[\w-]{1,100}", message_id) or not brief.strip():
            raise ValueError("Provide a stable message ID and actual Markdown")
        self.replies.prepare(brief, image_paths, table_paths)
        events = self.replies.events
        started = time.time()
        events.emit("reply.start", message_id=message_id, source="canonical")
        for offset in range(0, len(brief), 1024):
            events.emit(
                "reply.delta",
                message_id=message_id,
                offset=offset,
                text=brief[offset : offset + 1024],
                source="canonical",
            )
            await asyncio.sleep(0)
        delivered = self.replies.send(message_id, brief, image_paths, table_paths)
        with (events.run / "reply-deliveries.jsonl").open("a") as out:
            out.write(
                json.dumps(
                    {
                        "message_id": message_id,
                        "task_id": events.task_id,
                        "started": started,
                        "source": "canonical",
                        "sha256": hashlib.sha256(brief.encode()).hexdigest(),
                    }
                )
                + "\n"
            )
        return {**delivered, "markdown": brief}
