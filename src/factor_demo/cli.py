"""Generate, research, resume and finalize the minimal demo."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from .synthetic import generate, write_json

OBJECTIVE = (
    "Find a small set of reproducible sensor-step factors that distinguish label1, label2 "
    "and label3. Check train/development differences and recipe/chamber subgroups. "
    "Use experiments to improve or reject hypotheses, and explain remaining uncertainty. "
    "These are synthetic categorical labels without an assumed numerical order."
)


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
        "本 demo 没有构建 OS 安全沙箱；工具可见性不等同于对恶意生成代码的安全隔离。",
        "分类关联不等于机械根因。",
        "",
    ]
    target = run / "report.md"
    target.write_text("\n".join(lines))
    return target


async def run_research(
    data: Path,
    run: Path,
    budget: int,
    provider_name: str,
    note: str,
    timeout: int = 3600,
    settings_path: Path = Path("configs/runtime.json"),
):
    from .providers import load_provider
    from .research import Research
    from .runtime import RuntimeSettings, build_client, context_truncation, install_summarizer

    backend = Research(data, run, budget)
    if note:
        backend.add_note(note)
    provider = load_provider(provider_name)
    settings = RuntimeSettings.load(settings_path)
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    from nooa.tracing import enable_tracing, exporters

    from .agent import FactorResearchAgent

    llm = build_client(provider, settings)
    write_json(
        run / "provider.json",
        {
            "provider": provider.name,
            "model": provider.model,
            "base_url": provider.base_url,
            "nooa_commit": "1454b78c8f0a85c97eab1c6a91cc8cfc95b34de5",
        },
    )
    write_json(run / "runtime.json", settings.public_record(provider))
    enable_tracing(exporters=[exporters.journal_file(run / "traces")])
    backend._save_state("running")
    print(f"Research: {provider.name}/{provider.model}; budget={budget}; run={run}", flush=True)
    agent = None
    try:
        agent = FactorResearchAgent(backend, llm=llm, truncation=context_truncation(settings))
        install_summarizer(agent, settings)
        conclusion = await asyncio.wait_for(agent.research(OBJECTIVE), timeout=timeout)
        selected = next(
            (
                r
                for r in backend.records
                if r["id"] == conclusion.selected_experiment_id and r["status"] == "success"
            ),
            None,
        )
        if selected is None:
            raise ValueError("Agent did not select an actual successful experiment")
        write_json(run / "conclusion.json", conclusion.model_dump())
        (run / "error.json").unlink(missing_ok=True)
        backend._save_state("completed")
        print(
            f"Selected {selected['id']}, dev balanced accuracy={selected['metrics']['balanced_accuracy']:.4f}"
        )
    except BaseException as error:
        backend._save_state("interrupted")
        # Persist type only: provider exceptions may embed request/credential details.
        write_json(run / "error.json", {"type": type(error).__name__, "time": time.time()})
        raise
    finally:
        if agent is not None:
            await agent.aclose()
        report(run)
        from opentelemetry import trace

        trace.get_tracer_provider().force_flush()


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
    parser = argparse.ArgumentParser(description="Minimal NOOA factor research demo")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("serve", help="Run the local live research frontend")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p = sub.add_parser("import-hermes")
    p.add_argument("--host", required=True, help="Explicit SSH destination containing your Hermes config")
    p = sub.add_parser("probe")
    p.add_argument("--provider", default="glm")
    p = sub.add_parser(
        "generate", help="Legacy feature-only fixture; use generate-etch for physical traces"
    )
    p.add_argument("--data", type=Path, default=Path("data/demo"))
    p.add_argument("--rows", type=int, default=2400)
    p.add_argument("--seed", type=int, default=42)
    p = sub.add_parser("generate-etch", help="Generate 200-channel ICP traces, steps and features")
    p.add_argument("--data", type=Path, default=Path("data/etching-v2"))
    p.add_argument("--runs", type=int, default=90)
    p.add_argument("--seed", type=int, default=20261017)
    p.add_argument("--sample-period", type=float, default=0.5)
    p.add_argument("--no-controls", action="store_true")
    for name in ["validate-etch", "preview-etch"]:
        p = sub.add_parser(name)
        p.add_argument("--data", type=Path, default=Path("data/etching-v2"))
    for name in ["research", "resume"]:
        p = sub.add_parser(name)
        p.add_argument("--run", type=Path, required=True)
        p.add_argument("--data", type=Path, default=Path("data/demo"))
        p.add_argument("--provider", default="glm")
        p.add_argument("--max-experiments", type=int, default=8)
        p.add_argument("--note", default="")
        p.add_argument("--timeout", type=int, default=3600)
        p.add_argument("--settings", type=Path, default=Path("configs/runtime.json"))
    for name in ["finalize", "report", "autoresearch-audit"]:
        p = sub.add_parser(name)
        p.add_argument("--run", type=Path, required=True)
    for name in ["autoresearch", "autoresearch-chat"]:
        p = sub.add_parser(name, help="Free-code NOOA research with persistent expert dialogue")
        p.add_argument("--run", type=Path, required=True)
        if name == "autoresearch":
            p.add_argument("--data", type=Path, default=Path("data/etching-v2"))
        p.add_argument("--provider", default="glm")
        p.add_argument("--message", default="")
        p.add_argument("--message-file", type=Path)
        p.add_argument("--mode", choices=["research", "discuss"], default="research")
        p.add_argument("--speaker", choices=["user", "simulated_expert"], default="user")
        p.add_argument("--settings", type=Path, default=Path("configs/runtime.json"))
        p.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args()
    if args.command == "serve":
        from .web import serve

        serve(args.host, args.port)
    elif args.command == "generate-etch":
        from .etch_dataset import generate_etching
        from .etch_preview import preview_etching
        from .etch_validation import validate_etching

        manifest = generate_etching(
            args.data, args.runs, args.seed, args.sample_period, not args.no_controls
        )
        print(json.dumps({k: v for k, v in manifest.items() if not k.endswith("sha256")}, indent=2))
        print(json.dumps(validate_etching(args.data), ensure_ascii=False, indent=2))
        print(preview_etching(args.data))
    elif args.command == "validate-etch":
        from .etch_validation import validate_etching

        print(json.dumps(validate_etching(args.data), ensure_ascii=False, indent=2))
    elif args.command == "preview-etch":
        from .etch_preview import preview_etching

        print(preview_etching(args.data))
    elif args.command == "generate":
        print(json.dumps(generate(args.data, args.rows, args.seed), indent=2))
    elif args.command == "import-hermes":
        from .providers import import_hermes

        print(json.dumps(import_hermes(args.host), indent=2))
    elif args.command == "probe":
        from .providers import load_provider, probe

        print(json.dumps(probe(load_provider(args.provider)), indent=2))
    elif args.command in ["autoresearch", "autoresearch-chat"]:
        from .autoresearch import research_turn

        message = args.message_file.read_text() if args.message_file else args.message
        asyncio.run(
            research_turn(
                args.run,
                args.provider,
                message,
                args.mode,
                args.settings,
                args.timeout,
                args.data if args.command == "autoresearch" else None,
                args.speaker,
            )
        )
    elif args.command in ["research", "resume"]:
        if args.max_experiments < 1 or args.max_experiments > 100:
            parser.error("max-experiments must be 1-100")
        if args.command == "research" and args.run.exists() and any(args.run.iterdir()):
            parser.error("Run directory already exists; use resume or a new directory")
        if args.command == "resume":
            cfg = json.loads((args.run / "config.json").read_text())
            args.data = Path(cfg["data"])
        asyncio.run(
            run_research(
                args.data,
                args.run,
                args.max_experiments,
                args.provider,
                args.note,
                args.timeout,
                args.settings,
            )
        )
    elif args.command == "finalize":
        print(json.dumps(finalize(args.run), ensure_ascii=False, indent=2))
    elif args.command == "autoresearch-audit":
        from .research_audit import audit_session

        print(json.dumps(audit_session(args.run), ensure_ascii=False, indent=2))
    else:
        print(report(args.run))


if __name__ == "__main__":
    main()
