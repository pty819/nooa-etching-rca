"""Fail-closed process policy shared by CodeAct cells and scientific children."""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path


def runtime_reads() -> list[Path]:
    import importlib.util

    spec = importlib.util.find_spec("factor_demo")
    hidden = Path(spec.origin).resolve().parent if spec and spec.origin else None
    candidates = [
        Path(p).resolve()
        for p in [
            sys.prefix,
            sys.base_prefix,
            "/System",
            "/usr",
            "/bin",
            "/sbin",
            "/Library/Apple",
            "/Library/Fonts",
            "/private/var/db/timezone",
        ]
        if Path(p).exists()
    ]
    result = set()

    def grant(path):
        if hidden and (path == hidden or path.is_relative_to(hidden)):
            return
        if hidden and hidden.is_relative_to(path) and path.is_dir():
            for child in path.iterdir():
                grant(child.resolve())
        else:
            result.add(path)

    for candidate in candidates:
        grant(candidate)
    return sorted(result)


def mac_profile(reads, writes):
    def rules(paths):
        return " ".join(f"(subpath {json.dumps(str(Path(p).resolve()))})" for p in paths)

    return (
        "(version 1)(deny default)"
        "(allow process-exec)(allow process-fork)(allow sysctl-read)"
        "(allow process-info* (target self))"
        '(allow mach-lookup (global-name "com.apple.cfprefsd.agent") (global-name "com.apple.cfprefsd.daemon") (global-name "com.apple.FontObjectsServer"))'
        "(allow ipc-posix-shm)(allow ipc-posix-sem)"
        '(allow file-read-metadata)(allow file-read-data (literal "/"))'
        f"(allow file-read* {rules([*runtime_reads(), *reads, *writes])})"
        f'(allow file-write* {rules(writes)} (literal "/dev/null"))'
        '(allow file-read-data (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random") (literal "/private/etc/localtime"))'
    )


def launch(python_args, reads, writes):
    if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
        return [
            "/usr/bin/sandbox-exec",
            "-p",
            mac_profile(reads, writes),
            sys.executable,
            "-I",
            "-u",
            *map(str, python_args),
        ]
    if sys.platform.startswith("linux"):
        return [sys.executable, "-I", "-u", *map(str, python_args)]
    raise RuntimeError("No enforced execution backend on this platform")


def child_environment(work: Path, **extra):
    cache = work / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    return {
        "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
        "HOME": str(cache),
        "TMPDIR": str(cache),
        "MPLCONFIGDIR": str(cache / "mpl"),
        "NUMBA_CACHE_DIR": str(cache / "numba"),
        "JOBLIB_TEMP_FOLDER": str(cache / "joblib"),
        "MPLBACKEND": "Agg",
        "PYTHONUNBUFFERED": "1",
        "POLARS_MAX_THREADS": "2",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        **extra,
    }


def install_linux(reads, writes):
    """Applied by the trusted fresh child bootstrap before loading user code/state."""
    if not sys.platform.startswith("linux"):
        return
    import ctypes
    import platform

    from nooa.runtime.sandbox.config import LandlockRule
    from nooa.runtime.sandbox.guards import apply_landlock, apply_rlimits

    rules = [
        LandlockRule(str(Path(p).resolve()), required=True)
        for p in [*runtime_reads(), *reads]
        if Path(p).exists()
    ]
    rules += [LandlockRule(str(Path(p).resolve()), write=True, required=True) for p in writes]
    rules += [
        LandlockRule("/dev/null", write=True, required=True),
        LandlockRule("/dev/urandom"),
        LandlockRule("/dev/random"),
    ]
    apply_landlock(tuple(rules))
    apply_rlimits(max_memory_mb=2048, max_cpu_seconds=300)
    # Deny socket creation (including AF_UNIX), ptrace/process-memory access and
    # signals to other processes. The parent retains authority to reap children.
    arch = platform.machine()
    numbers = {
        "x86_64": [41, 101, 310, 311, 62, 200, 234, 424, 434],
        "aarch64": [198, 117, 270, 271, 129, 130, 131, 424, 434],
    }
    if arch not in numbers:
        raise RuntimeError("Unsupported seccomp architecture")

    class Filter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint),
        ]

    class Program(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

    expected_arch = 0xC000003E if arch == "x86_64" else 0xC00000B7
    instructions = [
        (0x20, 0, 0, 4),
        (0x15, 1, 0, expected_arch),
        (0x06, 0, 0, 0x80000000),
        (0x20, 0, 0, 0),
    ]
    if arch == "x86_64":
        # Reject the alternate x32 syscall table as well as native forbidden calls.
        instructions.extend([(0x35, 0, 1, 0x40000000), (0x06, 0, 0, 0x00050000 | 1)])
    for number in numbers[arch]:
        instructions.extend([(0x15, 0, 1, number), (0x06, 0, 0, 0x00050000 | 1)])
    instructions.append((0x06, 0, 0, 0x7FFF0000))
    array = (Filter * len(instructions))(*(Filter(*i) for i in instructions))
    program = Program(len(instructions), array)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) or libc.prctl(22, 2, ctypes.byref(program), 0, 0):
        raise OSError(ctypes.get_errno(), "Cannot install seccomp; execution refused")


async def reap(process):
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()
