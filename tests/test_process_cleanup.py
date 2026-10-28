"""Cleanup must accept exited Darwin groups but never hide live denial."""

import asyncio
import os
import subprocess
import sys

import pytest

from factor_demo import sandboxing


@pytest.mark.parametrize("platform,empty", [("darwin", True), ("darwin", False), ("linux", True)])
def test_group_permission_error_requires_darwin_and_no_live_members(monkeypatch, platform, empty):
    monkeypatch.setattr(sandboxing.sys, "platform", platform)
    monkeypatch.setattr(sandboxing, "_proc_children", lambda pid: set())
    monkeypatch.setattr(sandboxing, "_darwin_group_has_no_live_members", lambda pid: empty)

    def denied(*args):
        raise PermissionError("cannot signal")

    monkeypatch.setattr(sandboxing.os, "killpg", denied)
    if platform == "darwin" and empty:
        sandboxing._kill_tree(12345)
    else:
        with pytest.raises(PermissionError):
            sandboxing._kill_tree(12345)


@pytest.mark.parametrize(
    "listing,empty",
    [
        ("42 Z\n43 S\n", True),
        ("42 Z\n42 S\n", False),
        ("43 S\n", True),
    ],
)
def test_darwin_group_checks_all_members(monkeypatch, listing, empty):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(a, 0, listing)
    )
    assert sandboxing._darwin_group_has_no_live_members(42) is empty


def test_darwin_group_enumeration_failure_is_not_success(monkeypatch):
    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(subprocess, "run", failed)
    with pytest.raises(subprocess.CalledProcessError):
        sandboxing._darwin_group_has_no_live_members(42)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin zombie group semantics")
def test_unreaped_exited_group_can_be_cleaned():
    async def run():
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "pass", start_new_session=True
        )
        await process.wait()
        await sandboxing.reap(process)
        assert process.returncode == 0

    asyncio.run(run())
    # Popen intentionally leaves a zombie until wait(), unlike asyncio's child watcher.
    process = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    try:
        import time

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = subprocess.check_output(
                ["/bin/ps", "-o", "stat=", "-p", str(process.pid)], text=True
            ).strip()
            if state.startswith("Z"):
                break
            time.sleep(0.01)
        assert state.startswith("Z")
        sandboxing._kill_tree(process.pid)
    finally:
        process.wait()
    with pytest.raises(ProcessLookupError):
        os.kill(process.pid, 0)
