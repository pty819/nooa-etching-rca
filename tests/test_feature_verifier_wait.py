"""核对收尾阻塞提交、pending/error 区分与 finish 门禁。"""

import asyncio
import json

import nbformat
import pytest

from factor_demo.autoresearch_agent_v2.agent import Session, SessionState
from factor_demo.autoresearch_agent_v2.agent.model import Model
from factor_demo.autoresearch_agent_v2.config import ModelConfig
from factor_demo.autoresearch_agent_v2.feature_verifier import FeatureAnalyzeVerifier
from factor_demo.autoresearch_agent_v2.feature_verifier.agent import VerifierModel
from factor_demo.autoresearch_agent_v2.tools import NotebookTools


def tool_call(name, arguments, identity):
    """构造模拟模型工具调用。"""
    return {
        "id": identity,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def research_session(tmp_path, model):
    """创建已注册 Notebook 与 verifier 的研究会话。"""
    state = SessionState(session_id="research", agent_id="research", task_id="t", workdir=tmp_path)
    session = Session(model, state)
    session.register_stateful_tool(NotebookTools)
    session.register_stateful_tool(FeatureAnalyzeVerifier)
    return session


def prime_notebook(notebooks, name="round1"):
    """为提交准备一个空 Notebook 文档。"""
    notebooks._kernels[name] = None
    notebooks._save_document(name, nbformat.v4.new_notebook(), None)


def test_submit_wait_returns_terminal_verdict(tmp_path, monkeypatch):
    """wait=True 必须阻塞到形成判词并返回四态结果，而不是 submitted 回执。"""

    async def complete(self, messages, *, tools=None):
        return {
            "role": "assistant",
            "tool_calls": [
                tool_call(
                    "report", {"status": "regressed", "feedback": "持平，负结果同样有效"}, "v1"
                )
            ],
        }

    monkeypatch.setattr(VerifierModel, "completion", complete)

    async def scenario():
        model = Model(ModelConfig(model="openai/fixture", api_key="fixture"))
        session = research_session(tmp_path, model)
        async with session:
            notebooks = session.resource(NotebookTools)
            prime_notebook(notebooks)
            try:
                result = await session.call(
                    "feature_analyze_verifier",
                    {"notebook_name": "round1", "report": "第一版", "wait": True},
                )
                assert result["status"] == "regressed"
                assert result["accepted"] is True
                assert result["submission_id"]
                assert result["round_id"] == "round1"
            finally:
                notebooks._kernels.clear()

    asyncio.run(scenario())


def test_submit_wait_needs_revision_then_resubmit(tmp_path, monkeypatch):
    """needs_revision 后按原 round_id 重提交，最终取得通过判词。"""
    calls = {"n": 0}

    async def complete(self, messages, *, tools=None):
        calls["n"] += 1
        status = "needs_revision" if calls["n"] == 1 else "improved"
        return {
            "role": "assistant",
            "tool_calls": [
                tool_call(
                    "report",
                    {"status": status, "feedback": f"第 {calls['n']} 轮"},
                    f"v{calls['n']}",
                )
            ],
        }

    monkeypatch.setattr(VerifierModel, "completion", complete)

    async def scenario():
        model = Model(ModelConfig(model="openai/fixture", api_key="fixture"))
        session = research_session(tmp_path, model)
        async with session:
            notebooks = session.resource(NotebookTools)
            prime_notebook(notebooks)
            try:
                first = await session.call(
                    "feature_analyze_verifier",
                    {"notebook_name": "round1", "report": "初稿", "round_id": "r1", "wait": True},
                )
                assert first["status"] == "needs_revision"
                assert first["accepted"] is False
                second = await session.call(
                    "feature_analyze_verifier",
                    {"notebook_name": "round1", "report": "修订", "round_id": "r1", "wait": True},
                )
                assert second["status"] == "improved"
                assert second["accepted"] is True
                assert calls["n"] == 2
            finally:
                notebooks._kernels.clear()

    asyncio.run(scenario())


def test_submit_wait_surfaces_execution_error_not_success(tmp_path, monkeypatch):
    """验证执行故障时 wait=True 返回 error，绝不冒充通过。"""

    async def complete(self, messages, *, tools=None):
        raise RuntimeError("验证未完成")

    monkeypatch.setattr(VerifierModel, "completion", complete)

    async def scenario():
        model = Model(ModelConfig(model="openai/fixture", api_key="fixture"))
        session = research_session(tmp_path, model)
        async with session:
            notebooks = session.resource(NotebookTools)
            prime_notebook(notebooks)
            try:
                result = await session.call(
                    "feature_analyze_verifier",
                    {"notebook_name": "round1", "report": "研究", "wait": True},
                )
                assert result["status"] == "error"
                assert result.get("accepted") is not True
            finally:
                notebooks._kernels.clear()

    asyncio.run(scenario())


def test_finish_gate_rejects_while_verifier_busy(tmp_path, monkeypatch):
    """验证未完成时 finish 被拒绝，避免静默丢弃最终验收。"""
    blocked = asyncio.Event()

    async def complete(self, messages, *, tools=None):
        await blocked.wait()
        return {
            "role": "assistant",
            "tool_calls": [tool_call("report", {"status": "improved", "feedback": "通过"}, "v1")],
        }

    monkeypatch.setattr(VerifierModel, "completion", complete)

    async def scenario():
        model = Model(ModelConfig(model="openai/fixture", api_key="fixture"))
        session = research_session(tmp_path, model)
        async with session:
            notebooks = session.resource(NotebookTools)
            verifier = session.resource(FeatureAnalyzeVerifier)
            verifier.wait_timeout_s = 0.05
            prime_notebook(notebooks)
            try:
                await session.call(
                    "feature_analyze_verifier",
                    {"notebook_name": "round1", "report": "研究", "wait": False},
                )
                with pytest.raises(RuntimeError):
                    await session.call("finish", {"content": "提前交付"})
                blocked.set()
                await verifier.status(wait=True)
            finally:
                notebooks._kernels.clear()

    asyncio.run(scenario())


def test_finish_allowed_when_verifier_idle(tmp_path):
    """没有待验证提交时 finish 正常结束本轮并返回内容。"""

    async def scenario():
        model = Model(ModelConfig(model="openai/fixture", api_key="fixture"))
        session = research_session(tmp_path, model)
        async with session:
            result = await session.call("finish", {"content": "完成"})
            assert result == "完成"

    asyncio.run(scenario())
