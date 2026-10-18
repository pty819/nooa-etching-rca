"""Fail-closed process policy shared by CodeAct cells and scientific children."""

from __future__ import annotations

import json
import os
import signal
import sys
from functools import lru_cache
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
            "/etc/fonts",
            "/var/cache/fontconfig",
            "/bin",
            "/lib",
            "/lib64",
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


def landlock_abi():
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    return max(0, int(libc.syscall(444, 0, 0, 1)))


@lru_cache(maxsize=1)
def linux_backend():
    """Select an enforced filesystem backend; never substitute unrestricted execution."""
    import shutil
    import subprocess

    requested = os.environ.get("FACTOR_LINUX_BACKEND", "auto")
    if requested not in {"auto", "landlock", "bubblewrap"}:
        raise RuntimeError("FACTOR_LINUX_BACKEND must be auto, landlock or bubblewrap")
    abi = landlock_abi()
    if requested != "bubblewrap" and abi >= 1:
        return "landlock"
    if requested == "landlock":
        raise RuntimeError("This kernel does not provide Landlock; select bubblewrap or auto")
    binary = shutil.which("bwrap")
    if binary:
        probe = subprocess.run(
            [
                binary,
                "--unshare-all",
                "--die-with-parent",
                "--ro-bind",
                "/",
                "/",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "/usr/bin/true",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if probe.returncode == 0:
            return "bubblewrap"
        reason = probe.stderr.strip()[:500]
    else:
        reason = "bubblewrap is not installed"
    raise RuntimeError(
        f"No enforced Linux filesystem backend for {requested} (Landlock ABI {abi}): {reason}"
    )


def execution_info():
    import platform

    mode = (
        linux_backend()
        if sys.platform.startswith("linux")
        else "sandbox-exec"
        if sys.platform == "darwin"
        else "unsupported"
    )
    return {
        "platform": sys.platform,
        "architecture": platform.machine(),
        "backend": mode,
        "landlock_abi": landlock_abi() if sys.platform.startswith("linux") else None,
    }


def interpreter_links(allowed):
    """Preserve version aliases used by venv interpreter symlinks (not extra mounts)."""
    pending = [Path(sys.executable), Path(getattr(sys, "_base_executable", sys.executable))]
    seen, links = set(), {}
    while pending:
        path = pending.pop()
        for item in [path, *path.parents]:
            if item in seen:
                continue
            seen.add(item)
            if not item.is_symlink():
                continue
            target = Path(os.readlink(item))
            literal = target if target.is_absolute() else item.parent / target
            pending.append(literal)
            if not any(item == root or item.is_relative_to(root) for root in allowed):
                links[item] = os.readlink(item)
    return links


def bubblewrap_command(python_args, reads, writes):
    import shutil

    binary = shutil.which("bwrap")
    if not binary:
        raise RuntimeError("Selected bubblewrap executable disappeared")
    kit = Path(python_args[0]).resolve().parent
    marker = kit / "_mount_root.txt"
    if not marker.is_file():
        raise RuntimeError("Missing trusted mount bootstrap marker")
    command = [
        binary,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--tmpfs",
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
    ]
    # ELF interpreters use literal /lib or /lib64 paths even on usr-merged
    # distributions; binding only their resolved targets is insufficient.
    for alias in [Path("/lib"), Path("/lib64"), Path("/bin"), Path("/sbin")]:
        if alias.is_symlink():
            command.extend(["--symlink", os.readlink(alias), str(alias)])
    extras = [Path(p) for p in ["/etc/ld.so.cache", "/etc/localtime"] if Path(p).exists()]
    allowed = sorted(
        {*runtime_reads(), *(Path(p).resolve() for p in reads), *extras},
        key=lambda p: (len(p.parts), str(p)),
    )
    for path in allowed:
        if path == Path("/"):
            raise RuntimeError("A cell cannot receive the whole host root")
        command.extend(["--ro-bind", str(path), str(path)])
    for alias, target in interpreter_links(allowed).items():
        command.extend(["--symlink", target, str(alias)])
    for path in writes:
        path = Path(path).resolve()
        command.extend(["--bind", str(path), str(path)])
    command.extend(
        [
            "--ro-bind",
            str(marker),
            "/.nooa-cell-backend",
            "--chdir",
            str(Path(writes[0]).resolve()),
            "--remount-ro",
            "/proc",
            "--remount-ro",
            "/",
            "--",
            sys.executable,
            "-I",
            "-u",
            *map(str, python_args),
        ]
    )
    return command


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
        if linux_backend() == "bubblewrap":
            return bubblewrap_command(python_args, reads, writes)
        return [sys.executable, "-I", "-u", *map(str, python_args)]
    raise RuntimeError("No enforced execution backend on this platform")


def font_cache_seed():
    import hashlib
    from importlib.metadata import version

    explicit = os.environ.get("FACTOR_FONT_CACHE_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    identity = hashlib.sha256(str(Path(sys.prefix).resolve()).encode()).hexdigest()[:12]
    return Path.home() / ".cache/nooa-etching-rca/fonts" / (version("matplotlib") + "-" + identity)


def prepare_font_cache():
    """Deployment-time warmup; no model calls and no mutable cache shared by cells."""
    cache = font_cache_seed()
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    prior = os.environ.get("MPLCONFIGDIR")
    os.environ["MPLCONFIGDIR"] = str(cache)
    try:
        from matplotlib import font_manager

        files = sorted(cache.glob("fontlist-v*.json"))
        return {
            "font_cache": str(cache),
            "cache_files": len(files),
            "fonts": len(font_manager.fontManager.ttflist),
        }
    finally:
        if prior is None:
            os.environ.pop("MPLCONFIGDIR", None)
        else:
            os.environ["MPLCONFIGDIR"] = prior


def seed_worker_fonts(cache):
    import re
    import shutil

    source = font_cache_seed()
    if not source.is_dir():
        return
    target = cache / "mpl"
    if target.is_symlink():
        raise RuntimeError("Worker font cache must not be a symbolic link")
    target.mkdir(parents=True, exist_ok=True)
    for path in source.iterdir():
        if (
            not re.fullmatch(r"fontlist-v[0-9]+(?:\.[0-9]+)*\.json", path.name)
            or path.is_symlink()
            or not path.is_file()
        ):
            continue
        dest = target / path.name
        if not dest.is_symlink() and not dest.exists() and path.stat().st_size <= 8 * 1024 * 1024:
            shutil.copyfile(path, dest)


def child_environment(work: Path, **extra):
    cache = work / ".cache"
    if cache.is_symlink():
        raise RuntimeError("Worker cache must not be a symbolic link")
    cache.mkdir(parents=True, exist_ok=True)
    seed_worker_fonts(cache)
    environment = {
        "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
        "HOME": str(cache),
        "TMPDIR": str(cache),
        "MPLCONFIGDIR": str(cache / "mpl"),
        "NUMBA_CACHE_DIR": str(cache / "numba"),
        "JOBLIB_TEMP_FOLDER": str(cache / "joblib"),
        "MPLBACKEND": "Agg",
        "PYTHONUNBUFFERED": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        "POLARS_MAX_THREADS": "2",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        **extra,
    }
    if sys.platform.startswith("linux"):
        environment["NOOA_CELL_BACKEND"] = linux_backend()
    return environment


def prepare_guard_modules(kit: Path):
    """Copy the pinned, attributed guards without importing the full SDK in children."""
    import importlib.util

    (kit / "_mount_root.txt").write_text("nooa-private-mount-root-v1")
    for module, name in [("config", "_guard_config.py"), ("guards", "_guard_rules.py")]:
        spec = importlib.util.find_spec("nooa.runtime.sandbox." + module)
        if spec is None or not spec.origin:
            raise RuntimeError("Pinned NOOA guard source is unavailable")
        source = Path(spec.origin).read_text()
        if module == "guards":
            original = "from nooa.runtime.sandbox.config import LandlockRule, ResolvedSpec"
            if source.count(original) != 1:
                raise RuntimeError(
                    "Pinned NOOA guard import changed; refuse an unverified bootstrap"
                )
            source = source.replace(
                original, "from _guard_config import LandlockRule, ResolvedSpec"
            )
        (kit / name).write_text(source)


def install_linux(reads, writes):
    """Applied by the trusted fresh child bootstrap before loading user code/state."""
    if not sys.platform.startswith("linux"):
        return
    import ctypes
    import platform

    from _guard_config import LandlockRule
    from _guard_rules import apply_landlock, apply_rlimits

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
    # Resolve the trusted process baseline before /proc becomes inaccessible.
    apply_rlimits(max_memory_mb=2048, max_cpu_seconds=300)
    mode = os.environ.get("NOOA_CELL_BACKEND", "landlock")
    if mode == "landlock":
        apply_landlock(tuple(rules))
    elif mode == "bubblewrap":
        marker = Path("/.nooa-cell-backend")
        if not marker.is_file() or marker.read_text() != "nooa-private-mount-root-v1":
            raise RuntimeError("Bubblewrap backend was requested outside its trusted mount root")
        import stat

        if not os.statvfs("/").f_flag & getattr(stat, "ST_RDONLY", 1):
            raise RuntimeError("Bubblewrap root must be read-only")
    else:
        raise RuntimeError("Unknown cell execution backend")
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
