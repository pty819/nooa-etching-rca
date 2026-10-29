import asyncio

import pytest
from nooa.unifiedllm import FakeLLMClient
from test_capabilities import public_case
from test_execution_feedback import cell

from factor_demo.autoresearch_agent import AutoresearchAgent
from factor_demo.execution_observations import ExecutionObservations
from factor_demo.local_reflection import reflection_active
from factor_demo.research_context import install_research_context
from factor_demo.research_workspace import PythonWorkspace


def test_inner_failure_and_outer_crash_reach_actual_next_request_with_parent_links(tmp_path):
    async def run():
        workspace = PythonWorkspace(public_case(tmp_path / "case"))
        client = FakeLLMClient(
            scripted_responses=[
                cell(
                    "await self.code_write('score','def score(x):\\n return x/0')\nr=await self.code_experiment('score',1,'assert score(2)==4')\nraise ValueError('OUTER_FAILED_AFTER_INNER')",
                    1,
                ),
                cell("return_result(ResearchReply(summary='observed',intent='visualization'))", 2),
            ],
            strict_exhaustion=True,
        )
        agent = AutoresearchAgent(workspace, llm=client)
        install_research_context(agent, "repair score")
        try:
            assert (await agent.research("inspect failed formula")).summary == "observed"
            request = str(client.calls[1].messages)
            for text in (
                "ZeroDivisionError",
                "return x/0",
                "OUTER_FAILED_AFTER_INNER",
                "unresolved_failures",
                "observation-",
            ):
                assert text in request
            context = workspace.observations.context()
            failure = next(
                row for row in context["unresolved_failures"] if row["kind"] == "experiment"
            )
            original = workspace.observations.get(failure["observation_id"])
            parent = workspace.observations.get(original["parent_id"])
            assert parent["kind"] == "orchestration" and parent["status"] == "failed"
            restored = ExecutionObservations(workspace.run)
            assert restored.get(original["observation_id"])["checks"] == "assert score(2)==4"
            assert "ZeroDivisionError" in restored.read(original["observation_id"])["content"]
        finally:
            await workspace.close()
            await agent.aclose()

    asyncio.run(run())


def test_repair_replays_original_checks_and_unrelated_success_cannot_resolve(tmp_path):
    async def run():
        workspace = PythonWorkspace(public_case(tmp_path / "case"))
        agent = AutoresearchAgent(workspace, llm=FakeLLMClient())
        try:
            await agent.code_write("score", "def score(x):\n return x/0")
            failed = await agent.code_experiment(
                "score", 1, 'assert score(PARAMETERS["x"])==4', parameters={"x": 2}
            )
            identity = failed["observation_id"]
            await workspace.execute("unrelated=42")
            assert identity in {
                r["observation_id"] for r in workspace.observations.context()["unresolved_failures"]
            }
            with pytest.raises(ValueError, match="revision"):
                await agent.verify_repair(identity, "score", 1)
            await agent.code_edit("score", 1, "x/0", "x*2")
            with pytest.raises(ValueError, match="parameters"):
                await agent.verify_repair(identity, "score", 2, parameters={"x": 3})
            checked = await agent.verify_repair(identity, "score", 2)
            assert checked["status"] == "verified" and checked["goal_satisfied"] is None
            remaining = workspace.observations.context()["unresolved_failures"]
            assert not remaining, remaining
            wrong_goal = await agent.verify_goal(
                "score(2) equals 5", checked["experiment_id"], "assert score(2)==5"
            )
            assert wrong_goal["supplied_criterion_passed"] is False
            good = await agent.verify_goal(
                "score(2) equals 4", checked["experiment_id"], "assert score(2)==4"
            )
            assert good["supplied_criterion_passed"] is True
            # A passed goal does not dismiss the independently failed goal/check.
            assert workspace.observations.context()["unresolved_count"] > 0
            token = reflection_active.set(True)
            try:
                assert "score" in (await agent.read_observation(identity))["content"]
                with pytest.raises(PermissionError):
                    await agent.verify_repair(identity, "score", 2)
                with pytest.raises(PermissionError):
                    await agent.verify_goal("goal", checked["experiment_id"], "assert True")
            finally:
                reflection_active.reset(token)
        finally:
            await workspace.close()
            await agent.aclose()

    asyncio.run(run())


def test_scientific_original_path_verification_and_dataset_changes_are_not_repairs(tmp_path):
    async def run():
        workspace = PythonWorkspace(public_case(tmp_path / "case"))
        agent = AutoresearchAgent(workspace, llm=FakeLLMClient())
        try:
            await agent.code_write("score", "def score(x):\n return x/0")
            await agent.code_run("score", 1)
            failure = await workspace.execute("assert score(2)==4")
            await agent.code_edit("score", 1, "x/0", "x*2")
            result = await agent.verify_repair(failure["observation_id"], "score", 2)
            assert result["status"] == "verified"
            await agent.code_write("another", "answer=2")
            bad = await agent.code_experiment("another", 1, "assert answer==4")
            await agent.code_edit("another", 1, "2", "4")
            (workspace.data_root / "recipe.json").write_text("{}")
            changed = await agent.verify_repair(bad["observation_id"], "another", 2)
            assert changed["status"] == "unverified"
            assert bad["observation_id"] in {
                r["observation_id"] for r in workspace.observations.context()["unresolved_failures"]
            }
        finally:
            await workspace.close()
            await agent.aclose()

    asyncio.run(run())


def test_read_only_reflection_receives_same_execution_evidence(tmp_path):
    from nooa.strategies.reflexion import ReflectionOutput
    from nooa.unifiedllm import LLMResponse

    from factor_demo.research_models import ResearchReply

    async def run():
        workspace = PythonWorkspace(public_case(tmp_path / "case"))
        verdict = ReflectionOutput(is_satisfactory=False, issues=["fix observed formula"])
        responses = []
        for i in range(2):
            responses += [
                cell(
                    "return_result({'assessment':'failed path','issues':['formula'],'suggested_checks':['replay original']})",
                    i + 1,
                ),
                LLMResponse(content=verdict.model_dump_json(), parsed=verdict),
            ]
        client = FakeLLMClient(scripted_responses=responses, strict_exhaustion=True)
        agent = AutoresearchAgent(workspace, llm=client)
        install_research_context(agent, "inspect existing evidence")
        try:
            failed = await workspace.execute("raise ValueError('REFLECTION_EXECUTION_SENTINEL')")
            count = len(workspace.list_executions())
            result = await agent.inspect_candidate(
                ResearchReply(summary="candidate", intent="visualization")
            )
            assert result["status"] == "not_satisfactory"
            observed = str(client.calls[0].messages)
            assert "execution_evidence" in observed
            assert "REFLECTION_EXECUTION_SENTINEL" in observed
            assert failed["observation_id"] in observed
            assert len(workspace.list_executions()) == count
        finally:
            await workspace.close()
            await agent.aclose()

    asyncio.run(run())
