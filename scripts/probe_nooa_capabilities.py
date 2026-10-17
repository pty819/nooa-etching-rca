"""Offline executable probes against the pinned NOOA package; no provider API calls.

Verifies composition and control flow, not the scientific quality of reflection.
Run: uv run python scripts/probe_nooa_capabilities.py
"""

import asyncio
import json
from pathlib import Path

from nooa import Agent, hidden, strategy

with hidden:
    from nooa.config import CodeActConfig, LoopGuardConfig
    from nooa.config.strategy_config import ReflexionConfig
    from nooa.errors import LoopDetectedError
    from nooa.events import Feedback, LoopGuardTriggered
    from nooa.strategies import CodeActV2
    from nooa.strategies.reflexion import ReflectionOutput, ReflexionStrategy
    from nooa.strategy_validation import InvariantError
    from nooa.unifiedllm import FakeLLMClient, LLMResponse, ToolCall


def cell(code, index):
    return LLMResponse(
        parts=(
            ToolCall(id=f"cell-{index}", name="python_cell", arguments=json.dumps({"code": code})),
        ),
        finish_reason="tool_calls",
    )


def reflection(accepted):
    value = ReflectionOutput(
        is_satisfactory=accepted,
        issues=[] if accepted else ["Need an exact verified result"],
        suggestions=[] if accepted else ["Revise the answer"],
        reasoning="offline scripted verdict",
    )
    return LLMResponse(content=value.model_dump_json(), parsed=value)


class ReflectionProbe(Agent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.effects = 0

    def record_attempt(self):
        """Record a deterministic side effect to expose repeated execution."""
        self.effects += 1

    @strategy(
        ReflexionStrategy(
            base=CodeActV2(config=CodeActConfig(prefill=None, max_iterations=3, max_retries=1)),
            config=ReflexionConfig(max_iterations=2),
        )
    )
    async def solve(self, question: str) -> int:
        """Return an integer answer for this offline probe."""
        ...

    @strategy(
        ReflexionStrategy(
            base=CodeActV2(config=CodeActConfig(prefill=None, max_iterations=3, max_retries=1)),
            config=ReflexionConfig(max_iterations=1),
        )
    )
    async def exhausted(self) -> int:
        """Exercise reflection budget exhaustion."""
        ...


def reject_unverified(agent, result, call):
    if result != "verified":
        raise InvariantError("Return verified evidence")


class ValidationProbe(Agent):
    @strategy(
        CodeActV2(
            config=CodeActConfig(prefill=None, max_retries=2, postconditions=[reject_unverified])
        )
    )
    async def solve(self) -> str:
        """Return a result satisfying the deterministic postcondition."""
        ...


class GuardProbe(Agent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.effects = 0

    def repeat(self):
        """Return the same observable result."""
        self.effects += 1
        return "unchanged evidence"

    @strategy(
        CodeActV2(
            config=CodeActConfig(
                prefill=None,
                max_retries=10,
                loop_guard=LoopGuardConfig(repeat_threshold=3, window=8),
            )
        )
    )
    async def solve(self) -> str:
        """Exercise identical action/output loop detection."""
        ...


async def main():
    result = {
        "nooa_commit": "1454b78c8f0a85c97eab1c6a91cc8cfc95b34de5",
        "provider_calls": 0,
        "scope": "offline scripted responses through real NOOA strategies",
    }
    fake = FakeLLMClient(
        scripted_responses=[
            cell("self.record_attempt()\nreturn_result(10)", 1),
            reflection(False),
            cell("self.record_attempt()\nreturn_result(42)", 2),
            reflection(True),
        ],
        strict_exhaustion=True,
    )
    agent = ReflectionProbe(llm=fake)
    try:
        value = await agent.solve("Revise once after a negative reflection")
        assert value == 42 and fake.call_count == 4 and agent.effects == 2
        result["reflexion_codeact_v2"] = {
            "returned": value,
            "llm_attempts": fake.call_count,
            "side_effect_executions": agent.effects,
            "feedback_events": sum(isinstance(e, Feedback) for e in agent.event_manager.values()),
        }
    finally:
        await agent.aclose()
    fake = FakeLLMClient(
        scripted_responses=[cell("return_result(10)", 1), reflection(False)], strict_exhaustion=True
    )
    agent = ReflectionProbe(llm=fake)
    try:
        value = await agent.exhausted()
        assert value == 10 and fake.call_count == 2
        result["unsatisfactory_at_limit"] = {
            "returned": value,
            "last_reflection_accepted": False,
            "raises": False,
        }
    finally:
        await agent.aclose()
    fake = FakeLLMClient(
        scripted_responses=[
            cell("return_result('unverified')", 1),
            cell("return_result('verified')", 2),
        ],
        strict_exhaustion=True,
    )
    agent = ValidationProbe(llm=fake)
    try:
        assert await agent.solve() == "verified" and fake.call_count == 2
        result["v2_postcondition"] = {
            "rejected_then_recovered": True,
            "llm_attempts": fake.call_count,
        }
    finally:
        await agent.aclose()
    fake = FakeLLMClient(
        scripted_responses=[cell("print(self.repeat())", i) for i in range(1, 6)],
        strict_exhaustion=True,
    )
    agent = GuardProbe(llm=fake)
    try:
        try:
            await agent.solve()
        except LoopDetectedError:
            pass
        else:
            raise AssertionError("Loop did not stop")
        actions = [
            e.action for e in agent.event_manager.values() if isinstance(e, LoopGuardTriggered)
        ]
        assert actions == ["nudged", "stopped"]
        result["v2_loop_guard"] = {"actions": actions, "actual_tool_executions": agent.effects}
    finally:
        await agent.aclose()
    target = Path("docs/nooa-capability-probes.json")
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
