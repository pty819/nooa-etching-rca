"""Trusted host shim: subreaper that SIGKILLs descendants after the cell exits.

Runs *outside* Landlock/seccomp. Prefer being launched under `bwrap --unshare-pid`
(see sandboxing.launch) so killing namespace init tears down every grandchild.
When no PID namespace is available, freeze live descendants with SIGSTOP then SIGKILL
in a sustained loop (fork-chains can outrun a short /proc snapshot).
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path


def _descendants(root: int, *, live_only: bool = False) -> set[int]:
    if not Path("/proc").is_dir():
        return set()
    parents: dict[int, list[int]] = {}
    states: dict[int, str] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            pid = int(entry.name)
            raw = (entry / "stat").read_text()
            close = raw.rfind(")")
            fields = raw[close + 2 :].split()
            state = fields[0]
            ppid = int(fields[1])
            parents.setdefault(ppid, []).append(pid)
            states[pid] = state
        except (OSError, ValueError, IndexError):
            continue
    found: set[int] = set()
    stack = [root]
    while stack:
        current = stack.pop()
        for child in parents.get(current, []):
            if child in found or child == root:
                continue
            if live_only and states.get(child) == "Z":
                continue
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


def _kill_descendants(root: int, *, seconds: float = 3.0) -> None:
    """SIGSTOP live children (so they cannot keep forking), then SIGKILL, until quiet."""
    deadline = time.monotonic() + seconds
    quiet = 0
    while time.monotonic() < deadline:
        live = _descendants(root, live_only=True)
        if not live:
            _reap_zombies()
            quiet += 1
            if quiet >= 5:
                return
            time.sleep(0.002)
            continue
        quiet = 0
        for pid in live:
            try:
                os.kill(pid, signal.SIGSTOP)
            except ProcessLookupError:
                pass
        for pid in _descendants(root, live_only=True) | live:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _reap_zombies()
    # Final sweep
    for pid in _descendants(root, live_only=True):
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

        # Best-effort cgroup v2 pids.max (no factor_demo import: process starts with -I).
        try:
            root = os.environ.get("FACTOR_CELL_CGROUP", "").strip()
            limit = int(os.environ.get("FACTOR_CELL_NPROC", "512"))
            if root and limit >= 8:
                base = Path(root)
                cell = base / f"factor-cell-{os.getpid()}"
                cell.mkdir(parents=True, exist_ok=True)
                pids_max = cell / "pids.max"
                if pids_max.exists():
                    pids_max.write_text(str(limit))
                (cell / "cgroup.procs").write_text(str(os.getpid()))
        except (OSError, ValueError):
            pass
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(36, 1, 0, 0, 0)  # PR_SET_CHILD_SUBREAPER
    child = os.fork()
    if child == 0:
        if sys.platform.startswith("linux"):
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            # If the supervisor dies, take the worker with it (PID-ns init dies too).
            libc.prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
            if os.getppid() == 1:
                raise SystemExit(128 + signal.SIGKILL)
        os.execv(sys.argv[1], sys.argv[1:])
        raise SystemExit(127)
    try:
        _pid, status = os.waitpid(child, 0)
    except ChildProcessError:
        status = 0
    # Orphans from setsid() are re-parented here when we are subreaper; kill them.
    # Inside a private PID namespace, exiting as the last supervisor also tears down peers.
    _kill_descendants(os.getpid())
    if os.WIFEXITED(status):
        raise SystemExit(os.WEXITSTATUS(status))
    if os.WIFSIGNALED(status):
        raise SystemExit(128 + os.WTERMSIG(status))
    raise SystemExit(1)


if __name__ == "__main__":
    main()
