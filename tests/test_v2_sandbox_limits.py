"""验证沙箱资源上限的命令包装和真实执行限制。"""

import asyncio
import sys

import pytest

from factor_demo.autoresearch_agent_v2.sandboxing import Sandbox, SandboxConfig


def ready_sandbox(tmp_path, **limits) -> Sandbox:
    """创建并探测可用后端；不可用时跳过当前用例。"""
    sandbox = Sandbox(SandboxConfig(workdir=tmp_path, timeout_s=20, **limits))
    try:
        probe = asyncio.run(sandbox.run_shell("printf ready"))
    except Exception as error:  # noqa: BLE001 - 环境不满足时跳过，不掩盖断言失败。
        asyncio.run(sandbox.aclose())
        pytest.skip(f"需要可用的隔离后端: {error}")
    if not probe.ok:
        close_sandbox(sandbox)
        pytest.skip(f"需要可用的隔离后端: {probe.stderr}")
    return sandbox


def close_sandbox(sandbox: Sandbox) -> None:
    """关闭测试中创建的沙箱。"""
    asyncio.run(sandbox.aclose())


def test_resource_limits_wrap_backend_command(tmp_path):
    """配置资源上限后，后端命令被包在 ulimit 前缀内。"""
    sandbox = Sandbox(SandboxConfig(workdir=tmp_path, cpu_seconds=30, memory_mb=256, pids=64))
    try:
        backend, argv = sandbox.prepare_command(["/bin/true"])
        assert backend in {"macos", "bubblewrap", "landlock"}
        assert argv[:2] == ["/bin/sh", "-c"]
        script = argv[2]
        assert "ulimit -t 30" in script
        assert "ulimit -v 262144" in script
        assert "ulimit -u 64" in script
        assert argv[-2:] == ["--", "/bin/true"] or argv[-1] == "/bin/true"
    finally:
        close_sandbox(sandbox)


def test_no_limits_keeps_backend_command_unwrapped(tmp_path):
    """未配置资源上限时不插入额外 Shell 前缀。"""
    sandbox = Sandbox(SandboxConfig(workdir=tmp_path))
    try:
        _, argv = sandbox.prepare_command(["/bin/true"])
        assert "ulimit" not in " ".join(argv)
    finally:
        close_sandbox(sandbox)


def test_cpu_limit_kills_runaway_python(tmp_path):
    """CPU 上限到达后进程被内核终止，而不是等到墙钟超时。"""
    sandbox = ready_sandbox(tmp_path, cpu_seconds=1)
    try:
        result = asyncio.run(sandbox.run_python("while True:\n    pass\n"))
    finally:
        close_sandbox(sandbox)
    assert not result.timed_out
    assert result.returncode != 0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS 仅在 Linux 可靠生效")
def test_memory_limit_rejects_large_allocation(tmp_path):
    """内存上限生效时，超出预算的分配被拒绝。"""
    sandbox = ready_sandbox(tmp_path, memory_mb=256)
    try:
        result = asyncio.run(
            sandbox.run_python("value = bytearray(512 * 1024 * 1024)\nprint(len(value))\n")
        )
    finally:
        close_sandbox(sandbox)
    assert not result.timed_out
    assert result.returncode != 0
    assert "MemoryError" in result.stderr or "MemoryError" in result.stdout
