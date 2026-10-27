import asyncio
import json

import pytest
from nooa.unifiedllm import FakeLLMClient, LLMResponse, ToolCall
from test_capabilities import public_case

from factor_demo.autoresearch_agent import AutoresearchAgent
from factor_demo.research_context import install_research_context
from factor_demo.research_workspace import PythonWorkspace


def cell(code, number):
    return LLMResponse(
        parts=(
            ToolCall(
                id=f"feedback-{number}", name="python_cell", arguments=json.dumps({"code": code})
            ),
        ),
        finish_reason="tool_calls",
    )


def test_unprinted_failure_and_outer_traceback_reach_next_model_request(tmp_path):
    async def run():
        worker = PythonWorkspace(public_case(tmp_path / "case"))
        client = FakeLLMClient(
            scripted_responses=[
                cell("def broken():\n    return 1 / 0", 1),
                cell("broken()", 2),
                cell(
                    "ignored = await self.run_code(\"print('x' * 300000)\\nraise ValueError('INNER_FAILURE')\")",
                    3,
                ),
                cell("return_result(ResearchReply(summary='done', intent='visualization'))", 4),
            ],
            strict_exhaustion=True,
        )
        agent = AutoresearchAgent(worker, llm=client)
        install_research_context(agent, "feedback")
        try:
            await agent.research("probe")
            outer = str(client.calls[2].messages)
            assert "ZeroDivisionError" in outer and "broken" in outer and "return 1 / 0" in outer
            observation = str(client.calls[3].messages)
            assert "[Host execution observations]" in observation
            assert "ValueError: INNER_FAILURE" in observation
            record = worker.read_execution(worker.latest_execution["execution_ref"])
            assert "INNER_FAILURE" in record["fields"]["code"]["text"]
            tail = worker.read_execution(
                record["execution_ref"], offset=record["fields"]["output"]["characters"] - 12000
            )
            assert "INNER_FAILURE" in tail["fields"]["output"]["text"]
            assert worker.list_executions()[0]["execution_ref"] == record["execution_ref"]
            huge = await worker.execute("print('x' * 300000)\nraise ValueError('HUGE_TAIL')")
            assert huge["output_truncated"] and "HUGE_TAIL" in huge["output"]
            with pytest.raises(ValueError):
                worker.read_execution("../outside")
        finally:
            await worker.close()
            await agent.aclose()

    asyncio.run(run())


def test_execution_archive_survives_new_workspace_and_worker_file_changes(tmp_path):
    async def run():
        root = public_case(tmp_path / "case")
        worker = PythonWorkspace(root)
        try:
            result = await worker.execute("def broken():\n    return 1 / 0")
            failure = await worker.execute("broken()")
            assert not failure["ok"] and "ZeroDivisionError" in failure["output"]
            (worker.work / "research_cells/cell_0001.py").write_text("tampered")
            restored = PythonWorkspace(root)
            assert (
                "def broken"
                in restored.read_execution(result["execution_ref"])["fields"]["code"]["text"]
            )
            assert restored.execution_context()["checkpoint_available"] is False
            assert restored.list_executions()[0]["ok"] is False
        finally:
            await worker.close()

    asyncio.run(run())


def test_stderr_is_streamed_and_retains_tail_when_collection_is_bounded(tmp_path):
    async def run():
        events = []

        class Sink:
            def emit(self, kind, **data):
                events.append((kind, data))

        worker = PythonWorkspace(public_case(tmp_path / "case"))
        worker.events = Sink()
        try:
            result = await worker.execute(
                "import sys\nprint('EARLY_STDERR', file=sys.stderr)\nprint('x' * 300000, file=sys.stderr)\nraise ValueError('STDERR_TAIL')"
            )
            assert not result["ok"] and result["output_truncated"]
            assert "STDERR_TAIL" in result["stderr"] and "STDERR_TAIL" in result["output"]
            assert any(
                kind == "tool.output"
                and data.get("source") == "stderr"
                and "EARLY_STDERR" in data["text"]
                for kind, data in events
            )
        finally:
            await worker.close()

    asyncio.run(run())
