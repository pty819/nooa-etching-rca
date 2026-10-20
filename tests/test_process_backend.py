"""Namespace-free 'process' backend: auto fallback, rlimits, timeout, best-effort seccomp."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from factor_demo import sandboxing

linux = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux backend")


@pytest.fixture(autouse=True)
def clean_backend_cache():
    sandboxing.linux_backend.cache_clear()
    yield
    sandboxing.linux_backend.cache_clear()


def _no_namespaces(monkeypatch):
    monkeypatch.setattr(sandboxing, "landlock_abi", lambda: 0)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stderr="setting up uid map: denied"),
    )


def test_auto_falls_back_to_process_with_warning(monkeypatch, caplog):
    monkeypatch.delenv("FACTOR_LINUX_BACKEND", raising=False)
    monkeypatch.delenv("FACTOR_STRICT_BACKEND", raising=False)
    _no_namespaces(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="factor_demo.sandboxing"):
        assert sandboxing.linux_backend() == "process"
    assert "falling back" in caplog.text and "uid map" in caplog.text


def test_auto_without_bwrap_binary_falls_back(monkeypatch):
    monkeypatch.delenv("FACTOR_LINUX_BACKEND", raising=False)
    monkeypatch.delenv("FACTOR_STRICT_BACKEND", raising=False)
    monkeypatch.setattr(sandboxing, "landlock_abi", lambda: 0)
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert sandboxing.linux_backend() == "process"


def test_strict_and_forced_modes_still_fail_closed(monkeypatch):
    _no_namespaces(monkeypatch)
    monkeypatch.setenv("FACTOR_STRICT_BACKEND", "1")
    with pytest.raises(RuntimeError, match="No enforced Linux"):
        sandboxing.linux_backend()
    sandboxing.linux_backend.cache_clear()
    monkeypatch.delenv("FACTOR_STRICT_BACKEND")
    monkeypatch.setenv("FACTOR_LINUX_BACKEND", "bubblewrap")
    with pytest.raises(RuntimeError, match="No enforced Linux"):
        sandboxing.linux_backend()
    sandboxing.linux_backend.cache_clear()
    monkeypatch.setenv("FACTOR_LINUX_BACKEND", "landlock")
    with pytest.raises(RuntimeError, match="does not provide Landlock"):
        sandboxing.linux_backend()


def test_landlock_still_preferred_in_auto(monkeypatch):
    monkeypatch.delenv("FACTOR_LINUX_BACKEND", raising=False)
    monkeypatch.setattr(sandboxing, "landlock_abi", lambda: 3)
    assert sandboxing.linux_backend() == "landlock"


@linux
def test_process_launch_needs_no_namespaces(monkeypatch, tmp_path):
    monkeypatch.setenv("FACTOR_LINUX_BACKEND", "process")
    monkeypatch.setattr(sandboxing, "_pid_namespace_prefix", lambda: pytest.fail("no bwrap"))
    command = sandboxing.launch([Path("/tmp/x.py")], [], [str(tmp_path)])
    assert "bwrap" not in " ".join(map(str, command))
    assert any("cell_reaper" in str(part) for part in command)
    monkeypatch.setenv("FACTOR_CELL_NOFILE", "128")
    environment = sandboxing.child_environment(tmp_path)
    assert environment["NOOA_CELL_BACKEND"] == "process"
    assert environment["FACTOR_CELL_NOFILE"] == "128"
    assert "OPENAI_API_KEY" not in environment


def test_drop_privileges_is_noop_without_root_or_config(monkeypatch, tmp_path):
    monkeypatch.setenv("FACTOR_CELL_UID", "65534")
    monkeypatch.setattr("os.geteuid", lambda: 1000)
    monkeypatch.setattr("os.setuid", lambda uid: pytest.fail("must not drop"))
    sandboxing._drop_privileges([tmp_path])
    monkeypatch.delenv("FACTOR_CELL_UID")
    monkeypatch.setattr("os.geteuid", lambda: 0)
    sandboxing._drop_privileges([tmp_path])


def test_drop_privileges_as_root_chowns_scratch_and_drops(monkeypatch, tmp_path):
    (tmp_path / "a").write_text("x")
    calls = []
    monkeypatch.setenv("FACTOR_CELL_UID", "1234")
    monkeypatch.setattr("os.geteuid", lambda: 0 if not calls or calls[-1][0] != "uid" else 1234)
    monkeypatch.setattr("os.getuid", lambda: 1234)
    monkeypatch.setattr("os.lchown", lambda p, u, g: calls.append(("chown", u, g)))
    monkeypatch.setattr("os.setgroups", lambda g: calls.append(("groups", g)))
    monkeypatch.setattr("os.setgid", lambda g: calls.append(("gid", g)))
    monkeypatch.setattr("os.setuid", lambda u: calls.append(("uid", u)))
    sandboxing._drop_privileges([tmp_path])
    assert ("chown", 1234, 1234) in calls
    assert calls[-3:] == [("groups", []), ("gid", 1234), ("uid", 1234)]


def _case(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    (run / "input").mkdir(parents=True)
    (run / "workspace" / "artifacts").mkdir(parents=True)
    (run / "input" / "x.txt").write_text("data")
    return run


def _run(tmp_path, code, timeout=60):
    from factor_demo.research_workspace import PythonWorkspace

    async def exercise():
        ws = PythonWorkspace(_case(tmp_path))
        try:
            return await ws.execute(code, timeout)
        finally:
            await ws.close()

    return asyncio.run(exercise())


@linux
def test_process_backend_enforces_rlimits_and_seccomp(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTOR_LINUX_BACKEND", "process")
    monkeypatch.setenv("FACTOR_CELL_FSIZE_MB", "1")
    monkeypatch.setenv("FACTOR_CELL_NOFILE", "100")
    code = (
        "import os, resource, socket\n"
        "for n in ['CPU', 'AS', 'FSIZE', 'NOFILE', 'NPROC']:\n"
        "    print(n, resource.getrlimit(getattr(resource, 'RLIMIT_' + n)))\n"
        "print('CWD', os.getcwd())\n"
        "try:\n"
        "    open('big.bin', 'wb').write(b'0' * (2 * 1024 * 1024))\n"
        "    print('FSIZE_OK')\n"
        "except OSError as e:\n"
        "    print('FSIZE_DENIED', e.errno)\n"
        "try:\n"
        "    socket.socket()\n"
        "    print('SOCKET_OK')\n"
        "except OSError as e:\n"
        "    print('SOCKET_DENIED', e.errno)\n"
    )
    result = _run(tmp_path, code)
    assert result["ok"], result.get("output")
    out = result["output"]
    assert "FSIZE (1048576, 1048576)" in out
    assert "NOFILE (100, 100)" in out
    assert "CPU (300, 300)" in out
    assert "FSIZE_DENIED 27" in out
    assert "SOCKET_DENIED 1" in out


@linux
def test_process_backend_wall_clock_kills_process_group(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTOR_LINUX_BACKEND", "process")
    marker = tmp_path / "orphan-alive"
    code = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(4); open({str(marker)!r}, \"w\")'])\n"
        "time.sleep(60)\n"
    )
    started = time.monotonic()
    result = _run(tmp_path, code, timeout=2)
    assert time.monotonic() - started < 30
    assert not result["ok"]
    time.sleep(5)
    assert not marker.exists()
