"""Host-owned kernel lifecycle and bounded per-cell file streaming."""

import asyncio
import json
import os
import uuid

from .sandboxing import _proc_children, child_environment, launch, reap, reap_cell_children


class PersistentKernel:
    def __init__(self, work, kit, data_root, dataset_id, emit):
        self.work, self.kit, self.data_root = work, kit, data_root
        self.dataset_id, self.emit = dataset_id, emit
        self.process = None
        self.epoch = 0
        self.kernel_id = uuid.uuid4().hex
        self.lock = asyncio.Lock()
        self.diagnostics = None
        self.infrastructure = set()

    async def _drain(self, reader):
        # Bootstrap diagnostics must not fill a pipe during a running session.
        tail = b""
        while chunk := await reader.read(4096):
            tail = (tail + chunk)[-16000:]
        return tail.decode(errors="replace")

    async def start(self):
        if self.process and self.process.returncode is None:
            return
        if self.process:
            await self.close()
        self.epoch += 1
        self.process = await asyncio.create_subprocess_exec(
            *launch([self.kit / "research_worker.py"], [self.data_root, self.kit], [self.work]),
            cwd=self.work,
            env=child_environment(self.work),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=65536,
        )
        self.diagnostics = asyncio.create_task(self._drain(self.process.stderr))
        self.process.stdin.write(
            (
                json.dumps(
                    {
                        "work": str(self.work),
                        "reads": [str(self.data_root), str(self.kit)],
                        "dataset": str(self.data_root),
                        "dataset_id": self.dataset_id or "",
                    }
                )
                + "\n"
            ).encode()
        )
        await self.process.stdin.drain()
        try:
            async with asyncio.timeout(15):
                ready = json.loads(await self.process.stdout.readline())
                if ready != {"status": "ready"}:
                    raise ValueError("Invalid kernel bootstrap")
            # Captured before any user source runs: wrappers/init and Python only.
            self.infrastructure = _proc_children(self.process.pid)
        except BaseException:
            await self.close()
            raise

    async def close(self):
        if self.process:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 0.5)
            except TimeoutError:
                pass
        await reap(self.process)
        self.process = None
        if self.diagnostics:
            await self.diagnostics
            self.diagnostics = None

    async def run(self, code, filename, cell, timeout, *, script=False, args=None):
        async with self.lock:
            await self.start()
            epoch = self.epoch
            process = self.process
            identity = uuid.uuid4().hex
            from .path_guard import ensure_real_dir, open_regular_nofollow, write_text_nofollow

            folder = ensure_real_dir(self.work / ".kernel-output", root=self.work)
            paths = [folder / (identity + suffix) for suffix in (".stdout", ".stderr")]
            for path in paths:
                write_text_nofollow(path, "", root=self.work)
            # Open before dispatch; never follow a worker-replaced link while streaming.
            streams = [
                os.fdopen(open_regular_nofollow(path, root=self.work), "rb") for path in paths
            ]
            counts = [0, 0]
            buffers = ["", ""]
            stopped = asyncio.Event()

            async def capture():
                import codecs

                decoders = [codecs.getincrementaldecoder("utf-8")("replace") for _ in paths]
                with streams[0] as out, streams[1] as err:
                    while True:
                        for i, stream in enumerate((out, err)):
                            while chunk := stream.read(65536):
                                text = decoders[i].decode(chunk)
                                counts[i] += len(text)
                                buffers[i] = (
                                    (buffers[i] + text)[:262144]
                                    if i == 0
                                    else (buffers[i] + text)[-262144:]
                                )
                                if counts[i] <= 262144:
                                    self.emit(
                                        "tool.output",
                                        cell=cell,
                                        source=("stdout", "stderr")[i],
                                        text=text,
                                    )
                        if stopped.is_set():
                            return
                        await asyncio.sleep(0.02)

            pump = asyncio.create_task(capture())
            status, exception_type, variables, interrupted = "protocol_error", None, {}, None
            returncode = 1
            try:
                request = {
                    "request_id": identity,
                    "code": code,
                    "filename": filename,
                    "stdout": str(paths[0]),
                    "stderr": str(paths[1]),
                    "script": script,
                    "args": args or [],
                }
                async with asyncio.timeout(timeout):
                    process.stdin.write((json.dumps(request) + "\n").encode())
                    await process.stdin.drain()
                    line = await process.stdout.readline()
                    if not line:
                        status = "crashed"
                    else:
                        response = json.loads(line)
                        if response.get("request_id") != identity or response.get("status") not in {
                            "completed",
                            "exception",
                            "syntax_error",
                        }:
                            raise ValueError("Invalid kernel completion")
                        status = response["status"]
                        returncode = response.get("returncode", 0 if status == "completed" else 1)
                        exception_type = response.get("exception_type")
                        variables = response.get("variables", {})
                    # A successful cell must not leave background subprocesses alive.
                    # Keep the trusted bootstrap process chain and the live namespace.
                    await asyncio.to_thread(reap_cell_children, process.pid, self.infrastructure)
            except TimeoutError:
                status = "timeout"
            except asyncio.CancelledError as error:
                status, interrupted = "cancelled", error
            except (ValueError, OSError, RuntimeError):
                status = "protocol_error"
            finally:
                reset = status not in {"completed", "syntax_error"}
                if reset:
                    await self.close()
                stopped.set()
                await pump
            for path in paths:
                path.unlink(missing_ok=True)
            return {
                "ok": status == "completed",
                "returncode": returncode,
                "runtime_status": status,
                "exception_type": exception_type,
                "kernel_id": self.kernel_id,
                "epoch": epoch,
                "state_effect": "reset"
                if reset
                else "unchanged"
                if status == "syntax_error"
                else "preserved",
                "variables": {} if reset else variables,
                "stdout": buffers[0],
                "stderr": buffers[1],
                "output": "".join(buffers),
                "output_characters": sum(counts),
                "output_truncated": any(n > 262144 for n in counts),
                "interrupted": interrupted,
            }
