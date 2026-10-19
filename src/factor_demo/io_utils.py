"""Atomic JSON writes shared by the modern application and legacy fixtures."""

import json
import os
import tempfile
from pathlib import Path

from .path_guard import assert_not_symlink, assert_within


def write_json(path: Path, data: object, *, root: Path | None = None) -> None:
    """Atomically write JSON; refuse a symlink destination or symlink parent."""
    path = Path(path)
    parent = path.parent
    if parent.is_symlink():
        raise RuntimeError(f"Refusing symlink parent for JSON write: {parent}")
    if path.is_symlink():
        path.unlink()
    if root is not None:
        assert_within(parent, root, label="JSON parent")
    content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    parent.mkdir(parents=True, exist_ok=True)
    assert_not_symlink(parent, label="JSON parent")
    with tempfile.NamedTemporaryFile(
        mode="w", dir=parent, delete=False, encoding="utf-8"
    ) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    if path.is_symlink():
        raise RuntimeError(f"JSON landed on a symbolic link: {path}")
    if root is not None:
        assert_within(path, root, label="JSON file")
