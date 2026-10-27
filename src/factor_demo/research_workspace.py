"""Public discovery data and a general-purpose, persistent Python code executor."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import polars as pl

from .io_utils import write_json


def prepare_workspace(data: Path, run: Path) -> dict:
    data, run = data.resolve(), run.resolve()
    legacy_fdc_files = [
        "runs.parquet",
        "features.parquet",
        "steps.parquet",
        "outcomes.parquet",
        "sensors.json",
        "catalog.json",
        "recipe.json",
        "case.json",
    ]
    if data.is_file() or not all((data / name).is_file() for name in legacy_fdc_files):
        from .datasets import prepare_generic_workspace

        prepare_generic_workspace(data, run)
        return json.loads((run / "session.json").read_text())
    if run.exists() and any(p.name != ".turn.lock" for p in run.iterdir()):
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
        source_path = data / f"{source}.parquet"
        if (
            source == "control_features"
            and not source_path.exists()
            and not meta["is_control"].any()
        ):
            frame = pl.read_parquet(data / "features.parquet").head(0)
        else:
            frame = pl.read_parquet(source_path)
        frame.filter(pl.col("run_id").is_in(ids)).write_parquet(public / f"{target}.parquet")
    for name in ["sensors.json", "catalog.json", "recipe.json", "case.json"]:
        shutil.copyfile(data / name, public / name)
    if (data / "dataset.json").is_file():
        shutil.copyfile(data / "dataset.json", public / "dataset.json")
    from .dataset_adapter import safe_path

    for row in meta.iter_rows(named=True):
        source = safe_path(data, row["trace_path"])
        target = public / source.relative_to(data)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (work / "artifacts").mkdir()
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
    from .evaluation import prepare_evaluation

    prepare_evaluation(data, run)
    return config


class PythonWorkspace:
    """Trusted supervisor; each cell executes in a fresh credential-free child.

    State is deserialized only inside the next guarded child. The host never
    unpickles worker data and never accepts a worker-supplied success frame.
    """

    def __init__(self, run: Path, events=None, research_enabled=True, read_scope_root=None):
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
        self.read_scope_root = Path(read_scope_root or self.run).resolve()
        self.dataset_session = None
        self.dataset_id = None
        self.data_root = self.run / "input"
        if (self.run / "session.json").exists() and any(
            (self.data_root / n).exists()
            for n in ["runs.parquet", "dataset.json", "samples.parquet", "samples.csv"]
        ):
            from .datasets import DatasetSession

            self.dataset_session = DatasetSession(self.run, events)
        self.checkpoint = None
        self.latest_execution = None
        self.latest_failure = None
        self.pumps = []
        self.external_chars = 0
        self.output_events = 0
        self.kit = self.run / ".execution" / "runtime"

    def emit(self, kind, **data):
        if self.events is not None:
            self.events.emit(kind, **data)

    def profile(self):
        from .sandboxing import mac_profile

        if sys.platform != "darwin":
            raise RuntimeError("macOS profile requested on another platform")
        return mac_profile([self.data_root, self.kit], [self.work])

    async def start(self):
        self.work.mkdir(parents=True, exist_ok=True)
        self.kit.mkdir(parents=True, exist_ok=True)
        from .sandboxing import prepare_guard_modules

        prepare_guard_modules(self.kit)
        for name in ["research_worker.py", "sandboxing.py"]:
            shutil.copyfile(Path(__file__).with_name(name), self.kit / name)

    async def _capture(self, reader, chunks, counter, cell, source):
        import codecs

        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        while data := await reader.read(4096):
            text = decoder.decode(data)
            kept = text[: max(0, 262144 - counter[1])]
            counter[0] += len(text)
            counter[1] += len(kept)
            if source == "stderr":
                chunks[:] = [("".join(chunks) + text)[-262144:]]
            elif kept:
                chunks.append(kept)
            if kept:
                self.emit("tool.output", cell=cell, text=kept, source=source)

    async def execute(
        self,
        code: str,
        timeout_s: int = 120,
        *,
        script: Path | None = None,
        args: list[str] | None = None,
        source_ref: str | None = None,
    ) -> dict:
        if not self.research_enabled:
            raise PermissionError("Research code execution is disabled in discussion mode")
        from .execution_provenance import artifact_hashes, next_cell, record_cell
        from .sandboxing import (
            apply_cell_cgroup_pids,
            child_environment,
            ensure_child_subreaper,
            launch,
            reap,
        )

        ensure_child_subreaper()

        async with self.lock:
            if self.dataset_session is not None:
                current = self.dataset_session.current()
                if self.dataset_id != current["id"]:
                    self.checkpoint = None
                    self.dataset_id = current["id"]
                    self.data_root = self.dataset_session.path(self.dataset_id)
            await self.start()
            self.count = next_cell(self.run, self.count)
            cell = self.count
            try:
                before = artifact_hashes(self.run)
            except ValueError:
                before = {}
            from .path_guard import ensure_real_dir, write_text_nofollow

            cells = ensure_real_dir(self.work / "research_cells", root=self.work)
            filename = cells / f"cell_{cell:04d}.py"
            if source_ref is None:
                write_text_nofollow(filename, code, root=self.work)
            # The child's source/config are immutable to that child, even on Linux.
            private = self.run / ".execution" / f"cell_{cell:06d}"
            private.mkdir(parents=True, exist_ok=False)
            source = private / "source.py"
            executed_source = script.read_text() if script else code
            if source_ref is None:
                source.write_text(executed_source)
            states = ensure_real_dir(self.work / ".cell-state", root=self.work)
            checkpoint = states / f"{cell:06d}.pickle"
            metadata = states / f"{cell:06d}.json"
            config = private / "request.json"
            write_json(
                config,
                {
                    "source": str(source),
                    **({"code": code} if source_ref else {}),
                    "filename": source_ref
                    or (str(script) if script else str(filename.relative_to(self.work))),
                    "script": bool(script),
                    "args": args or [],
                    "previous": str(self.checkpoint) if self.checkpoint else None,
                    "checkpoint": str(checkpoint),
                    "metadata": str(metadata),
                    "work": str(self.work),
                    "reads": [str(self.data_root), str(self.kit), str(private)],
                },
            )
            environment = child_environment(
                self.work,
                RESEARCH_DATASET=str(self.data_root),
                RESEARCH_DATASET_ID=self.dataset_id or "",
            )
            started = time.time()
            self.emit(
                "tool.start",
                cell=cell,
                code=code,
                timeout_s=min(300, max(1, timeout_s)),
                dataset_ref=self.dataset_id,
            )
            stdout, stderr, counters = [], [], [[0, 0], [0, 0]]
            interrupted = None
            timed_out = False
            apply_cell_cgroup_pids()
            self.process = await asyncio.create_subprocess_exec(
                *launch(
                    [self.kit / "research_worker.py", config],
                    [self.data_root, self.kit, private],
                    [self.work],
                ),
                cwd=self.work,
                env=environment,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            process = self.process
            self.pumps = [
                asyncio.create_task(
                    self._capture(process.stdout, stdout, counters[0], cell, "stdout")
                ),
                asyncio.create_task(
                    self._capture(process.stderr, stderr, counters[1], cell, "stderr")
                ),
            ]
            try:
                async with asyncio.timeout(min(300, max(1, timeout_s))):
                    await process.wait()
                    await asyncio.gather(*self.pumps)
            except TimeoutError:
                timed_out = True
            except asyncio.CancelledError as error:
                interrupted = error
            finally:
                await reap(process)
                await asyncio.gather(*self.pumps, return_exceptions=True)
                self.process = None
                self.pumps = []
            stdout_text, stderr_text = "".join(stdout), "".join(stderr)
            output = stdout_text + stderr_text
            truncated = any(c[0] > c[1] for c in counters)
            if timed_out or interrupted:
                self.checkpoint = None
                output += (
                    "\nHard timeout: child killed; variables reset, files retained"
                    if timed_out
                    else "\nCancelled: child stopped; files retained"
                )
            elif checkpoint.is_file() and not checkpoint.is_symlink():
                self.checkpoint = checkpoint
            else:
                self.checkpoint = None
            if truncated:
                marker = "\n[Output truncated; save large results to artifacts.]"
                output = self.preview(output, 262000) + marker
            variables = {}
            if metadata.is_file() and not metadata.is_symlink() and metadata.stat().st_size < 32000:
                try:
                    raw = json.loads(metadata.read_text()).get("variables", {})
                    variables = {str(k)[:200]: str(v)[:200] for k, v in list(raw.items())[:100]}
                except (ValueError, AttributeError, OSError):
                    pass
            result = {
                "ok": process.returncode == 0 and not timed_out and not interrupted,
                "returncode": process.returncode,
                "source_ref": source_ref,
                "output": output,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "output_truncated": truncated,
                "output_characters": sum(c[0] for c in counters),
                "variables": variables,
            }
            record = {
                "cell": cell,
                "code": code,
                "kind": "script" if script else "python",
                "started": started,
                "elapsed_s": time.time() - started,
                "dataset_ref": self.dataset_id,
                **result,
            }
            proof = record_cell(
                self.run,
                record,
                source,
                self.data_root,
                before,
                source_text=code if source_ref else None,
            )
            result["ok"] = record["ok"]
            output = record["output"]
            result["output"] = output
            record["execution_ref"] = proof
            write_json(private / "result.json", record)
            self.latest_execution = {
                "execution_ref": proof,
                "ok": result["ok"],
                "code": self.preview(executed_source),
                "output": self.preview(output),
            }
            if not result["ok"]:
                self.latest_failure = self.latest_execution
            with (self.run / "execution.jsonl").open("a") as out:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
            cells = ensure_real_dir(self.work / "research_cells", root=self.work)
            write_json(cells / f"cell_{cell:04d}.output.json", result, root=self.work)
            self.emit(
                "tool.end",
                cell=cell,
                ok=result["ok"],
                returncode=result["returncode"],
                elapsed_s=record["elapsed_s"],
                output=output[-24000:],
                dataset_ref=self.dataset_id,
                execution_ref=proof,
            )
            if interrupted:
                raise interrupted
            return {
                **result,
                "cell": cell,
                "execution_ref": proof,
                "dataset_ref": self.dataset_id,
                "output": self.preview(output),
                "full_output": str(filename.with_suffix(".output.json").relative_to(self.work)),
                "output_truncated": truncated or len(output) > 24000,
            }

    def execution_context(self):
        return {
            "namespace": "current research turn only; new turn starts empty",
            "checkpoint_available": self.checkpoint is not None,
            "latest_execution": self.latest_execution,
            "latest_failure": self.latest_failure,
        }

    @staticmethod
    def preview(text, limit=24000):
        if len(text) <= limit:
            return text
        return (
            text[: limit // 2] + "\n[... truncated; use read_execution ...]\n" + text[-limit // 2 :]
        )

    def list_executions(self, offset=0, limit=20):
        import sqlite3

        path = self.run / ".execution/ledger.sqlite"
        if not path.exists():
            return []
        with sqlite3.connect(path) as db:
            rows = db.execute(
                "SELECT id,doc FROM cells ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (min(100, max(1, limit)), max(0, offset)),
            ).fetchall()
        return [{"execution_ref": key, **json.loads(doc)} for key, doc in rows]

    def read_execution(self, execution_ref, offset=0, limit=12000):
        import re

        if not re.fullmatch(r"cell-[0-9]{6,}", execution_ref):
            raise ValueError("Invalid execution_ref")
        private = self.run / ".execution" / execution_ref.replace("-", "_")
        record = json.loads((private / "result.json").read_text())
        offset, limit = max(0, offset), min(32000, max(1, limit))
        fields = {
            "code": record["code"]
            if record.get("source_ref")
            else (private / "source.py").read_text(),
            **{k: record[k] for k in ("output", "stdout", "stderr")},
        }
        return {
            "execution_ref": execution_ref,
            "ok": record["ok"],
            "dataset_ref": record["dataset_ref"],
            "source_ref": record.get("source_ref"),
            "offset": offset,
            "output_truncated": record["output_truncated"],
            "fields": {
                k: {
                    "text": v[offset : offset + limit],
                    "characters": len(v),
                    "next_offset": offset + limit if len(v) > offset + limit else None,
                }
                for k, v in fields.items()
            },
        }

    async def close(self):
        from .sandboxing import reap

        await reap(self.process)
        self.process = None
        self.checkpoint = None
