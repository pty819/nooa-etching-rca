"""Check tracked publication scope without displaying any secret value."""

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check():
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    names = [x for x in names if x]
    if not names:
        raise SystemExit("No tracked publication files to inspect")
    secrets = []
    private = ROOT / ".secrets/providers.json"
    if private.exists():

        def collect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if (
                        key in {"api_key", "auth_token", "access_token"}
                        and isinstance(item, str)
                        and len(item) >= 12
                    ):
                        secrets.append(item.encode())
                    else:
                        collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        collect(json.loads(private.read_text()))
    failures = []
    blocked = {
        "data",
        "runs",
        ".secrets",
        ".research-cache",
        ".vendor",
        ".venv",
        ".venv-docs",
        "node_modules",
        "__pycache__",
        "_build",
    }
    patterns = [
        rb"g2a_[A-Za-z0-9_]{25,}",
        rb"sk-proj-[A-Za-z0-9_-]{20,}",
        rb"gh[pousr]_[A-Za-z0-9]{30,}",
    ]
    for name in names:
        path = ROOT / name
        if blocked.intersection(Path(name).parts) or name == ".env":
            failures.append({"path": name, "rule": "private/generated path"})
        if name.startswith("docs/") and not (
            name.startswith("docs/sphinx/")
            or name in {"docs/package.json", "docs/package-lock.json", "docs/puppeteer.json"}
        ):
            failures.append({"path": name, "rule": "historical archive outside public docs"})
        if path.is_symlink():
            failures.append({"path": name, "rule": "symlink publication"})
            continue
        data = path.read_bytes()
        if any(secret in data for secret in secrets) or any(
            re.search(pattern, data) for pattern in patterns
        ):
            failures.append({"path": name, "rule": "credential literal"})
        if path.suffix in {".py", ".md", ".rst", ".inc", ".json", ".yml"} and re.search(
            b"/" + b"Users" + rb"/[^/\s]+/", data
        ):
            failures.append({"path": name, "rule": "personal absolute path"})
    print(
        json.dumps(
            {"tracked_files": len(names), "failures": failures}, ensure_ascii=False, indent=2
        )
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    check()
