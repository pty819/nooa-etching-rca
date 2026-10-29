import asyncio

import pytest
from nooa.unifiedllm import FakeLLMClient
from test_capabilities import public_case
from test_execution_feedback import cell

from factor_demo.autoresearch_agent import AutoresearchAgent
from factor_demo.code_experiments import CodeExperiments
from factor_demo.execution_provenance import artifact_proof
from factor_demo.research_context import install_research_context
from factor_demo.research_workspace import PythonWorkspace


def test_failed_candidates_checks_and_timeout_preserve_main_kernel_and_files(tmp_path):
    async def run():
        workspace = PythonWorkspace(public_case(tmp_path / "case"))
        agent = AutoresearchAgent(workspace, llm=FakeLLMClient())
        try:
            main = await workspace.execute(
                "import os\nfrom pathlib import Path\npid=os.getpid()\nvalues=[7]\nalias=values\n"
                "Path('artifacts/good.txt').write_text('good')"
            )
            await agent.code_write(
                "bad",
                "from pathlib import Path\nPath('artifacts/good.txt').write_text('bad')\nvalues=[99]\nraise ValueError('wrong formula')",
            )
            failure = await agent.code_experiment("bad", 1, "assert values == [7]")
            assert failure["status"] == "failed"
            assert "wrong formula" in failure["executions"][-1]["output"]
            with pytest.raises(ValueError, match="checked"):
                await agent.code_publish(failure["experiment_id"])
            await agent.code_write("wrong", "answer=2")
            checked = await agent.code_experiment("wrong", 1, "assert answer==3")
            assert checked["status"] == "failed"
            assert checked["executions"][-1]["source_ref"] == "experiment://checks"
            await agent.code_write("slow", "import time\ntime.sleep(5)")
            timed = await agent.code_experiment("slow", 1, "assert True", timeout_s=0.2)
            assert timed["status"] in ("timeout", "failed")
            after = await workspace.execute(
                "assert os.getpid()==pid\nassert values is alias\nassert values==[7]\n"
                "assert Path('artifacts/good.txt').read_text()=='good'"
            )
            assert after["ok"], after["output"]
            assert after["epoch"] == main["epoch"]
        finally:
            await workspace.close()
            await agent.aclose()

    asyncio.run(run())


def test_exact_dependencies_parameters_publication_and_stale_evidence(tmp_path):
    async def run():
        workspace = PythonWorkspace(public_case(tmp_path / "case"))
        agent = AutoresearchAgent(workspace, llm=FakeLLMClient())
        try:
            await agent.code_write("dep", "scale=3")
            await agent.code_write(
                "calc",
                "from pathlib import Path\nanswer=scale*PARAMETERS['x']\nPath('artifacts/result.txt').write_text(str(answer))",
            )
            doc = await agent.code_experiment("calc", 1, "assert answer==6", {"dep": 1}, {"x": 2})
            assert doc["status"] == "checked", doc
            assert not list((workspace.work / "artifacts").iterdir())
            assert [s["source_ref"] for s in doc["sources"]] == ["cell://dep@1", "cell://calc@1"]
            published = await agent.code_publish(doc["experiment_id"])
            name = next(iter(published["published"]))
            assert (workspace.work / name).read_text() == "6"
            assert artifact_proof(workspace.run, name)["experiment_id"] == doc["experiment_id"]
            await agent.code_edit("dep", 1, "3", "4")
            assert (await agent.code_read_experiment(doc["experiment_id"]))["validity"] == "stale"
            with pytest.raises(ValueError, match="stale"):
                artifact_proof(workspace.run, name)
            with pytest.raises(ValueError, match="current"):
                await agent.code_publish(doc["experiment_id"])
            # Explicit old revisions remain runnable but cannot publish as current.
            old = await agent.code_experiment("calc", 1, "assert answer==6", {"dep": 1}, {"x": 2})
            assert old["status"] == "checked" and old["validity"] == "stale"
            fresh = await agent.code_experiment("calc", 1, "assert answer==8", {"dep": 2}, {"x": 2})
            assert fresh["status"] == "checked" and fresh["validity"] == "current"
            # Changing any frozen input byte invalidates previously checked results.
            (workspace.dataset_session.path(fresh["dataset_ref"]) / "recipe.json").write_text("{}")
            assert CodeExperiments(workspace).get(fresh["experiment_id"])["validity"] == "stale"
            with pytest.raises(ValueError, match="current"):
                await agent.code_publish(fresh["experiment_id"])
        finally:
            await workspace.close()
            await agent.aclose()

    asyncio.run(run())


def test_publication_refuses_tampered_or_symlink_artifacts(tmp_path):
    async def run():
        workspace = PythonWorkspace(public_case(tmp_path / "case"))
        agent = AutoresearchAgent(workspace, llm=FakeLLMClient())
        try:
            await agent.code_write(
                "calc", "from pathlib import Path\nPath('artifacts/result.txt').write_text('ok')"
            )
            doc = await agent.code_experiment(
                "calc", 1, "assert Path('artifacts/result.txt').read_text()=='ok'"
            )
            stage = (
                workspace.run
                / ".execution/experiments"
                / doc["experiment_id"]
                / "workspace/artifacts/result.txt"
            )
            stage.write_text("tamper")
            with pytest.raises(ValueError, match="changed"):
                await agent.code_publish(doc["experiment_id"])
            stage.unlink()
            stage.symlink_to(workspace.data_root / "recipe.json")
            with pytest.raises(ValueError, match="symlink"):
                await agent.code_publish(doc["experiment_id"])
            assert not list((workspace.work / "artifacts").iterdir())
            with pytest.raises(ValueError, match="positive"):
                await agent.code_experiment("calc", 0, "assert True")
            with pytest.raises(ValueError, match="validation"):
                await agent.code_experiment("calc", 1, "")
        finally:
            await workspace.close()
            await agent.aclose()

    asyncio.run(run())


def test_experiment_tools_are_available_through_real_codeact_proxy(tmp_path):
    async def run():
        workspace = PythonWorkspace(public_case(tmp_path / "case"))
        client = FakeLLMClient(
            scripted_responses=[
                cell(
                    "await self.code_write('calc', 'answer=PARAMETERS[\"x\"]*2')\n"
                    "r=await self.code_experiment('calc',1,'assert answer==8',parameters={'x':4})\n"
                    "assert r['status']=='checked'\n"
                    "saved=await self.code_read_experiment(r['experiment_id'])\n"
                    "assert saved['parameters']=={'x':4}\n"
                    "return_result(ResearchReply(summary='checked',intent='visualization'))",
                    1,
                )
            ],
            strict_exhaustion=True,
        )
        agent = AutoresearchAgent(workspace, llm=client)
        install_research_context(agent, "experiment tool regression")
        try:
            assert (await agent.research("check explicit input")).summary == "checked"
            assert workspace.execution_context()["code_experiments"][0]["status"] == "checked"
        finally:
            await workspace.close()
            await agent.aclose()

    asyncio.run(run())


def test_experiment_cancellation_closes_worker_and_sandbox_blocks_main_workspace(tmp_path):
    async def run():
        started = asyncio.Event()

        class Events:
            def emit(self, kind, **data):
                if kind == "tool.output" and "EXPERIMENT_READY" in data.get("text", ""):
                    started.set()

        workspace = PythonWorkspace(public_case(tmp_path / "case"))
        agent = AutoresearchAgent(workspace, llm=FakeLLMClient())
        workspace.events = Events()
        task = None
        try:
            await workspace.execute(
                "from pathlib import Path\nkeep=[1]\nPath('artifacts/good').write_text('good')"
            )
            await agent.code_write(
                "isolated",
                f"from pathlib import Path\nmain=Path({str(workspace.work)!r})\ntry:\n main.joinpath('artifacts/good').write_text('bad')\nexcept (PermissionError,OSError):\n pass\nelse:\n raise AssertionError('sandbox allowed main write')\n",
            )
            checked = await agent.code_experiment("isolated", 1, "assert DATASET.is_dir()")
            assert checked["status"] == "checked", checked
            await agent.code_write(
                "wait",
                "import time,os\nprint('EXPERIMENT_READY',os.getpid(),flush=True)\ntime.sleep(20)",
            )
            task = asyncio.create_task(
                agent.code_experiment("wait", 1, "assert True", timeout_s=60)
            )
            await asyncio.wait_for(started.wait(), 15)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            docs = CodeExperiments(workspace).catalog()
            assert docs[0]["status"] == "cancelled"
            assert (
                await workspace.execute(
                    "assert keep==[1]\nassert Path('artifacts/good').read_text()=='good'"
                )
            )["ok"]
        finally:
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await workspace.close()
            await agent.aclose()

    asyncio.run(run())
