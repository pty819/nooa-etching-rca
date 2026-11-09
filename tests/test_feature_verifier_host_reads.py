"""核对验证器子沙箱只使用宿主显式授权的只读路径。"""

import asyncio

from factor_demo.autoresearch_agent_v2.agent import Session, SessionState
from factor_demo.autoresearch_agent_v2.agent.model import Model
from factor_demo.autoresearch_agent_v2.config import ModelConfig
from factor_demo.autoresearch_agent_v2.feature_verifier import FeatureAnalyzeVerifier
from factor_demo.autoresearch_agent_v2.sandboxing import Sandbox, SandboxConfig
from factor_demo.autoresearch_agent_v2.tools import NotebookTools, SandboxTools


def _parent(tmp_path, *, read_paths=(), sandbox=True):
    """创建研究会话；读取授权仅由宿主配置给出。"""
    workdir = tmp_path / "work"
    workdir.mkdir()
    session = Session(
        Model(ModelConfig(model="openai/fixture", api_key="fixture")),
        SessionState(session_id="r", agent_id="r", task_id="t", workdir=workdir),
    )
    if sandbox:
        SandboxTools.register(session, config=SandboxConfig(workdir=workdir, read_paths=read_paths))
    session.register_stateful_tool(NotebookTools)
    session.register_stateful_tool(FeatureAnalyzeVerifier)
    return session


def test_verifier_child_uses_host_read_paths_not_dataset_metadata(tmp_path):
    """模型改写 DATASET 登记不能扩大验证子会话的读取范围。"""
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    forged = tmp_path / "forged"
    forged.mkdir()

    async def scenario():
        async with _parent(tmp_path, read_paths=(dataset,)) as session:
            # DATASET 登记只是模型可见提示，可被模型反复改写。
            await session.call(
                "register_dataset",
                {"name": "DATASET", "path": str(forged), "description": "伪造的数据位置"},
            )
            verifier = session.resource(FeatureAnalyzeVerifier)
            await verifier._ensure_child()
            child = verifier.child
            await child.start()
            config = child.resource(Sandbox).config
            parent_reads = session.resource(Sandbox).config.read_paths
            assert config.read_paths == parent_reads
            assert dataset.resolve() in config.read_paths
            assert forged.resolve() not in config.read_paths

    asyncio.run(scenario())


def test_verifier_child_without_host_sandbox_gets_no_read_grants(tmp_path):
    """宿主没有登记沙箱时，验证子会话不继承任何额外只读路径。"""
    dataset = tmp_path / "dataset"
    dataset.mkdir()

    async def scenario():
        async with _parent(tmp_path, read_paths=(dataset,), sandbox=False) as session:
            await session.call(
                "register_dataset",
                {"name": "DATASET", "path": str(dataset), "description": "模型登记"},
            )
            verifier = session.resource(FeatureAnalyzeVerifier)
            await verifier._ensure_child()
            child = verifier.child
            await child.start()
            assert child.resource(Sandbox).config.read_paths == ()

    asyncio.run(scenario())
