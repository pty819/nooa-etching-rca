"""A branch has its own NOOA object, trace sink, SQLite history and Python worker."""

import argparse
import asyncio
import json
from pathlib import Path

from .autoresearch import research_turn
from .capabilities import Capabilities
from .live_events import LiveEvents, branch_path


async def main(args):
    run = branch_path(args.root, args.agent)
    request = json.loads((run / "requests" / f"{args.mission}.json").read_text())
    meta = json.loads((run / "branch.json").read_text())
    events = LiveEvents(
        run,
        args.mission,
        root=args.root,
        agent_id=args.agent,
        parent_agent_id=meta["parent_agent_id"],
    )
    try:
        await research_turn(
            run,
            request["provider"],
            request["message"],
            "research",
            settings_path=Path(request["settings_path"]),
            events=events,
            live=True,
            timeout=request["policy"]["branch_timeout_s"],
            policy=Capabilities.model_validate(request["policy"]),
            admission_path=Path(request["admission_path"]),
        )
    except asyncio.CancelledError:
        events.emit("branch.cancelled")
    except Exception as error:
        events.emit("branch.error", error_type=type(error).__name__)
        raise
    finally:
        events.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--mission", required=True)
    asyncio.run(main(parser.parse_args()))
