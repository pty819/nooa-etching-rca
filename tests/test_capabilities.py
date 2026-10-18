import asyncio
import hashlib
import json
from types import SimpleNamespace

import polars as pl
import pytest

from factor_demo.evidence import EvidenceError, EvidenceVerifier, research_postcondition
from factor_demo.live_events import LiveEvents
from factor_demo.research_models import EvidenceRef, Hypothesis, ResearchReply


def public_case(path):
    (path / "input").mkdir(parents=True)
    (path / "workspace/artifacts").mkdir(parents=True)
    pl.DataFrame(
        {
            "run_id": ["r1", "r2"],
            "split": ["train", "dev"],
            "recipe_id": ["fixed", "fixed"],
            "value": [4, 6],
        }
    ).write_parquet(path / "input/runs.parquet")
    (path / "input/recipe.json").write_text('{"recipe_id":"fixed"}')
    (path / "session.json").write_text(
        json.dumps(
            {
                "discovery_target_runs": 2,
                "discovery_control_runs": 0,
                "public_sha256": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in (path / "input").iterdir()
                },
            }
        )
    )
    return path


def report(path, value=4):
    from concurrent.futures import ThreadPoolExecutor

    from factor_demo.datasets import DatasetSession
    from factor_demo.research_workspace import PythonWorkspace

    async def produce():
        worker = PythonWorkspace(path)
        worker.dataset_session = DatasetSession(path)
        try:
            result = await worker.execute(
                "import polars as pl\npl.read_parquet(DATASET/'runs.parquet').select('run_id','value').write_csv('artifacts/evidence.csv')"
            )
            assert result["ok"], result["output"]
        finally:
            await worker.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: asyncio.run(produce())).result()
    return ResearchReply(
        summary="待审查的发现",
        evidence=[
            EvidenceRef(
                id="e1",
                claim="r1 measurement",
                artifact="artifacts/evidence.csv",
                column="value",
                where={"run_id": "r1"},
                value=value,
                unit="V",
                run_ids=["r1"],
            )
        ],
        hypotheses=[
            Hypothesis(
                id="h1",
                title="signal",
                status="weak",
                mathematical_evidence="r1 value",
                evidence_ids=["e1"],
                evidence_files=["artifacts/evidence.csv"],
                alternative_explanation="calibration",
                falsification_test="independent measurement",
            )
        ],
    )


def test_evidence_verifies_real_cells_scope_and_hash(tmp_path):
    root = public_case(tmp_path / "run")
    events = LiveEvents(root, "scope")
    verifier = EvidenceVerifier(root, events)
    candidate = report(root)
    proof = verifier.verify(candidate)
    assert proof["checks"][0]["actual"] == 4
    assert (
        proof["checks"][0]["sha256"]
        == hashlib.sha256((root / "workspace/artifacts/evidence.csv").read_bytes()).hexdigest()
    )
    candidate.evidence[0].value = 99
    with pytest.raises(EvidenceError, match="saved-table"):
        verifier.verify(candidate)
    candidate.evidence[0].value = 4
    candidate.evidence[0].run_ids = ["secret-test-run"]
    with pytest.raises(EvidenceError, match="outside"):
        verifier.verify(candidate)
    candidate.evidence[0].run_ids = ["r1"]
    candidate.evidence[0].sha256 = "wrong"
    with pytest.raises(EvidenceError, match="hash"):
        verifier.verify(candidate)
    candidate.evidence[0].sha256 = None
    candidate.hypotheses[0].evidence_ids = []
    with pytest.raises(EvidenceError, match="structured"):
        verifier.verify(candidate)
    assert any(e["type"] == "validation.issue" for e in events.since(0))
    events.close()


def test_aggregate_and_constant_recipe_precondition(tmp_path):
    root = public_case(tmp_path / "run")
    candidate = report(root)
    candidate.evidence[0].where = {}
    candidate.evidence[0].statistic = "mean"
    candidate.evidence[0].value = 5
    assert EvidenceVerifier(root).verify(candidate)["checks"][0]["actual"] == 5
    candidate.evidence[0].statistic = "value"
    with pytest.raises(EvidenceError, match="exactly one"):
        EvidenceVerifier(root).verify(candidate)
    from factor_demo.datasets import DatasetSession

    pl.DataFrame({"run_id": ["r1"], "split": ["test"], "recipe_id": ["fixed"]}).write_parquet(
        DatasetSession(root).path() / "runs.parquet"
    )
    with pytest.raises((ValueError, EvidenceError), match="hash changed"):
        EvidenceVerifier(root).scope()


def test_native_hooks_reflexion_and_context_survive_snapshot(tmp_path):
    from nooa.storage.sqlite import SQLiteStorageManager
    from nooa.strategies.reflexion import ReflectionOutput
    from nooa.unifiedllm import FakeLLMClient, LLMResponse, ToolCall

    from factor_demo.autoresearch_agent import AutoresearchAgent
    from factor_demo.local_reflection import reflection_active
    from factor_demo.research_context import install_research_context
    from factor_demo.research_workspace import PythonWorkspace

    root = public_case(tmp_path / "run")
    candidate = report(root)

    def cell(code, id):
        return LLMResponse(
            parts=(ToolCall(id=id, name="python_cell", arguments=json.dumps({"code": code})),),
            finish_reason="tool_calls",
        )

    def verdict():
        value = ReflectionOutput(is_satisfactory=False, issues=["needs evidence"])
        return LLMResponse(content=value.model_dump_json(), parsed=value)

    scripted = [
        cell(
            "return_result({'assessment':'not established','issues':['needs evidence'],'suggested_checks':['repeat']})",
            "one",
        ),
        verdict(),
        cell(
            "return_result({'assessment':'still not established','issues':['needs evidence'],'suggested_checks':['repeat']})",
            "two",
        ),
        verdict(),
    ]

    async def exercise():
        storage = SQLiteStorageManager(root / "conversation.sqlite")
        workspace = PythonWorkspace(root)
        agent = AutoresearchAgent(
            workspace,
            llm=FakeLLMClient(scripted_responses=scripted, strict_exhaustion=True),
            storage=storage,
        )
        try:
            install_research_context(agent, "inspect candidates")
            before_cells = len(list((root / "workspace/research_cells").glob("*.py")))
            assert agent._knowledge_registry.activated() == ["etch.physics", "etch.statistics"]
            result = await agent.inspect_candidate(candidate)
            # Confirm the actual NOOA model context advertises the broader stack.
            model_context = str(agent._llm.calls[0].messages)
            for library in ["NumPy", "SciPy", "scikit-learn", "aeon", "statsmodels"]:
                assert library in model_context
            assert result["status"] == "not_satisfactory" and len(result["verdicts"]) == 2
            assert len(list((root / "workspace/research_cells").glob("*.py"))) == before_cells
            token = reflection_active.set(True)
            try:
                with pytest.raises(PermissionError):
                    await agent.write_file("artifacts/no.py", "no")
                with pytest.raises(PermissionError):
                    await agent.run_code("print(1)")
                with pytest.raises(PermissionError):
                    await agent.reply("bad", "must not publish")
            finally:
                reflection_active.reset(token)
            agent.round_number = 3
            storage.save_snapshot(agent)
        finally:
            await agent.aclose()
            storage.close()
            await workspace.close()
        storage = SQLiteStorageManager(root / "conversation.sqlite")
        agent = AutoresearchAgent(PythonWorkspace(root), llm=FakeLLMClient(), storage=storage)
        try:
            assert storage.restore_latest_snapshot(agent)
            assert agent.round_number == 3
            install_research_context(agent, "continue")
            assert agent._knowledge_registry.activated()
        finally:
            await agent.aclose()
            storage.close()

    asyncio.run(exercise())


def test_postcondition_waits_for_active_branches(tmp_path):
    root = public_case(tmp_path / "run")
    candidate = report(root)
    manager = SimpleNamespace(
        mission="m",
        identity="main",
        store=SimpleNamespace(
            list=lambda: [{"mission": "m", "state": "running", "parent_id": "main"}]
        ),
    )
    agent = SimpleNamespace(_workspace=SimpleNamespace(run=root, events=None), _manager=manager)
    with pytest.raises(EvidenceError, match="still active"):
        research_postcondition(agent, candidate, SimpleNamespace(method_name="research"))


def test_review_evidence_bundle_uses_actual_native_reflection_verdict(tmp_path):
    from factor_demo.review_pipeline import review_bundle

    root = public_case(tmp_path / "case")
    events = LiveEvents(root, "m")
    try:
        events.emit(
            "reflection.verdict", iteration=1, is_satisfactory=False, issues=["incomplete read"]
        )
        candidate = report(root)
        candidate.summary = "Candidate assessment says PASS, which is not the native verdict"
        bundle = review_bundle(candidate, EvidenceVerifier(root).verify(candidate), events)
        native = bundle["host_observed_reflection_events_latest_first"][0]
        assert native["data"]["is_satisfactory"] is False
        assert bundle["allowed_claim_ids"] == ["summary", "scope", "h1", "e1"]
    finally:
        events.close()


def test_native_reflexion_keeps_negative_verdict_when_followup_budget_ends(tmp_path, caplog):
    from nooa.strategies.reflexion import ReflectionOutput
    from nooa.unifiedllm import FakeLLMClient, LLMResponse, ToolCall

    from factor_demo.autoresearch_agent import AutoresearchAgent
    from factor_demo.budget import BudgetExceeded
    from factor_demo.research_workspace import PythonWorkspace

    verdict = ReflectionOutput(is_satisfactory=False, issues=["not enough evidence"])
    responses = [
        LLMResponse(
            parts=(
                ToolCall(
                    id="one",
                    name="python_cell",
                    arguments=json.dumps(
                        {
                            "code": "return_result({'assessment':'candidate only','issues':[],'suggested_checks':[]})"
                        }
                    ),
                ),
            ),
            finish_reason="tool_calls",
        ),
        LLMResponse(content=verdict.model_dump_json(), parsed=verdict),
    ]

    class EndingClient(FakeLLMClient):
        budget_exhausted = False

        async def acall(self, *args, **kwargs):
            if self.call_count >= 2:
                self.budget_exhausted = True
                raise BudgetExceeded("fixture shared budget")
            return await super().acall(*args, **kwargs)

    root = public_case(tmp_path / "case")

    async def exercise():
        workspace = PythonWorkspace(root)
        agent = AutoresearchAgent(workspace, llm=EndingClient(scripted_responses=responses))
        try:
            result = await agent.inspect_candidate(report(root))
            assert result["status"] == "budget_exhausted"
            assert result["assessment"]["assessment"] == "candidate only"
            assert result["reason"] == "shared_model_budget"
            assert len(result["verdicts"]) == 1 and not result["verdicts"][0]["is_satisfactory"]
            assert json.loads(next((root / "reflections").glob("*.json")).read_text()) == result
        finally:
            await agent.aclose()
            await workspace.close()

    asyncio.run(exercise())
    assert "failed to resolve" not in caplog.text
