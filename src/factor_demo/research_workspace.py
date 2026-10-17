"""Public discovery data and a general-purpose, persistent Python code executor."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

import polars as pl

from .synthetic import write_json


def prepare_workspace(data: Path, run: Path) -> dict:
    data, run = data.resolve(), run.resolve()
    if run.exists() and any(run.iterdir()):
        raise ValueError("Choose a new run or use autoresearch-chat")
    public, work = run / "input", run / "workspace"
    (public / "traces").mkdir(parents=True)
    work.mkdir()
    meta = pl.read_parquet(data / "runs.parquet").filter(pl.col("split") != "test")
    ids = meta["run_id"].implode()
    for name in ["runs", "steps", "outcomes"]:
        frame = meta if name == "runs" else pl.read_parquet(data / f"{name}.parquet")
        frame.filter(pl.col("run_id").is_in(ids)).write_parquet(public / f"{name}.parquet")
    for source, target in [("features", "features"), ("control_features", "control_features")]:
        frame = pl.read_parquet(data / f"{source}.parquet")
        frame.filter(pl.col("run_id").is_in(ids)).write_parquet(public / f"{target}.parquet")
    for name in ["sensors.json", "catalog.json", "recipe.json", "case.json"]:
        shutil.copyfile(data / name, public / name)
    for row in meta.iter_rows(named=True):
        shutil.copyfile(data / row["trace_path"], public / row["trace_path"])
    (work / "artifacts").mkdir()
    shutil.copyfile(Path(__file__).with_name("research_worker.py"), work / "_worker.py")
    config = {
        "data": str(data),
        "mode": "general_code_autoresearch",
        "discovery_target_runs": meta.filter(~pl.col("is_control")).height,
        "discovery_control_runs": meta.filter(pl.col("is_control")).height,
        "excluded": ["test lots", "generator source", "hidden physics", "prior diagnosis reports"],
        "public_sha256": {
            str(p.relative_to(public)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(public.rglob("*"))
            if p.is_file()
        },
    }
    write_json(run / "session.json", config)
    return config


class PythonWorkspace:
    """The macOS worker has its own environment and OS filesystem/network policy.

    This protects the *research worker*. The trusted NOOA orchestration process
    remains outside it; this is not a containment claim about adversarial agents.
    """

    def __init__(self, run: Path, events=None, research_enabled=True):
        self.run = run.resolve()
        self.work = self.run / "workspace"
        self.process = None
        self.count = max(
            (
                int(p.stem.removeprefix("cell_"))
                for p in (self.work / "research_cells").glob("cell_*.py")
            ),
            default=0,
        )
        self.lock = asyncio.Lock()
        self.events = events
        self.research_enabled = research_enabled
        self.protocol_reader = None
        self.protocol_transport = None
        self.pumps = []
        self.external_output = []
        self.active_cell = 0

    def emit(self, kind, **data):
        if self.events is not None:
            self.events.emit(kind, **data)

    async def pump(self, reader, source):
        import codecs

        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        while chunk := await reader.read(4096):
            value = decoder.decode(chunk)
            self.external_output.append(value)
            self.emit("tool.output", cell=self.active_cell, text=value, source=source)

    def profile(self) -> str:
        if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").exists():
            raise RuntimeError("This executor requires macOS sandbox-exec; no unconfined fallback")
        read_paths = [
            "/System",
            "/usr",
            "/bin",
            "/sbin",
            "/Library/Apple",
            "/Library/Fonts",
            "/private/etc",
            "/private/var/db/timezone",
            "/private/var/run",
            "/dev",
            sys.base_prefix,
            sys.prefix,
            str(self.run / "input"),
            str(self.work),
        ]
        excludes = " ".join(
            f"(require-not (subpath {json.dumps(str(Path(p).resolve()))}))" for p in read_paths
        )
        return (
            "(version 1)(allow default)(deny network*)"
            # Match filesystem paths, leaving inherited protocol pipes usable.
            f'(deny file-read-data (require-all (require-any (subpath "/Users") '
            f"(subpath {json.dumps(str((self.run / 'input').resolve().parent))})) {excludes}))"
            f'(deny file-write* (require-all (regex #"^/") '
            f"(require-not (subpath {json.dumps(str(self.work))})) "
            '(require-not (literal "/dev/null"))))'
        )

    async def start(self):
        cache = self.work / ".cache"
        cache.mkdir(exist_ok=True)
        # Explicit allowlist: no parent API credentials or user environment.
        env = {
            "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin",
            "TMPDIR": str(cache),
            "MPLCONFIGDIR": str(cache / "mpl"),
            "NUMBA_CACHE_DIR": str(cache / "numba"),
            "JOBLIB_TEMP_FOLDER": str(cache / "joblib"),
            "MPLBACKEND": "Agg",
            "PYTHONUNBUFFERED": "1",
            "POLARS_MAX_THREADS": "4",
            "OPENBLAS_NUM_THREADS": "2",
            "OMP_NUM_THREADS": "2",
            "VECLIB_MAXIMUM_THREADS": "2",
        }
        shutil.copyfile(Path(__file__).with_name("research_worker.py"), self.work / "_worker.py")
        sandbox_profile = self.profile()
        read_fd, write_fd = os.pipe()
        env["NOOA_WORKER_CHANNEL_FD"] = str(write_fd)
        self.protocol_reader = asyncio.StreamReader(limit=16 * 1024 * 1024)
        self.protocol_transport, _ = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(self.protocol_reader),
            os.fdopen(read_fd, "rb", buffering=0),
        )
        try:
            launch = asyncio.create_task(
                asyncio.create_subprocess_exec(
                    "/usr/bin/sandbox-exec",
                    "-p",
                    sandbox_profile,
                    sys.executable,
                    "-I",
                    "-u",
                    str(self.work / "_worker.py"),
                    cwd=self.work,
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                    limit=16 * 1024 * 1024,
                    pass_fds=(write_fd,),
                )
            )
            try:
                self.process = await asyncio.shield(launch)
            except asyncio.CancelledError:
                self.process = await launch
                await self.close()
                raise
        except BaseException:
            if self.protocol_transport is not None:
                self.protocol_transport.close()
            self.protocol_transport = None
            raise
        finally:
            os.close(write_fd)
        self.pumps = [
            asyncio.create_task(self.pump(self.process.stdout, "stdout")),
            asyncio.create_task(self.pump(self.process.stderr, "stderr")),
        ]

    async def execute(self, code: str, timeout_s: int = 120) -> dict:
        if not self.research_enabled:
            raise PermissionError("Research code execution is disabled in discussion mode")
        async with self.lock:
            if self.process is None or self.process.returncode is not None:
                await self.start()
            self.count += 1
            self.active_cell = self.count
            self.external_output = []
            cells = self.work / "research_cells"
            cells.mkdir(exist_ok=True)
            filename = cells / f"cell_{self.count:04d}.py"
            filename.write_text(code)
            request = {"code": code, "filename": str(filename.relative_to(self.work))}
            started = time.time()
            self.emit(
                "tool.start", cell=self.count, code=code, timeout_s=min(300, max(1, timeout_s))
            )
            captured = []
            cancelled = False
            try:
                self.process.stdin.write((json.dumps(request) + "\n").encode())
                await self.process.stdin.drain()
                async with asyncio.timeout(min(300, max(1, timeout_s))):
                    while True:
                        line = await self.protocol_reader.readline()
                        if not line:
                            raise RuntimeError("Research worker exited before a result")
                        result = json.loads(line)
                        if result.get("kind") == "output":
                            captured.append(result["text"])
                            self.emit(
                                "tool.output", cell=self.count, text=result["text"], source="python"
                            )
                        else:
                            break
                await asyncio.sleep(0.005)
                result["output"] += "".join(self.external_output)
            except TimeoutError:
                await self.close()
                result = {
                    "ok": False,
                    "output": "".join(captured + self.external_output)
                    + "\nHard timeout: worker killed; variables reset, files retained",
                }
            except asyncio.CancelledError:
                await self.close()
                cancelled = True
                result = {
                    "ok": False,
                    "output": "".join(captured + self.external_output)
                    + "\nCancelled: worker stopped; files retained",
                }
            except (
                RuntimeError,
                json.JSONDecodeError,
                BrokenPipeError,
                ConnectionResetError,
            ) as error:
                await self.close()
                result = {
                    "ok": False,
                    "output": "".join(captured + self.external_output)
                    + f"\nWorker failure: {error}",
                }
            record = {
                "cell": self.count,
                "code": code,
                "started": started,
                "elapsed_s": time.time() - started,
                **result,
            }
            with (self.run / "execution.jsonl").open("a") as out:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
            write_json(filename.with_suffix(".output.json"), result)
            self.emit(
                "tool.end",
                cell=self.count,
                ok=result["ok"],
                elapsed_s=record["elapsed_s"],
                output=result["output"][-24000:],
            )
            print(
                f"Code cell {self.count}: {'OK' if result['ok'] else 'ERROR'} ({record['elapsed_s']:.1f}s)",
                flush=True,
            )
            if cancelled:
                raise asyncio.CancelledError
            return {
                **result,
                "cell": self.count,
                "output": result["output"][:24000],
                "full_output": str(filename.with_suffix(".output.json").relative_to(self.work)),
                "output_truncated": len(result["output"]) > 24000,
            }

    async def close(self):
        if self.process is not None and self.process.returncode is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await self.process.wait()
        for task in self.pumps:
            task.cancel()
        if self.pumps:
            await asyncio.gather(*self.pumps, return_exceptions=True)
        self.pumps = []
        if self.protocol_transport is not None:
            self.protocol_transport.close()
        self.protocol_transport = None
        self.process = None
