"""Standalone persistent scientific Python worker; no provider or NOOA objects."""

import contextlib
import io
import json
import os
import sys
import time
import traceback
from pathlib import Path


def main():
    sys.path.insert(0, str(Path.cwd()))
    namespace = {
        "__name__": "__research__",
        "DATASET": Path(os.environ.get("RESEARCH_DATASET", "../input")),
        "DATASET_ID": os.environ.get("RESEARCH_DATASET_ID", ""),
    }
    fd = os.environ.get("NOOA_WORKER_CHANNEL_FD")
    protocol = os.fdopen(int(fd), "w", buffering=1) if fd else sys.stdout

    def send(value):
        protocol.write(json.dumps(value) + "\n")
        protocol.flush()

    class Output(io.StringIO):
        def __init__(self):
            super().__init__()
            self.total = 0
            self.kept = 0
            self.pending = ""
            self.last = 0.0

        def flush(self):
            if self.pending:
                for start in range(0, len(self.pending), 4096):
                    send({"kind": "output", "text": self.pending[start : start + 4096]})
                self.pending = ""
                self.last = time.monotonic()

        def write(self, value):
            self.total += len(value)
            kept = value[: max(0, 262144 - self.kept)]
            self.kept += len(kept)
            self.pending += kept
            super().write(kept)
            if len(self.pending) >= 4096 or time.monotonic() - self.last >= 0.025:
                self.flush()
            return len(value)

    for line in sys.stdin:
        request = json.loads(line)
        stream = Output()
        ok = True
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            try:
                exec(compile(request["code"], request["filename"], "exec"), namespace)  # noqa: S102
            except SystemExit as error:
                if error.code not in (None, 0):
                    ok = False
                    traceback.print_exc()
            except BaseException:  # noqa: BLE001 - keep the worker alive after scientific cell errors
                ok = False
                traceback.print_exc()
        stream.flush()
        truncated = stream.total > stream.kept
        if truncated:
            send(
                {
                    "kind": "output",
                    "text": "\n[Output truncated at 262144 characters; save large results to artifacts.]\n",
                }
            )
        response = {
            "kind": "result",
            "ok": ok,
            "output": stream.getvalue(),
            "output_truncated": truncated,
            "output_characters": stream.total,
            "variables": {
                k: type(namespace[k]).__name__
                for k in list(namespace)[-100:]
                if not k.startswith("_")
            },
        }
        send(response)


if __name__ == "__main__":
    main()
