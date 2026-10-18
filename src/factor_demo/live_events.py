"""Durable cross-process SSE events and the agent's Markdown reply channel."""

from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import quote

import polars as pl


class LiveEvents:
    def __init__(
        self,
        run: Path,
        task_id: str = "",
        *,
        root: Path | None = None,
        agent_id: str = "main",
        parent_agent_id: str = "",
    ):
        self.run = run.resolve()
        self.root = (root or run).resolve()
        self.agent_id = agent_id
        self.parent_agent_id = parent_agent_id
        self.task_id = task_id
        self.closed = False
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "ui.sqlite", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                type TEXT NOT NULL, data TEXT NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS inbox(
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                message TEXT NOT NULL, consumed INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS requests(request_id TEXT PRIMARY KEY, task_id TEXT NOT NULL);
        """)
        # Serialize additive migration when HTTP replay and new branch hosts connect together.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            for table, column, definition in [
                ("events", "agent_id", "TEXT NOT NULL DEFAULT 'main'"),
                ("events", "parent_agent_id", "TEXT NOT NULL DEFAULT ''"),
                ("inbox", "agent_id", "TEXT NOT NULL DEFAULT 'main'"),
                ("inbox", "source", "TEXT NOT NULL DEFAULT 'user'"),
            ]:
                if column not in {row[1] for row in self.db.execute(f"PRAGMA table_info({table})")}:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def emit(self, kind: str, **data) -> int:
        with self.lock:
            if self.closed:
                return 0
            with self.db:
                row = self.db.execute(
                    "INSERT INTO events(task_id,type,data,created,agent_id,parent_agent_id) VALUES(?,?,?,?,?,?)",
                    (
                        self.task_id,
                        kind,
                        json.dumps(data, ensure_ascii=False),
                        time.time(),
                        self.agent_id,
                        self.parent_agent_id,
                    ),
                )
                return row.lastrowid

    def since(self, after: int, limit: int = 256) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id,task_id,type,data,created,agent_id,parent_agent_id FROM events WHERE id>? ORDER BY id LIMIT ?",
                (after, limit),
            ).fetchall()
        return [
            {
                "id": i,
                "task_id": t,
                "type": k,
                "data": json.loads(d),
                "time": s,
                "agent_id": agent,
                "parent_agent_id": parent,
            }
            for i, t, k, d, s, agent, parent in rows
        ]

    def add_guidance(self, message: str, source: str = "user"):
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO inbox(task_id,message,agent_id,source) VALUES(?,?,?,?)",
                (self.task_id, message, self.agent_id, source),
            )

    def take_guidance_records(self) -> list[dict]:
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            rows = self.db.execute(
                "SELECT id,message,source FROM inbox WHERE task_id=? AND agent_id=? AND consumed=0 ORDER BY id",
                (self.task_id, self.agent_id),
            ).fetchall()
            self.db.executemany("UPDATE inbox SET consumed=1 WHERE id=?", [(r[0],) for r in rows])
        return [{"message": r[1], "source": r[2]} for r in rows]

    def take_guidance(self) -> list[str]:
        return [r["message"] for r in self.take_guidance_records()]

    def close(self):
        with self.lock:
            if not self.closed:
                self.closed = True
                self.db.close()


def artifact_root(run: Path) -> Path:
    work = (run / "workspace").resolve()
    root = run / "workspace/artifacts"
    if root.is_symlink() or not root.resolve().is_relative_to(work):
        raise ValueError("Artifact directory must remain inside the research workspace")
    return root.resolve()


def artifact_path(run: Path, name: str) -> Path:
    root = artifact_root(run)
    path = (run / "workspace" / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Choose an existing file under artifacts/")
    return path


class MarkdownReplies:
    def __init__(self, events: LiveEvents):
        self.events = events
        self.committed = set()
        self.publication = "progress"
        self.review_status = None
        self.final_appendix = ""

    def url(self, name: str) -> str:
        public_artifact_path(self.events.run, name)
        normalized = str(Path(name))
        if self.events.root != self.events.run:
            normalized = f"branches/{self.events.agent_id}/{normalized}"
        return (
            f"/api/sessions/{quote(self.events.root.name)}/artifacts/{quote(normalized, safe='/')}"
        )

    def prepare(self, markdown: str, image_paths=None, table_paths=None) -> tuple[str, list]:
        body = markdown
        if self.publication in {"final", "checkpoint"} and self.final_appendix:
            body += "\n\n" + self.final_appendix
        attachments = []
        for name in image_paths or []:
            path = public_artifact_path(self.events.run, name)
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                raise ValueError("Use PNG, JPEG, WebP or GIF for inline images")
            url = self.url(name)
            body += f"\n\n![{path.stem}]({url})"
            attachments.append({"path": name, "url": url, "kind": "image"})
        for name in table_paths or []:
            path = public_artifact_path(self.events.run, name)
            if path.suffix.lower() != ".csv":
                raise ValueError("Use CSV files for inline table previews")
            frame = pl.read_csv(path, infer_schema=False, n_rows=21)
            if frame.width > 30:
                raise ValueError("Select at most 30 columns for a table preview")

            def cell(value):
                return (
                    str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")
                )

            rows = [
                "| " + " | ".join(map(cell, frame.columns)) + " |",
                "| " + " | ".join("---" for _ in frame.columns) + " |",
            ]
            rows += ["| " + " | ".join(map(cell, row)) + " |" for row in frame.head(20).iter_rows()]
            url = self.url(name)
            body += "\n\n" + "\n".join(rows)
            if frame.height > 20:
                body += "\n\n*仅展示前20行，完整数据见下载文件。*"
            body += f"\n\n[下载完整表格]({url})"
            attachments.append(
                {"path": name, "url": url, "kind": "table", "preview_truncated": frame.height > 20}
            )
        return body, attachments

    def send(self, message_id: str, markdown: str, image_paths=None, table_paths=None) -> dict:
        if not re.fullmatch(r"[\w-]{1,100}", message_id):
            raise ValueError("message_id must be 1-100 letters, digits, underscores or hyphens")
        body, attachments = self.prepare(markdown, image_paths, table_paths)
        if self.publication in {"final", "checkpoint"} and self.review_status:
            body = (
                f"> 审查状态：{self.review_status}（本轮检查状态，不代表根因已被证实）\n\n" + body
            )
        # Canonical replacement reconciles streamed text without duplicating messages.
        self.events.emit(
            "reply.commit",
            message_id=message_id,
            markdown=body,
            attachments=attachments,
            publication=self.publication,
            review_status=self.review_status,
        )
        self.committed.add(message_id)
        return {"delivered": True, "message_id": message_id, "attachments": len(attachments)}


def artifact_index(run: Path, prefix: str = "artifacts") -> list[dict]:
    root = artifact_root(run)
    start = (run / "workspace" / prefix).resolve()
    if not start.is_relative_to(root):
        raise ValueError("Browse inside artifacts/")
    return [
        {
            "path": str(p.relative_to(run / "workspace")),
            "size": p.stat().st_size,
            "mime": mimetypes.guess_type(p.name)[0] or "application/octet-stream",
        }
        for p in sorted(start.rglob("*"))
        if p.is_file() and p.resolve().is_relative_to(root) and "__pycache__" not in p.parts
    ][:300]


def branch_path(root: Path, agent_id: str) -> Path:
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", agent_id) or agent_id == "main":
        raise ValueError("Invalid branch identity")
    base = (root / "branches").resolve()
    path = (base / agent_id).resolve()
    if path.parent != base or not (path / "branch.json").is_file():
        raise ValueError("Unknown research branch")
    return path


def public_artifact_path(run: Path, name: str) -> Path:
    parts = Path(name).parts
    if len(parts) >= 3 and parts[0] == "branches":
        return artifact_path(branch_path(run, parts[1]), str(Path(*parts[2:])))
    return artifact_path(run, name)


def public_artifact_index(run: Path) -> list[dict]:
    result = [{**f, "agent_id": "main"} for f in artifact_index(run)]
    for marker in sorted((run / "branches").glob("*/branch.json")):
        try:
            branch = branch_path(run, marker.parent.name)
        except ValueError:
            continue
        result.extend(
            {**f, "path": f"branches/{branch.name}/{f['path']}", "agent_id": branch.name}
            for f in artifact_index(branch)
        )
    return result
