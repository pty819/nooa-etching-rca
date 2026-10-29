"""NOOA CodeActV2 with a persistent credential-free tool-only worker."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import shutil
import tempfile
import time
import typing
import uuid
from pathlib import Path

from nooa import TextSkill
from nooa.events import ExecutionResult
from nooa.strategies import CodeActV2
from nooa.strategies.codeact import _ReturnResultSignal
from pydantic import BaseModel, TypeAdapter

from .cell_wire import MAX_FRAME, decode, dumps, encode, loads
from .sandboxing import (
    apply_cell_cgroup_pids,
    child_environment,
    ensure_child_subreaper,
    launch,
    prepare_guard_modules,
    reap,
)

TOOLS = frozenset(
    [
        "inspect_dataset",
        "list_datasets",
        "read_dataset_lineage",
        "derive_dataset",
        "select_dataset",
        "return_dataset",
        "request_data",
        "materialize_raw_evidence",
        "materialize_formula_evidence",
        "run_code",
        "write_file",
        "code_write",
        "code_edit",
        "code_read",
        "code_list",
        "code_run",
        "code_experiment",
        "code_read_experiment",
        "code_publish",
        "read_observation",
        "verify_repair",
        "verify_goal",
        "run_script",
        "reply",
        "list_artifacts",
        "read_artifact",
        "read_execution",
        "list_executions",
        "create_formula_study",
        "begin_formula_comparison",
        "register_formula",
        "evaluate_formula_set",
        "read_formula_study",
        "retire_formula_set",
        "freeze_formula_selection",
        "inspect_candidate",
        "spawn_research",
        "list_research",
        "wait_research",
        "read_branch_result",
        "guide_research",
        "cancel_research",
        "resume_research",
        "describe_dataset",
        "screen_features",
        "related_features",
        "inspect_features",
        "evaluate_candidate",
        "list_experiments",
        "record_finding",
    ]
)


def _cell_tool_timeout() -> float:
    raw = os.environ.get("FACTOR_CELL_TOOL_TIMEOUT", "330").strip()
    try:
        value = float(raw)
    except ValueError as error:
        raise RuntimeError("FACTOR_CELL_TOOL_TIMEOUT must be a number") from error
    if value <= 0:
        raise RuntimeError("FACTOR_CELL_TOOL_TIMEOUT must be positive")
    return value


def _cell_max_tool_calls() -> int:
    raw = os.environ.get("FACTOR_CELL_MAX_TOOL_CALLS", "64").strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError("FACTOR_CELL_MAX_TOOL_CALLS must be an integer") from error
    if value < 1:
        raise RuntimeError("FACTOR_CELL_MAX_TOOL_CALLS must be >= 1")
    return value


def contract_types():
    from . import dataset_models, formula_models, research_models
    from .legacy import models as legacy_models

    return {
        k: v
        for m in [research_models, formula_models, dataset_models, legacy_models]
        for k, v in vars(m).items()
        if isinstance(v, type) and issubclass(v, BaseModel) and v is not BaseModel
    }


class ExternalCell:
    def __init__(self, agent, call, timeout=60):
        self.agent, self.call, self.timeout = agent, call, timeout or 60
        self.registry = contract_types()
        self.temp = tempfile.TemporaryDirectory(prefix="nooa-codeact-")
        self.root = Path(self.temp.name).resolve()
        self.kit = self.root / "runtime"
        self.kit.mkdir()
        prepare_guard_modules(self.kit)
        self.work = self.root / "workspace"
        self.work.mkdir()
        for name in [
            "codeact_child.py",
            "cell_proxy.py",
            "cell_wire.py",
            "sandboxing.py",
            "session_cell.py",
        ]:
            shutil.copyfile(Path(__file__).with_name(name), self.kit / name)
        (self.kit / "contracts").mkdir()
        (self.kit / "contracts/__init__.py").write_text("")
        for name in ["research_models.py", "formula_models.py", "dataset_models.py"]:
            shutil.copyfile(Path(__file__).with_name(name), self.kit / "contracts" / name)
        shutil.copyfile(
            Path(__file__).parent / "legacy/models.py", self.kit / "contracts/legacy_models.py"
        )
        self.process = None
        self.checkpoint = None
        self.epoch = 0
        self.errors = None
        self.tools = {
            name: inspect.getdoc(getattr(agent, name)) or name
            for name in TOOLS
            if hasattr(agent, name)
        }
        self.tool_timeout = _cell_tool_timeout()
        self.max_tool_calls = _cell_max_tool_calls()

    @property
    def docs(self):
        """Export data-only text for currently active skills at each cell boundary."""
        registry = getattr(self.agent, "_knowledge_registry", None)
        if registry is None:
            return {}
        result = {}
        for registered_name in registry.activated():
            name = registered_name.rsplit(".", 1)[-1].replace("-", "_")
            skill = getattr(self.agent, name, None)
            if not name.startswith("_") and isinstance(skill, TextSkill):
                result[name] = inspect.getdoc(type(skill)) or ""
        return result

    async def dispatch(self, message):
        if message.get("name") not in self.tools or set(message) - {
            "kind",
            "cell",
            "sequence",
            "name",
            "args",
            "kwargs",
        }:
            raise PermissionError("Tool is not in this cell capability surface")
        method = getattr(self.agent, message["name"])
        args = decode(message.get("args", []), self.registry)
        kwargs = decode(message.get("kwargs", {}), self.registry)
        bound = inspect.signature(method).bind(*args, **kwargs)
        hints = typing.get_type_hints(method)
        for name, value in list(bound.arguments.items()):
            if name in hints:
                bound.arguments[name] = TypeAdapter(hints[name]).validate_python(value)
        if inspect.iscoroutinefunction(method):
            return await method(*bound.args, **bound.kwargs)
        return await asyncio.to_thread(method, *bound.args, **bound.kwargs)

    async def run_cell(self, code, *, execution_count=1):
        workspace = getattr(self.agent, "_workspace", None)
        if workspace is None:
            return await self._run_cell(code, execution_count=execution_count)
        from .execution_observations import parent_execution

        journal = workspace.observations
        identity = journal.record("orchestration", "running", code=code, epoch=self.epoch)
        token = parent_execution.set(identity)
        result = None
        error = None
        try:
            result = await self._run_cell(code, execution_count=execution_count)
            return result
        except BaseException as caught:  # archive cancellation before propagating
            error = caught
            raise
        finally:
            parent_execution.reset(token)
            doc = journal.get(identity)
            doc.update(
                status="failed" if error or (result and result.error) else "passed",
                epoch=self.epoch,
                state_effect="reset" if self.process is None else "retained",
                output=(result.stdout + "\n" + result.stderr) if result else "",
                error=str(error or (result.error if result else "")),
            )
            journal.save(doc)

    async def _run_cell(self, code, *, execution_count=1):
        token = uuid.uuid4().hex
        bindings = {k: v for k, v in self.call.bound_parameters().items() if k != "self"}
        config = {
            "cell": token,
            "tools": self.tools,
            "docs": self.docs,
            "bindings": encode(bindings),
            "code": code,
            "return_type": getattr(self.call.return_type, "__name__", "ResearchReply"),
            "method": self.call.method_name,
            "execution_count": execution_count,
            "work": str(self.work),
            "reads": [str(self.kit)],
        }
        ensure_child_subreaper()
        apply_cell_cgroup_pids()
        if self.process is None or self.process.returncode is not None:
            self.epoch += 1
            self.process = await asyncio.create_subprocess_exec(
                *launch([self.kit / "codeact_child.py"], [self.kit], [self.work]),
                cwd=self.work,
                env=child_environment(self.work),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=MAX_FRAME,
            )

            async def error_output():
                tail = b""
                while data := await self.process.stderr.read(4096):
                    tail = (tail + data)[-16000:]
                return tail.decode(errors="replace")

            self.errors = asyncio.create_task(error_output())
        process = self.process
        errors = self.errors
        process.stdin.write(dumps(config))
        await process.stdin.drain()
        invalidate = False
        deadline = time.monotonic() + float(self.timeout)
        sequence = 0
        observations = []

        def observed(result):
            if observations:
                result.stdout += "\n[Host execution observations]\n" + "\n".join(observations)
            return result

        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"External cell wall-clock budget exhausted ({self.timeout}s)"
                    )
                line = await asyncio.wait_for(process.stdout.readline(), max(0.001, remaining))
                if not line:
                    raise RuntimeError("External cell exited without a result: " + await errors)
                message = loads(line)
                if message.get("cell") != token:
                    raise ValueError("Wrong cell response identity")
                if message.get("kind") == "tool":
                    sequence += 1
                    if sequence > self.max_tool_calls or message.get("sequence") != sequence:
                        raise ValueError("Invalid tool sequence or too many calls in one cell")
                    tool_budget = min(self.tool_timeout, deadline - time.monotonic())
                    if tool_budget <= 0:
                        raise TimeoutError(
                            f"External cell wall-clock budget exhausted ({self.timeout}s)"
                        )
                    try:
                        value = await asyncio.wait_for(self.dispatch(message), tool_budget)
                        if isinstance(value, dict) and (
                            "execution_ref" in value or "observation_id" in value
                        ):
                            observations.append(
                                json.dumps(
                                    {
                                        k: value[k]
                                        for k in (
                                            "observation_id",
                                            "execution_ref",
                                            "ok",
                                            "output",
                                            "dataset_ref",
                                            "source_ref",
                                            "epoch",
                                            "state_effect",
                                            "runtime_status",
                                            "exception_type",
                                            "experiment_id",
                                            "status",
                                        )
                                        if k in value
                                    },
                                    ensure_ascii=False,
                                )
                            )
                        reply = {
                            "cell": token,
                            "sequence": sequence,
                            "ok": True,
                            "value": encode(value),
                        }
                    except asyncio.CancelledError:
                        raise
                    except TimeoutError:
                        raise TimeoutError(
                            f"External cell wall-clock budget exhausted ({self.timeout}s)"
                        )
                    except Exception as error:  # noqa: BLE001 - untrusted execution/service boundary
                        detail = (
                            "模型检查或数据请求未完成；宿主保留了状态"
                            if message["name"] in {"inspect_candidate", "request_data"}
                            else str(error)[:2000]
                        )
                        reply = {
                            "cell": token,
                            "sequence": sequence,
                            "ok": False,
                            "error": type(error).__name__ + ": " + detail,
                        }
                    process.stdin.write(dumps(reply))
                    await process.stdin.drain()
                    continue
                if message.get("kind") != "result":
                    raise ValueError("Invalid cell response")
                invalidate = message.get("status") == "exception" or "signal" in message
                result = ExecutionResult(
                    stdout=str(message.get("stdout", ""))[:32000],
                    stderr=str(message.get("stderr", ""))[:16000],
                    wrapper_line_offset=int(message.get("wrapper_line_offset", 0)),
                )
                if message.get("error"):
                    result.error = RuntimeError(
                        str(message["error"])[-16000:]
                        + (
                            "\nOrchestration kernel reset; prior tool effects remain recorded."
                            if invalidate
                            else ""
                        )
                    )
                if "signal" in message:
                    result.signal = _ReturnResultSignal(
                        result=decode(message["signal"], self.registry)
                    )
                elif "returned_value" in message:
                    result.returned_value = decode(message["returned_value"], self.registry)
                return observed(result)
        except asyncio.CancelledError:
            invalidate = True
            raise
        except TimeoutError as error:
            invalidate = True
            return observed(ExecutionResult(stdout="", error=RuntimeError(str(error)[:4000])))
        except Exception as error:  # noqa: BLE001 - untrusted execution/service boundary
            invalidate = True
            return observed(
                ExecutionResult(
                    stdout="", error=RuntimeError("External cell failed: " + str(error)[:4000])
                )
            )
        finally:
            if invalidate:
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), 0.5)
                except TimeoutError:
                    pass
                await reap(process)
                await errors
                self.process = None

    async def aclose(self):
        if self.process:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 0.5)
            except TimeoutError:
                pass
        await reap(self.process)
        if self.errors:
            await self.errors
        self.process = None
        self.temp.cleanup()


class SecureCodeActV2(CodeActV2):
    async def sandbox_context(self, runtime):
        return "外层代码在本次生成调用的长驻无凭据进程内执行，只能使用声明的工具代理和持续Python变量；运行异常使内核重置，已完成工具副作用不回滚；不能访问主控对象或项目文件。完整科学Python通过run_code/run_script执行，读取DATASET并保存artifacts。"

    def _create_sandbox_executor(self, runtime, call, builtins):
        return ExternalCell(runtime.agent, call, self.config.cell_timeout)
