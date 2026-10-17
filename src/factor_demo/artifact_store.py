"""Content-addressed immutable snapshots; mutable workspace files are copied first."""

import os
import shutil
import uuid
from pathlib import Path

from .datasets import file_hash


def snapshot_file(source: Path, target: Path, store: Path):
    sha = file_hash(source)
    blob = store / sha[:2] / sha
    blob.parent.mkdir(parents=True, exist_ok=True)
    if not blob.exists():
        temporary = blob.with_name("." + uuid.uuid4().hex)
        shutil.copyfile(source, temporary)
        os.replace(temporary, blob)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if file_hash(target) != sha:
            raise ValueError("Frozen artifact already exists with different content")
        return sha
    try:
        target.hardlink_to(blob)
    except OSError:
        shutil.copyfile(blob, target)
    return sha


def snapshot_tree(source: Path, target: Path, store: Path):
    target.mkdir(parents=True, exist_ok=False)
    for path in sorted(source.rglob("*")):
        if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts:
            snapshot_file(path, target / path.relative_to(source), store)
