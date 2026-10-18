"""Build native strategy instances from the mission's effective policy."""

from nooa.config import CodeActConfig, LoopGuardConfig
from nooa.config.strategy_config import ReflexionConfig
from nooa.runtime.restrictions import RESTRICTED_MODULES, RestrictionsConfig

from .evidence import research_postcondition, research_precondition
from .local_reflection import TrackedReflexion
from .secure_codeact import SecureCodeActV2


def research_strategy(policy, local=False):
    return SecureCodeActV2(
        config=CodeActConfig(
            max_iterations=6 if local else policy.max_research_iterations,
            max_retries=policy.validation_retries,
            cell_timeout=60 if local else 330,
            execution_backend="sandbox",
            loop_guard=LoopGuardConfig(
                repeat_threshold=policy.loop_repeat_threshold, window=policy.loop_window
            )
            if policy.loop_guard
            else None,
            preconditions=[research_precondition],
            postconditions=[research_postcondition],
            restrictions=RestrictionsConfig(restricted_imports=RESTRICTED_MODULES),
        )
    )


def reflection_strategy(policy):
    return TrackedReflexion(
        base=research_strategy(policy, local=True),
        config=ReflexionConfig(max_iterations=policy.reflexion_iterations),
    )
