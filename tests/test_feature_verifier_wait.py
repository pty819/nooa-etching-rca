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


async def submit_wait(session, notebooks, *, name="round1", report="研究"):
    """提交一轮验证并阻塞到判词，供 finish 门禁用例复用。"""
    prime_notebook(notebooks, name)
    return await session.call(
        "feature_analyze_verifier",
        {"notebook_name": name, "report": report, "wait": True},
    )


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


def test_finish_rejected_without_verification_submission(tmp_path):
    """从未提交验证时 finish 被拒绝，避免未经验收的结果被当成最终交付。"""

    async def scenario():
        model = Model(ModelConfig(model="openai/fixture", api_key="fixture"))
        session = research_session(tmp_path, model)
        async with session:
            with pytest.raises(RuntimeError) as excinfo:
                await session.call("finish", {"content": "完成"})
            assert "尚未提交" in str(excinfo.value)

    asyncio.run(scenario())


def test_finish_allowed_after_accepted_verdict(tmp_path, monkeypatch):
    """取得 improved 判词后 finish 正常结束并返回内容。"""

    async def complete(self, messages, *, tools=None):
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
            assert (await submit_wait(session, notebooks))["accepted"] is True
            try:
                result = await session.call("finish", {"content": "完成"})
                assert result == "完成"
            finally:
                notebooks._kernels.clear()

    asyncio.run(scenario())


def test_finish_rejected_after_needs_revision(tmp_path, monkeypatch):
    """needs_revision 且未重新提交时 finish 被拒绝，并提示修订后重交。"""

    async def complete(self, messages, *, tools=None):
        return {
            "role": "assistant",
            "tool_calls": [
                tool_call("report", {"status": "needs_revision", "feedback": "需修订"}, "v1")
            ],
        }

    monkeypatch.setattr(VerifierModel, "completion", complete)

    async def scenario():
        model = Model(ModelConfig(model="openai/fixture", api_key="fixture"))
        session = research_session(tmp_path, model)
        async with session:
            notebooks = session.resource(NotebookTools)
            assert (await submit_wait(session, notebooks))["status"] == "needs_revision"
            try:
                with pytest.raises(RuntimeError) as excinfo:
                    await session.call("finish", {"content": "完成"})
                assert "needs_revision" in str(excinfo.value)
            finally:
                notebooks._kernels.clear()

    asyncio.run(scenario())


def test_finish_allowed_after_explicit_blocked(tmp_path, monkeypatch):
    """验证器显式 blocked 时允许收尾，交给研究者说明缺少的资源。"""

    async def complete(self, messages, *, tools=None):
        return {
            "role": "assistant",
            "tool_calls": [
                tool_call("report", {"status": "blocked", "feedback": "缺少数据集"}, "v1")
            ],
        }

    monkeypatch.setattr(VerifierModel, "completion", complete)

    async def scenario():
        model = Model(ModelConfig(model="openai/fixture", api_key="fixture"))
        session = research_session(tmp_path, model)
        async with session:
            notebooks = session.resource(NotebookTools)
            verdict = await submit_wait(session, notebooks)
            assert verdict["status"] == "blocked"
            try:
                assert await session.call("finish", {"content": "完成"}) == "完成"
            finally:
                notebooks._kernels.clear()

    asyncio.run(scenario())


def test_finish_allowed_with_unverified_marker_after_execution_error(tmp_path, monkeypatch):
    """验证执行故障时允许收尾，但结果必须带未验证标记。"""

    async def complete(self, messages, *, tools=None):
        raise RuntimeError("验证未完成")

    monkeypatch.setattr(VerifierModel, "completion", complete)

    async def scenario():
        model = Model(ModelConfig(model="openai/fixture", api_key="fixture"))
        session = research_session(tmp_path, model)
        async with session:
            notebooks = session.resource(NotebookTools)
            assert (await submit_wait(session, notebooks))["status"] == "error"
            try:
                result = await session.call("finish", {"content": "完成"})
                assert result == "完成\n\n[验证标记] 本轮验证执行故障，最终结果未经验证"
            finally:
                notebooks._kernels.clear()

    asyncio.run(scenario())
