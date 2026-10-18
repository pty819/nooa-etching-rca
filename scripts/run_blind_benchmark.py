"""Create neutral-named physical cases; optionally grade already frozen reports.

The private mapping is kept outside discovery inputs. No model or provider is
called by generation/scoring, and existing datasets/cases are never overwritten.
"""

import argparse
import asyncio
import json
import uuid
from pathlib import Path

import numpy as np

from factor_demo.etch_dataset import generate_etching
from factor_demo.evaluation import finalize_evaluation
from factor_demo.io_utils import write_json
from factor_demo.research_workspace import prepare_workspace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=180)
    parser.add_argument("--seed", type=int, default=20261018)
    parser.add_argument("--score", action="store_true")
    args = parser.parse_args()
    if args.score:
        rows = []
        for case in sorted((args.root / "cases").glob("*")):
            if not (case / "latest_reply.json").exists():
                rows.append({"case": case.name, "status": "report_missing"})
                continue
            try:
                rows.append(
                    {
                        "case": case.name,
                        "status": "scored",
                        "result": asyncio.run(finalize_evaluation(case)),
                    }
                )
            except (ValueError, OSError) as error:
                rows.append({"case": case.name, "status": "incomplete", "error": str(error)})
        write_json(args.root / "benchmark-results.json", rows)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if args.root.exists() and any(args.root.iterdir()):
        raise ValueError("Choose a new benchmark directory")
    args.root.mkdir(parents=True)
    scenarios = [
        "edge_leak_contact",
        "pump_restriction",
        "surface_condition",
        "edge_ring",
        "common_mode_only",
        "stable_control",
    ]
    generator = np.random.default_rng(args.seed)
    generator.shuffle(scenarios)
    private = []
    for i, scenario in enumerate(scenarios):
        identity = "case-" + uuid.uuid4().hex[:12]
        source = args.root / "sources" / identity
        case = args.root / "cases" / identity
        generate_etching(source, runs=args.runs, seed=args.seed + i, scenario=scenario)
        prepare_workspace(source, case)
        private.append({"case": identity, "scenario": scenario})
        print(identity, "ready", flush=True)
    write_json(args.root / "private-case-mapping.json", private)


if __name__ == "__main__":
    main()
