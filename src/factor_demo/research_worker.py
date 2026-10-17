"""Standalone persistent scientific Python worker; no provider or NOOA objects."""

import contextlib
import io
import json
import os
import sys
import traceback


def main():
    namespace = {"__name__": "__research__"}
    fd = os.environ.get("NOOA_WORKER_CHANNEL_FD")
    protocol = os.fdopen(int(fd), "w", buffering=1) if fd else sys.stdout

    def send(value):
        protocol.write(json.dumps(value) + "\n")
        protocol.flush()

    class Output(io.StringIO):
        def write(self, value):
            if value:
                send({"kind": "output", "text": value})
            return super().write(value)

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
        response = {
            "kind": "result",
            "ok": ok,
            "output": stream.getvalue(),
            "variables": {
                k: type(v).__name__ for k, v in namespace.items() if not k.startswith("_")
            },
        }
        send(response)


if __name__ == "__main__":
    main()
