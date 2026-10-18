"""Historical feature fixtures and reports; interactive research uses the modern CLI."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from ..io_utils import write_json
from .synthetic import generate


def report(run: Path) -> Path:

    records = (
        [json.loads(l) for l in (run / "experiments.jsonl").read_text().splitlines()]
        if (run / "experiments.jsonl").exists()
        else []
    )
    lines = [
        "# NOOA 因子研究运行报告",
        "",
        "所有得分来自固定评估器的实际计算。",
        "",
        "| 实验 | 状态 | 开发集 balanced accuracy | 因子数 |",
        "|---|---|---:|---:|",
    ]
    for row in records:
        score = row.get("metrics", {}).get("balanced_accuracy")
        lines.append(
            f"| {row['id']} | {row['status']} | {score if score is not None else '-'} | {len(row['factors'])} |"
        )
    if (run / "conclusion.json").exists():
        conclusion = json.loads((run / "conclusion.json").read_text())
        selected = next(
            (r for r in records if r["id"] == conclusion["selected_experiment_id"]), None
        )
        lines += [
            "",
            "## Agent 选择的候选",
            "",
            "所选实验：" + conclusion["selected_experiment_id"],
            "",
        ]
        if selected:
            lines += [
                "```json",
                json.dumps(selected["factors"], ensure_ascii=False, indent=2),
                "```",
            ]
        lines += [
            "",
            "模型生成的解释与局限保存在 conclusion.json，需对照实验记录审查。此报告的得分表由宿主程序生成。",
        ]
    if (run / "final.json").exists():
        final = json.loads((run / "final.json").read_text())
        lines += [
            "",
            "## 冻结后的独立验收",
            "",
            "```json",
            json.dumps(final, ensure_ascii=False, indent=2),
            "```",
        ]
    else:
        lines += ["", "最终验收集尚未评估。开发集结果属于探索结果。"]
    lines += [
        "",
        "研究使用 NOOA CodeAct 动态工具循环。模型输入包括摘要与开发结果，不包括验收数据或合成真值。",
        "这是早期特征表实验的历史汇总；现代自由代码流程与受限执行说明见当前设计文档。",
        "分类关联不等于机械根因。",
        "",
    ]
    target = run / "report.md"
    target.write_text("\n".join(lines))
    return target


def finalize(run: Path) -> dict:
    import pandas as pd

    from .factors import Factor
    from .research import score_candidate

    if (run / "final.json").exists():
        return json.loads((run / "final.json").read_text())
    if (run / "frozen.json").exists():
        frozen = json.loads((run / "frozen.json").read_text())
    else:
        conclusion = json.loads((run / "conclusion.json").read_text())
        records = [json.loads(l) for l in (run / "experiments.jsonl").read_text().splitlines()]
        selected = next(
            r
            for r in records
            if r["id"] == conclusion["selected_experiment_id"] and r["status"] == "success"
        )
        frozen = {
            "selected": selected,
            "frozen_at": time.time(),
            "config": json.loads((run / "config.json").read_text()),
        }
        # Freeze before reading holdout. A failed finalize may be retried but not revised.
        write_json(run / "frozen.json", frozen)
    data = Path(frozen["config"]["data"])
    for name, digest in frozen["config"]["dataset_sha256"].items():
        if hashlib.sha256((data / f"{name}.parquet").read_bytes()).hexdigest() != digest:
            raise ValueError("Dataset content changed after research")
    specs = [Factor.model_validate(s) for s in frozen["selected"]["factors"]]
    train, test = pd.read_parquet(data / "train.parquet"), pd.read_parquet(data / "test.parquet")
    allowed = {r["name"] for r in json.loads((data / "catalog.json").read_text())}
    result = {
        "selected_experiment_id": frozen["selected"]["id"],
        "factors": frozen["selected"]["factors"],
        "development": frozen["selected"]["metrics"],
        "holdout": score_candidate(train, test, specs, allowed),
    }
    truth_path = data / "truth.private.json"
    if truth_path.exists():
        truth = json.loads(truth_path.read_text())
        result["synthetic_truth_check"] = {
            "contains_injected_difference": any(
                s.operation == "difference"
                and set(s.columns) == set(truth["difference"]["columns"])
                for s in specs
            ),
            "contains_unstable_raw": any(
                s.operation == "raw" and s.columns == [truth["unstable_signal"]] for s in specs
            ),
            "contains_unstable_input": any(truth["unstable_signal"] in s.columns for s in specs),
            "note": "Host-side fixture check; not a physical root cause result",
        }
    write_json(run / "final.json", result)
    state = json.loads((run / "state.json").read_text())
    state.update(status="finalized", selected_experiment_id=result["selected_experiment_id"])
    write_json(run / "state.json", state)
    report(run)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Legacy feature-only fixtures and historical reports"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("generate")
    p.add_argument("--data", type=Path, default=Path("data/demo"))
    p.add_argument("--rows", type=int, default=2400)
    p.add_argument("--seed", type=int, default=42)
    for name in ["finalize", "report"]:
        p = commands.add_parser(name)
        p.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "generate":
        print(json.dumps(generate(args.data, args.rows, args.seed), indent=2))
    elif args.command == "finalize":
        print(json.dumps(finalize(args.run), ensure_ascii=False, indent=2))
    else:
        print(report(args.run))


if __name__ == "__main__":
    main()
