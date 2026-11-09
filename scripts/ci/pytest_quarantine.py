"""运行 pytest，并跳过 tests/ci_known_failures.txt 中登记的存量失败用例。

Runtime checks 恢复前，fork 合并与后续提交在 main 上留下了若干已知失败
（测试与代码漂移、旧栈环境依赖等）。这些用例在修复前统一登记，避免让
所有功能 PR 的历史失败淹没新改动，同时保留执行完整测试集的入口。
修复某项后用相应 PR 删除对应行，让测试重新进入门禁。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QUARANTINE = ROOT / "tests" / "ci_known_failures.txt"


def load_deselects() -> list[str]:
    """读取登记文件中的 pytest 节点 id，忽略空行与注释。"""
    if not QUARANTINE.is_file():
        return []
    entries: list[str] = []
    for line in QUARANTINE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            entries.append(stripped)
    return entries


def main() -> None:
    """把登记的用例转换为 pytest --deselect 参数后执行。"""
    command = [sys.executable, "-m", "pytest", *sys.argv[1:]]
    for node_id in load_deselects():
        command.extend(["--deselect", node_id])
    raise SystemExit(subprocess.call(command, cwd=ROOT))


if __name__ == "__main__":
    main()
