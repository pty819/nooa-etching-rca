"""Separate read-only reviewer; provider credentials remain in the trusted host."""

import json
import os

from nooa import Agent, strategy
from nooa.config.strategy_config import PredictConfig
from nooa.strategies import PredictStrategy

from .providers import PRIVATE_CONFIG, Provider, load_provider
from .research_models import ReviewResult


def choose_provider(primary, settings):
    requested = settings.review_provider
    if requested == "disabled":
        return None
    candidates = []
    if requested not in {"auto", "disabled"}:
        candidates = [requested]
    elif PRIVATE_CONFIG.exists():
        candidates = list(json.loads(PRIVATE_CONFIG.read_text()))
    if requested == "auto" and os.getenv("FACTOR_REVIEW_MODEL"):
        candidates.insert(0, "custom-review")
    for name in candidates:
        try:
            if name == "custom-review":
                provider = Provider(
                    name,
                    os.environ["FACTOR_REVIEW_MODEL"],
                    os.environ["FACTOR_REVIEW_API_BASE"],
                    os.getenv("FACTOR_REVIEW_API_STYLE", "openai"),
                    os.environ["FACTOR_REVIEW_API_KEY"],
                )
            else:
                provider = load_provider(name)
        except (KeyError, OSError, ValueError):
            continue
        if provider.model != primary.model:
            return provider
    return None


class EvidenceReviewer(Agent):
    """Review only the supplied verified bundle. No research tools or code execution."""

    @strategy(PredictStrategy(config=PredictConfig(max_retries=2)))
    async def review(self, evidence_bundle: dict) -> ReviewResult:
        """审查当前报告实际涉及的声明。数值来源、验证级别和登记检查由宿主给出。

        区分已核对的数值、可重复的探索和未确认的机制；不把分类分数当根因证明。
        仅给有依据的问题，指出单个claim_id及evidence_ids，不执行代码、不补造实验。
        输出结构必须满足ReviewResult：issues是对象数组；每项evidence_ids必须是字符串数组，
        无引用用[]，单个引用用["id"]，不得给null或直接字符串。claim_id用单个已有ID。
        没有重算的事项不能冒充已重算；只在用户当前任务需要时要求额外检查。
        """
        ...
