"""Host-side path checks that refuse symbolic-link escapes from sandbox workspaces."""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path


def assert_not_symlink(path: Path, *, label: str = "path") -> Path:
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError(f"Refusing symbolic link for {label}: {path}")
    return path


def assert_within(path: Path, root: Path, *, label: str = "path") -> Path:
    root = Path(root).resolve()
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError(f"Refusing symbolic link for {label}: {path}")
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise RuntimeError(f"{label} escapes workspace root: {resolved}")
    return resolved


def ensure_real_dir(path: Path, *, root: Path | None = None) -> Path:
    """Create (or reuse) a real directory; replace a symlink trap."""
    path = Path(path)
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f"Directory must be a real directory: {path}")
    if root is not None:
        return assert_within(path, root, label="directory")
    return path.resolve()


def write_text_nofollow(path: Path, content: str, *, root: Path | None = None) -> None:
    """Write text without following a final-path symlink."""
    path = Path(path)
    parent = path.parent
    if root is not None:
        ensure_real_dir(parent, root=root)
    else:
        if parent.is_symlink():
            raise RuntimeError(f"Refusing symlink parent: {parent}")
        parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.exists():
        path.unlink()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            fd = -1
    finally:
        if fd >= 0:
            os.close(fd)
    if root is not None:
        assert_within(path, root, label="written file")


def open_regular_nofollow(path: Path, *, root: Path | None = None) -> int:
    """Open an existing regular file with O_NOFOLLOW; optional root containment."""
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError(f"Refusing symbolic link: {path}")
    if root is not None:
        assert_within(path, root, label="source")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError(f"Expected a regular file: {path}")
        return fd
    except Exception:
        os.close(fd)
        raise


def copy_regular_nofollow(source: Path, dest: Path, *, required_root: Path) -> None:
    """Copy a regular file opened with O_NOFOLLOW; source must stay under required_root."""
    source = Path(source)
    dest = Path(dest)
    required_root = Path(required_root).resolve()
    fd = open_regular_nofollow(source, root=required_root)
    try:
        if dest.parent.is_symlink():
            raise RuntimeError(f"Refusing symlink destination parent: {dest.parent}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.exists():
            dest.unlink()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        out = os.open(dest, flags, 0o644)
        try:
            with os.fdopen(fd, "rb") as src, os.fdopen(out, "wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
                fd = -1
                out = -1
        finally:
            if out >= 0:
                os.close(out)
    finally:
        if fd >= 0:
            os.close(fd)
