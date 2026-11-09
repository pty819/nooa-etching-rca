"""按任务累计 token 与工具调用用量，提供软告警与可选硬上限。"""

from typing import Any

from ..config import RunLimits


class RunBudget:
    """跨多轮累计模型用量和工具调用，不依赖上下文预算。"""

    def __init__(self, limits: RunLimits | None = None) -> None:
        """绑定提醒与硬上限配置，计数从零开始。

        Args:
            limits: 任务预算；None 使用仅告警、不设硬上限的默认配置。
        """
        self.limits = limits if limits is not None else RunLimits()
        self.tokens = 0
        self.tool_calls = 0
        self._token_warned = False
        self._tool_call_warned = False

    def record_usage(self, usage: dict[str, Any] | None) -> None:
        """累加一次模型响应报告的 token 用量；未报告时忽略。

        Args:
            usage: LiteLLM 返回的原始用量字典，可能包含 total_tokens 或
                prompt_tokens/completion_tokens。
        """
        if not usage:
            return
        total = usage.get("total_tokens")
        if not isinstance(total, int) or total <= 0:
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            if isinstance(prompt, int) and isinstance(completion, int):
                total = prompt + completion
            else:
                return
        self.tokens += total

    def record_tool_call(self) -> None:
        """累加一次即将执行的工具调用。"""
        self.tool_calls += 1

    def new_warnings(self) -> list[str]:
        """返回本次新增的软告警，每条阈值只提醒一次。

        Returns:
            尚未提醒过的阈值说明；没有新告警时为空列表。
        """
        warnings: list[str] = []
        if not self._token_warned and self.tokens >= self.limits.token_warning:
            self._token_warned = True
            warnings.append(
                f"本任务累计 token 约 {self.tokens}，已达到提醒阈值 {self.limits.token_warning}"
            )
        if not self._tool_call_warned and self.tool_calls >= self.limits.tool_call_warning:
            self._tool_call_warned = True
            warnings.append(
                f"本任务累计工具调用 {self.tool_calls} 次，"
                f"已达到提醒阈值 {self.limits.tool_call_warning}"
            )
        return warnings

    def enforce_tokens(self) -> None:
        """累计 token 超过硬上限时终止任务；未配置硬上限时不处理。

        Raises:
            RuntimeError: token 用量已超过配置的硬上限。
        """
        limit = self.limits.token_limit
        if limit is not None and self.tokens > limit:
            raise RuntimeError(
                f"本任务累计 token 约 {self.tokens}，已超过硬上限 {limit}；"
                "请收敛任务或按实际运行情况调整 RunLimits.token_limit"
            )

    def enforce_tool_calls(self) -> None:
        """累计工具调用超过硬上限时终止任务；未配置硬上限时不处理。

        Raises:
            RuntimeError: 工具调用次数已超过配置的硬上限。
        """
        limit = self.limits.tool_call_limit
        if limit is not None and self.tool_calls > limit:
            raise RuntimeError(
                f"本任务累计工具调用 {self.tool_calls} 次，已超过硬上限 {limit}；"
                "请收敛任务或按实际运行情况调整 RunLimits.tool_call_limit"
            )
