"""Track native experimental Reflexion verdicts and contain its public tool effects."""

from contextvars import ContextVar

from nooa.strategies.reflexion import ReflexionStrategy

reflection_active: ContextVar[bool] = ContextVar("local_reflection_active", default=False)


class TrackedReflexion(ReflexionStrategy):
    # The native wrapper delegates block definitions, whose expressions resolve
    # against the outer strategy. Preserve CodeActV2's live cell/tool context too.
    async def python_cell_context(self, runtime):
        return await self.base.python_cell_context(runtime)

    async def python_cell_state_context(self, runtime):
        return await self.base.python_cell_state_context(runtime)

    async def _reflect(self, runtime, call, result):
        verdict = await super()._reflect(runtime, call, result)
        runtime.agent._reflection_verdicts.append(verdict.model_dump())
        events = getattr(getattr(runtime.agent, "_workspace", None), "events", None)
        if events:
            events.emit(
                "reflection.verdict",
                iteration=len(runtime.agent._reflection_verdicts),
                **verdict.model_dump(),
            )
        return verdict
