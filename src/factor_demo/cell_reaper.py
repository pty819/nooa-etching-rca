"""Trusted host shim: subreaper that SIGKILLs descendants after the cell exits."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path


def _descendants(root: int) -> set[int]:
    if not Path("/proc").is_dir():
        return set()
    parents: dict[int, list[int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            raw = (entry / "stat").read_text()
            close = raw.rfind(")")
            ppid = int(raw[close + 2 :].split()[1])
            parents.setdefault(ppid, []).append(pid)
        except (OSError, ValueError, IndexError):
            continue
    found: set[int] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        for child in parents.get(current, []):
            if child not in found and child != root:
                found.add(child)
                stack.append(child)
    return found


def _reap_zombies() -> None:
    while True:
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def _kill_descendants(root: int) -> None:
    for _ in range(8):
        targets = _descendants(root)
        if not targets:
            _reap_zombies()
            return
        for pid in targets:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _reap_zombies()


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("cell_reaper requires a command")
    if sys.platform.startswith("linux"):
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
    child = os.fork()
    if child == 0:
        os.execv(sys.argv[1], sys.argv[1:])
        raise SystemExit(127)
    try:
        _pid, status = os.waitpid(child, 0)
    except ChildProcessError:
        status = 0
    # Orphans from setsid() are re-parented here; kill then reap them.
    _kill_descendants(os.getpid())
    if os.WIFEXITED(status):
        raise SystemExit(os.WEXITSTATUS(status))
    if os.WIFSIGNALED(status):
        raise SystemExit(128 + os.WTERMSIG(status))
    raise SystemExit(1)


if __name__ == "__main__":
    main()
