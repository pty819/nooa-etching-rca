"""Kernel capability selection never downgrades to unrestricted code execution."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from factor_demo import sandboxing


@pytest.fixture(autouse=True)
def clean_backend_cache():
    sandboxing.linux_backend.cache_clear()
    yield
    sandboxing.linux_backend.cache_clear()


def test_landlock_is_preferred_when_available(monkeypatch):
    monkeypatch.delenv("FACTOR_LINUX_BACKEND", raising=False)
    monkeypatch.setattr(sandboxing, "landlock_abi", lambda: 5)
    assert sandboxing.linux_backend() == "landlock"


def test_missing_landlock_selects_only_a_working_namespace_backend(monkeypatch):
    monkeypatch.delenv("FACTOR_LINUX_BACKEND", raising=False)
    monkeypatch.setattr(sandboxing, "landlock_abi", lambda: 0)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stderr="")
    )
    assert sandboxing.linux_backend() == "bubblewrap"
    sandboxing.linux_backend.cache_clear()
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=1, stderr="user namespace unavailable"),
    )
    with pytest.raises(RuntimeError, match="No enforced Linux"):
        sandboxing.linux_backend()


def test_forced_backends_fail_closed_and_reject_unknown_options(monkeypatch):
    monkeypatch.setattr(sandboxing, "landlock_abi", lambda: 0)
    monkeypatch.setenv("FACTOR_LINUX_BACKEND", "landlock")
    with pytest.raises(RuntimeError, match="does not provide Landlock"):
        sandboxing.linux_backend()
    sandboxing.linux_backend.cache_clear()
    monkeypatch.setenv("FACTOR_LINUX_BACKEND", "unsafe")
    with pytest.raises(RuntimeError, match="must be"):
        sandboxing.linux_backend()


def test_mount_command_exposes_only_declared_data_and_scratch(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/bwrap")
    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "_mount_root.txt").write_text("nooa-private-mount-root-v1")
    work = tmp_path / "work"
    work.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(sandboxing, "runtime_reads", lambda: [Path("/usr")])
    command = sandboxing.bubblewrap_command([kit / "research_worker.py"], [kit, data], [work])
    assert command[:2] == ["/usr/bin/bwrap", "--unshare-all"]
    assert command[command.index("--bind") + 1 : command.index("--bind") + 3] == [
        str(work),
        str(work),
    ]
    assert "--remount-ro" in command and "/.nooa-cell-backend" in command
    assert ["--ro-bind", "/", "/"] not in [command[i : i + 3] for i in range(len(command) - 2)]
    assert ["--ro-bind", str(data), str(data)] in [
        command[i : i + 3] for i in range(len(command) - 2)
    ]


def test_uv_version_alias_is_preserved_without_mounting_its_parent(tmp_path, monkeypatch):
    base = tmp_path / "cpython-3.12.13"
    (base / "bin").mkdir(parents=True)
    (base / "bin/python3.12").write_text("fixture interpreter")
    alias = tmp_path / "cpython-3.12"
    alias.symlink_to(base, target_is_directory=True)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    executable = venv / "bin/python"
    executable.symlink_to(alias / "bin/python3.12")
    monkeypatch.setattr("sys.executable", str(executable))
    monkeypatch.setattr("sys._base_executable", str(alias / "bin/python3.12"))
    assert sandboxing.interpreter_links([base, venv]) == {alias: str(base)}
