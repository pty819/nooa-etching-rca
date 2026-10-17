"""Write chronological case periods, raw telemetry, boundaries and derived features."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from .etch_chemistry import WALL_DESORPTION_PER_S
from .etch_features import extract_features, feature_catalog
from .etch_model import simulate_run
from .etch_recipe import RECIPE_ID, STEPS, hardware_for_period, recipe_document, recipe_sha256
from .etch_sensors import sensor_catalog
from .synthetic import write_json


def _table(rows: list[dict], feature_order: list[str]) -> pl.DataFrame:
    # Build columns rather than asking Polars to infer 22000 mixed row fields.
    keys = ["run_id", "timestamp", "site_id", "recipe_id", "chamber_id", "label", *feature_order]
    return pl.DataFrame({k: [r[k] for r in rows] for k in keys}).with_columns(
        pl.col(feature_order).cast(pl.Float32)
    )


def generate_etching(
    directory: Path, runs: int = 90, seed: int = 20261017, dt: float = 0.5, controls: bool = True
) -> dict:
    if runs < 45 or runs % 5:
        raise ValueError("Use at least 45 target runs and a multiple of 5 (independent lots)")
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Choose an empty directory; old datasets are not overwritten")
    if not 0.1 <= dt <= 1:
        raise ValueError("sample period must be 0.1-1 seconds")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "traces").mkdir()
    (directory / "truth.private").mkdir(mode=0o700)
    catalog = sensor_catalog()
    step_template = [{"step_id": i + 1, "step_name": s.name} for i, s in enumerate(STEPS)]
    features_meta = feature_catalog(catalog, step_template)
    order = [r["name"] for r in features_meta]
    write_json(directory / "sensors.json", catalog)
    write_json(directory / "catalog.json", features_meta)
    recipe_hash = recipe_sha256()
    write_json(directory / "recipe.json", recipe_document())
    batches = runs // 5
    n1 = max(3, int(batches * 0.44))
    n2 = max(3, int(batches * 0.28))
    if batches - n1 - n2 < 3:
        n1 = batches - n2 - 3
    period_batches = [n1, n2, batches - n1 - n2]
    data_rows, control_rows, metadata, step_rows, outcome_rows, truth_rows = [], [], [], [], [], []
    start = datetime(2026, 1, 1, 8)
    sequence = 0
    wall_history = {}
    for period, count in enumerate(period_batches, 1):
        for batch in range(count):
            # Validation data occur within every business state, in separate lots.
            train_count = max(1, count - 2)
            split = "train" if batch < train_count else "dev" if batch == count - 2 else "test"
            batch_id = f"p{period}_lot_{batch:03d}"
            for within in range(5):
                recipe = RECIPE_ID
                target_index = sequence
                for chamber in [0, 1] if controls and within == 0 else [0]:
                    run_id = f"run_{'A' if chamber == 0 else 'B'}_{target_index:05d}"
                    run_seed = int(
                        np.random.SeedSequence([seed, target_index, chamber]).generate_state(1)[0]
                    )
                    hrng = np.random.default_rng(run_seed + 1)
                    hardware = hardware_for_period(period, chamber, hrng)
                    timestamp = start + timedelta(
                        minutes=25 * sequence + 8 * chamber, hours=6 if period == 3 else 0
                    )
                    if chamber in wall_history:
                        last_end, last_wall = wall_history[chamber]
                        idle = max(0.0, (timestamp - last_end).total_seconds())
                        # Between runs the unchanged idle purge removes desorbed product.
                        hardware = replace(
                            hardware,
                            wall_initial_inventory=last_wall
                            * np.exp(-WALL_DESORPTION_PER_S * idle),
                        )
                    simulation = simulate_run(hardware, run_seed, recipe, dt)
                    wall_history[chamber] = (
                        timestamp + timedelta(seconds=sum(s.seconds for s in STEPS)),
                        simulation.latent["wall_product_inventory_proxy"][-1],
                    )
                    relative = f"traces/{run_id}.parquet"
                    simulation.trace.write_parquet(directory / relative, compression="zstd")
                    simulation.latent.write_parquet(
                        directory / "truth.private" / f"{run_id}.parquet", compression="zstd"
                    )
                    label = f"label{period}" if chamber == 0 else "label1"
                    features = extract_features(simulation.trace, simulation.steps, catalog)
                    row = {
                        "run_id": run_id,
                        "timestamp": timestamp,
                        "site_id": "site_01",
                        "recipe_id": recipe,
                        "chamber_id": "chamber_A" if chamber == 0 else "chamber_B",
                        "label": label,
                        **features,
                    }
                    (data_rows if chamber == 0 else control_rows).append(row)
                    metadata.append(
                        {
                            k: row[k]
                            for k in ["run_id", "timestamp", "recipe_id", "chamber_id", "label"]
                        }
                        | {
                            "period_id": period,
                            "batch_id": batch_id,
                            "split": split,
                            "is_control": chamber == 1,
                            "trace_path": relative,
                            "sample_period_s": dt,
                            "recipe_sha256": recipe_hash,
                            "samples": simulation.trace.height,
                        }
                    )
                    step_rows += [dict(run_id=run_id, **r) for r in simulation.steps.to_dicts()]
                    outcome_rows.append(
                        {
                            "run_id": run_id,
                            "period_id": period,
                            "label": label,
                            "chamber_id": row["chamber_id"],
                            "recipe_id": recipe,
                            **simulation.outcomes,
                        }
                    )
                    truth_rows.append(
                        {"run_id": run_id, "period_id": period, "parameters": asdict(hardware)}
                    )
                sequence += 1
                if sequence % 15 == 0:
                    print(f"Generated {sequence}/{runs} target runs", flush=True)
    full = _table(data_rows, order)
    full.write_parquet(directory / "features.parquet", compression="zstd")
    meta = pl.DataFrame(metadata)
    meta.write_parquet(directory / "runs.parquet", compression="zstd")
    pl.DataFrame(step_rows).write_parquet(directory / "steps.parquet", compression="zstd")
    pl.DataFrame(outcome_rows).write_parquet(directory / "outcomes.parquet", compression="zstd")
    if control_rows:
        _table(control_rows, order).write_parquet(
            directory / "control_features.parquet", compression="zstd"
        )
    counts = {}
    for split in ["train", "dev", "test"]:
        ids = meta.filter((pl.col("split") == split) & ~pl.col("is_control"))["run_id"]
        part = full.filter(pl.col("run_id").is_in(ids.implode()))
        # Keep compatibility with the original run-level feature reader.
        part.to_pandas().set_index("run_id").to_parquet(directory / f"{split}.parquet")
        counts[split] = part.height
    write_json(directory / "truth.private" / "hardware.json", truth_rows)
    write_json(
        directory / "truth.private" / "scenario.json",
        {
            "fault": "edge backside helium seal leak plus reduced wafer-ESC contact",
            "repair": "seal leakage repaired; thermal contact remains partially degraded",
            "unobserved_variables": [
                "edge contact factor",
                "wafer actual temperatures",
                "true leakage conductance",
            ],
            "nuisance": "ambient change and redundant heater transducer recalibration in both chambers",
            "warning": "Assumed reduced-order physics; parameters are not calibrated to production equipment",
        },
    )
    phases = (
        meta.filter(~pl.col("is_control"))
        .group_by("period_id", maintain_order=True)
        .agg(
            pl.col("timestamp").min().alias("start"),
            pl.col("timestamp").max().alias("end"),
            pl.col("run_id").len().alias("runs"),
        )
    )
    write_json(
        directory / "case.json",
        {
            "periods": [
                {**r, "start": r["start"].isoformat(), "end": r["end"].isoformat()}
                for r in phases.to_dicts()
            ],
            "label_meaning": {
                "label1": "normal",
                "label2": "major deviation",
                "label3": "after maintenance with residual deviation",
            },
            "maintenance": "Six-hour intervention between periods 2 and 3; detailed work order unavailable",
            "reference": "chamber_B is a contemporaneous normal reference, one sample per lot",
        },
    )
    manifest = {
        "schema_version": 3,
        "kind": "physics_inspired_icp_etch",
        "model_version": "fixed-recipe-species-surface-v2",
        "recipe_id": RECIPE_ID,
        "recipe_sha256": recipe_hash,
        "recipe_invariant": "identical programmed steps, durations, gases, temperature and RF commands in all runs",
        "seed": seed,
        "rows": runs,
        "control_runs": len(control_rows),
        "sensor_channels": 200,
        "features": len(order),
        "step_count": len(STEPS),
        "counts": counts,
        "sample_period_s": dt,
        "period_labels": {
            "label1": "normal",
            "label2": "major deviation",
            "label3": "after maintenance, residual deviation",
        },
        "period_batches": period_batches,
        "batch_size": 5,
        "metadata": ["timestamp", "site_id", "recipe_id", "chamber_id"],
        "label": "label",
        "split": "chronological independent lots within each business period",
        "sha256": {
            s: hashlib.sha256((directory / f"{s}.parquet").read_bytes()).hexdigest() for s in counts
        },
        "trace_sha256": {
            m["run_id"]: hashlib.sha256((directory / m["trace_path"]).read_bytes()).hexdigest()
            for m in metadata
        },
    }
    manifest["model_source_sha256"] = {
        name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
        for name in [
            "etch_model.py",
            "etch_recipe.py",
            "etch_sensors.py",
            "etch_features.py",
            "etch_chemistry.py",
            "etch_dataset.py",
        ]
    }
    manifest["polars_version"] = pl.__version__
    write_json(directory / "manifest.json", manifest)
    return manifest
