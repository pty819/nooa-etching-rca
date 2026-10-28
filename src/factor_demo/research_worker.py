"""Credential-free long-lived scientific kernel; stdout is never its protocol."""

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


async def main():
    setup = json.loads(sys.stdin.buffer.readline())
    from sandboxing import install_linux

    install_linux(setup["reads"], [setup["work"]])
    from session_cell import CellCompileError, execute_source

    control = os.fdopen(os.dup(1), "w", buffering=1)
    namespace = {
        "__name__": "__research__",
        "DATASET": Path(setup["dataset"]),
        "DATASET_ID": setup["dataset_id"],
    }
    while raw := sys.stdin.buffer.readline():
        request = json.loads(raw)
        if request.get("op") == "close":
            break
        saved = [os.dup(1), os.dup(2)]
        status, error_type = "completed", None
        try:
            with open(request["stdout"], "w") as out, open(request["stderr"], "w") as err:  # noqa: ASYNC230 - dedicated isolated kernel
                sys.stdout.flush()
                sys.stderr.flush()
                os.dup2(out.fileno(), 1)
                os.dup2(err.fileno(), 2)
                executing = namespace
                if request.get("script"):
                    sys.argv = [request["filename"], *request.get("args", [])]
                    executing = {
                        "__name__": "__main__",
                        "__file__": request["filename"],
                        "DATASET": namespace["DATASET"],
                        "DATASET_ID": namespace["DATASET_ID"],
                    }
                try:
                    await execute_source(request["code"], executing, request["filename"])
                except BaseException as error:  # noqa: BLE001 - cell execution boundary
                    error_type = type(error).__name__
                    status = "syntax_error" if isinstance(error, CellCompileError) else "exception"
                    if isinstance(error, SystemExit) and error.code in (None, 0):
                        status, error_type = "completed", None
                    else:
                        traceback.print_exc()
                finally:
                    sys.stdout.flush()
                    sys.stderr.flush()
        finally:
            for fd, saved_fd in zip((1, 2), saved, strict=True):
                os.dup2(saved_fd, fd)
                os.close(saved_fd)
        variables = {
            k: type(v).__name__ for k, v in list(namespace.items())[-100:] if not k.startswith("_")
        }
        control.write(
            json.dumps(
                {
                    "request_id": request["request_id"],
                    "status": status,
                    "exception_type": error_type,
                    "variables": variables,
                }
            )
            + "\n"
        )
        if status == "exception":
            break
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
