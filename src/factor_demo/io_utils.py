"""Atomic JSON writes shared by the modern application and legacy fixtures."""

import json
import os
import tempfile
from pathlib import Path


def write_json(path: Path, data: object) -> None:
    content = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        temporary = Path(f.name)
        f.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
