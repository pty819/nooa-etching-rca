"""验证全任务 token 与工具调用预算的计数、提醒和可选硬上限。"""

import asyncio
from copy import deepcopy

import litellm
import pytest

from factor_demo.autoresearch_agent_v2.agent.loop import AgentLoop
from factor_demo.autoresearch_agent_v2.agent.model import Model
from factor_demo.autoresearch_agent_v2.agent.typing import SessionState
from factor_demo.autoresearch_agent_v2.config import ModelConfig, RunLimits


def tool_call(name: str, content: str, call_id: str) -> dict:
    """构造一条原生工具调用。"""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": '{"content": "' + content + '"}'},
    }


def make_loop(tmp_path, limits: RunLimits) -> AgentLoop:
    """创建带预算配置的最小循环。"""
    return AgentLoop(
        Model(ModelConfig(model="openai/fixture-model", api_key="fixture-key")),
        SessionState(session_id="s1", agent_id="a1", task_id="t1", workdir=tmp_path),
        run_limits=limits,
    )


def response(tool_calls: list[dict], total_tokens: int) -> litellm.ModelResponse:
    """构造带用量报告的单条模型响应。"""
    return litellm.ModelResponse(
        choices=[{"message": {"role": "assistant", "tool_calls": tool_calls}}],
        usage=litellm.Usage(
            prompt_tokens=total_tokens - 5, completion_tokens=5, total_tokens=total_tokens
        ),
    )


def run_loop(monkeypatch, loop: AgentLoop, responses: list[litellm.ModelResponse]):
    """按顺序返回预置响应，并收集实际发送的请求。"""
    requests: list[dict] = []
    queue = iter(responses)

    async def complete(**kwargs):
        requests.append(deepcopy(kwargs))
        return next(queue)

    monkeypatch.setattr(litellm, "acompletion", complete)
    result = asyncio.run(loop.run("开始"))
    return result, requests


def test_token_warning_injected_once(monkeypatch, tmp_path, capsys):
    """累计 token 越过提醒阈值时，在完整工具批次之后提醒一次。"""
    loop = make_loop(tmp_path, RunLimits(token_warning=10, tool_call_warning=10_000))
    loop.tools.register_callable("inspect", lambda content: content, description="检查数据")
    result, _ = run_loop(
        monkeypatch,
        loop,
        [
            response([tool_call("inspect", "检查", "r1")], total_tokens=15),
            response([tool_call("finish", "完成", "f1")], total_tokens=15),
        ],
    )
    assert result == "完成"
    warnings = [
        message
        for message in loop.session.messages
        if (message.get("content") or "").startswith("[运行预算提醒]")
    ]
    assert len(warnings) == 1
    assert "提醒阈值 10" in warnings[0]["content"]
    capsys.readouterr()


def test_tool_call_warning_injected_once(monkeypatch, tmp_path, capsys):
    """累计工具调用越过提醒阈值时提醒一次。"""
    loop = make_loop(tmp_path, RunLimits(token_warning=10_000, tool_call_warning=1))
    loop.tools.register_callable("inspect", lambda content: content, description="检查数据")
    result, _ = run_loop(
        monkeypatch,
        loop,
        [
            response(
                [tool_call("inspect", "检查", "r1"), tool_call("inspect", "再检查", "r2")],
                total_tokens=10,
            ),
            response([tool_call("finish", "完成", "f1")], total_tokens=10),
        ],
    )
    assert result == "完成"
    warnings = [
        message
        for message in loop.session.messages
        if (message.get("content") or "").startswith("[运行预算提醒]")
    ]
    assert len(warnings) == 1
    assert "工具调用 2 次" in warnings[0]["content"]
    capsys.readouterr()


def test_hard_token_limit_raises(monkeypatch, tmp_path):
    """累计 token 超过硬上限时终止任务，不把错误伪装成工具结果。"""
    loop = make_loop(tmp_path, RunLimits(token_warning=5, token_limit=10))
    loop.tools.register_callable("inspect", lambda content: content, description="检查数据")
    with pytest.raises(RuntimeError, match="硬上限 10"):
        run_loop(
            monkeypatch,
            loop,
            [response([tool_call("inspect", "检查", "r1")], total_tokens=15)],
        )


def test_hard_tool_call_limit_raises(monkeypatch, tmp_path):
    """工具调用超过硬上限时终止任务，未执行的调用不写入结果。"""
    loop = make_loop(
        tmp_path,
        RunLimits(token_warning=10_000, tool_call_warning=10_000, tool_call_limit=2),
    )
    loop.tools.register_callable("inspect", lambda content: content, description="检查数据")
    with pytest.raises(RuntimeError, match="硬上限 2"):
        run_loop(
            monkeypatch,
            loop,
            [
                response(
                    [
                        tool_call("inspect", "一", "r1"),
                        tool_call("inspect", "二", "r2"),
                        tool_call("inspect", "三", "r3"),
                    ],
                    total_tokens=10,
                ),
            ],
        )
    tool_messages = [message for message in loop.session.messages if message["role"] == "tool"]
    assert [message["tool_call_id"] for message in tool_messages] == ["r1", "r2"]


def test_default_limits_do_not_raise(monkeypatch, tmp_path, capsys):
    """默认配置只有提醒、不设硬上限。"""
    loop = make_loop(tmp_path, RunLimits())
    loop.tools.register_callable("inspect", lambda content: content, description="检查数据")
    result, requests = run_loop(
        monkeypatch,
        loop,
        [
            response([tool_call("inspect", "检查", "r1")], total_tokens=10),
            response([tool_call("finish", "完成", "f1")], total_tokens=10),
        ],
    )
    assert result == "完成"
    assert len(requests) == 2
    assert not any(
        (message.get("content") or "").startswith("[运行预算提醒]")
        for message in loop.session.messages
    )
    capsys.readouterr()


def test_usage_callback_still_receives_usage(monkeypatch, tmp_path, capsys):
    """新增预算计数不改变原有的用量回调行为。"""
    seen: list[dict | None] = []
    model = Model(
        ModelConfig(model="openai/fixture-model", api_key="fixture-key"),
        usage_callback=seen.append,
    )
    loop = AgentLoop(
        model,
        SessionState(session_id="s1", agent_id="a1", task_id="t1", workdir=tmp_path),
        run_limits=RunLimits(),
    )
    result, _ = run_loop(
        monkeypatch,
        loop,
        [response([tool_call("finish", "完成", "f1")], total_tokens=7)],
    )
    assert result == "完成"
    assert seen and seen[0]["total_tokens"] == 7
    assert model.last_usage["total_tokens"] == 7
    capsys.readouterr()
