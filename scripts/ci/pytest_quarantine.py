"""运行 pytest，并跳过 tests/ci_known_failures.txt 中登记的存量失败用例。

Runtime checks 恢复前，fork 合并与后续提交在 main 上留下了若干已知失败
（测试与代码漂移、旧栈环境依赖等）。这些用例在修复前统一登记，避免让
所有功能 PR 的历史失败淹没新改动，同时保留执行完整测试集的入口。
修复某项后用相应 PR 删除对应行，让测试重新进入门禁。

节点 id 后可带平台限定，例如 `` @macos`` 或 `` @linux,macos``：
只有当前平台在列表内时才隔离该用例，其余平台照常执行。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUARANTINE = ROOT / "tests" / "ci_known_failures.txt"


def current_platform() -> str:
    """返回隔离清单使用的平台名。"""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def load_deselects() -> list[str]:
    """读取登记文件中适用于当前平台的 pytest 节点 id。"""
    if not QUARANTINE.is_file():
        return []
    current = current_platform()
    entries: list[str] = []
    for line in QUARANTINE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        node_id, _, scope = stripped.partition(" @")
        platforms = [item.strip() for item in scope.split(",") if item.strip()]
        if platforms and current not in platforms:
            continue
        entries.append(node_id)
    return entries


def main() -> None:
    """把登记的用例转换为 pytest --deselect 参数后执行。"""
    command = [sys.executable, "-m", "pytest", *sys.argv[1:]]
    for node_id in load_deselects():
        command.extend(["--deselect", node_id])
    raise SystemExit(subprocess.call(command, cwd=ROOT))


if __name__ == "__main__":
    main()
