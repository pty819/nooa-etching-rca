import asyncio

import pytest
from nooa.unifiedllm import FakeLLMClient
from test_capabilities import public_case
from test_execution_feedback import cell

from factor_demo.autoresearch_agent import AutoresearchAgent
from factor_demo.code_buffers import CodeBuffers
from factor_demo.local_reflection import reflection_active
from factor_demo.research_context import install_research_context
from factor_demo.research_workspace import PythonWorkspace


def test_revision_conflicts_and_ambiguous_edits_leave_history_unchanged(tmp_path):
    store = CodeBuffers(tmp_path)
    first = store.write("features", "x = 1\nx = 1\n")
    with pytest.raises(ValueError, match="exactly one"):
        store.write("features", "", 1, old="x = 1", new="x = 2")
    with pytest.raises(ValueError, match="conflict"):
        store.write("features", "different")
    with pytest.raises(ValueError):
        store.get("../escape")
    second = store.write("features", "", 1, old="x = 1\nx = 1", new="x = 2")
    assert second["revision"] == 2 and second["executed"] is False
    restored = CodeBuffers(tmp_path)
    assert restored.get("features", 1)["sha256"] == first["sha256"]
    assert restored.get("features")["source"] == "x = 2\n"
    assert restored.catalog() == [{"name": "features", "revision": 2}]


def test_virtual_revisions_keep_original_function_traceback_and_do_not_replay(tmp_path):
    async def run():
        root = public_case(tmp_path / "case")
        worker = PythonWorkspace(root)
        agent = AutoresearchAgent(worker, llm=FakeLLMClient())
        source = 'counter = globals().get("counter", 0) + 1\ndef score(x):\n    return x / 0\n'
        try:
            await agent.code_write("features", source)
            first = await agent.code_run("features", 1)
            assert first["ok"] and first["source_ref"] == "cell://features@1"
            await agent.run_code("old_score = score")
            edit = await agent.code_edit("features", 1, "return x / 0", "return x / 2")
            assert edit["revision"] == 2
            before = await agent.run_code("print(counter)\nscore(2)")
            assert not before["ok"] and "cell://features@1" in before["output"]
            assert "return x / 0" in before["output"]
            assert before["stdout"].strip() == "1"
            assert (await agent.code_run("features", 2))["ok"]
            after = await agent.run_code("print(counter, score(2))\nold_score(2)")
            assert after["stdout"].strip() == "2 1.0"
            assert "cell://features@1" in after["output"]
            assert "return x / 0" in after["output"]
            archived = await agent.read_execution(first["execution_ref"])
            assert archived["source_ref"] == "cell://features@1"
            assert archived["fields"]["code"]["text"] == source
            for cell_dir in (root / ".execution").glob("cell_*"):
                request = __import__("json").loads((cell_dir / "request.json").read_text())
                if "code" in request:
                    assert not (cell_dir / "source.py").exists()
                    assert not (cell_dir / "source.txt").exists()
            assert not (worker.work / "research_cells/cell_0001.py").exists()
            page = await agent.code_read("features", 1, offset=5, limit=9)
            assert page["content"] == source[5:14] and page["next_offset"] == 14
            assert (await agent.code_read("features", 1))["content"] == source
            with pytest.raises(ValueError):
                await agent.code_run("features", 0)
            token = reflection_active.set(True)
            try:
                with pytest.raises(PermissionError):
                    await agent.code_write("forbidden", "x = 1")
            finally:
                reflection_active.reset(token)
            worker.research_enabled = False
            with pytest.raises(PermissionError):
                await agent.code_edit("features", 2, "return", "yield")
            with pytest.raises(PermissionError):
                await agent.code_run("features", 2)
        finally:
            await worker.close()
            await agent.aclose()

    asyncio.run(run())


def test_codeact_can_edit_and_execute_buffers_through_declared_tools(tmp_path):
    async def run():
        worker = PythonWorkspace(public_case(tmp_path / "case"))
        client = FakeLLMClient(
            scripted_responses=[
                cell(
                    "v = await self.code_write('calc', 'def score(x):\\n    return x / 0\\n')\nr = await self.code_run('calc', v['revision'])\nf = await self.run_code('score(2)')",
                    1,
                ),
                cell(
                    "v = await self.code_edit('calc', 1, 'return x / 0', 'return x / 2')\nawait self.code_run('calc', v['revision'])\nr = await self.run_code('print(score(2))')\nassert r['output'].strip() == '1.0'\nreturn_result(ResearchReply(summary='repaired', intent='visualization'))",
                    2,
                ),
            ],
            strict_exhaustion=True,
        )
        agent = AutoresearchAgent(worker, llm=client)
        install_research_context(agent, "buffer regression")
        try:
            reply = await agent.research("repair")
            assert reply.summary == "repaired"
            observed = str(client.calls[1].messages)
            assert "ZeroDivisionError" in observed and "cell://calc@1" in observed
            assert (await agent.code_read("calc", 1))["revision"] == 1
            assert (await agent.code_read("calc", 2))["revision"] == 2
        finally:
            await worker.close()
            await agent.aclose()

    asyncio.run(run())
