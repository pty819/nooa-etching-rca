"""Reproducible feature-level fixture, with private truth for host-side assessment."""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from ..io_utils import write_json

METADATA = ["timestamp", "site_id", "recipe_id", "chamber_id"]
STATS = ["mean", "std", "min", "max"]


def generate(directory: Path, rows: int = 2400, seed: int = 42) -> dict:
    if rows < 300 or rows % 100:
        raise ValueError("rows must be a multiple of 100 and at least 300")
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("Choose an empty data directory; existing data will not be overwritten")
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    labels = np.tile(np.arange(3), (rows + 2) // 3)[:rows]
    rng.shuffle(labels)
    recipe = rng.integers(0, 3, rows)
    chamber = rng.integers(0, 4, rows)
    feature_names = [
        f"sensor_{s:03d}__step_{t:02d}__{stat}"
        for s in range(100)
        for t in range(1, 6)
        for stat in STATS
    ]
    x = rng.normal(size=(rows, 2000))
    # Stable sensor/recipe offsets, independent of label, provide redundant context.
    for s in range(100):
        x[:, s * 20 : (s + 1) * 20] += (recipe * 0.35 + chamber * 0.15)[:, None]
    a_sensor, b_sensor, nuisance_sensor = rng.choice(100, 3, replace=False).tolist()
    raw = f"sensor_{a_sensor:03d}__step_02__mean"
    before = f"sensor_{b_sensor:03d}__step_02__mean"
    after = f"sensor_{b_sensor:03d}__step_03__mean"
    nuisance = f"sensor_{nuisance_sensor:03d}__step_04__mean"
    z = labels - 1
    shared = rng.normal(0, 2.5, rows) + recipe * 2.0
    x[:, feature_names.index(raw)] = z * 1.0 + rng.normal(0, 1.0, rows) + recipe * 0.5
    x[:, feature_names.index(before)] = shared + rng.normal(0, 0.30, rows)
    x[:, feature_names.index(after)] = shared + z * 2.0 + rng.normal(0, 0.30, rows)
    train_end, dev_end = int(rows * 0.6), int(rows * 0.8)
    spur = rng.normal(0, 0.6, rows)
    spur[:train_end] += z[:train_end] * 2.0
    spur[train_end:dev_end] -= z[train_end:dev_end] * 2.0
    x[:, feature_names.index(nuisance)] = spur
    x[rng.random(x.shape) < 0.003] = np.nan
    features = pd.DataFrame(x.astype("float32"), columns=feature_names)
    meta = pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=rows, freq="h"),
            "site_id": [f"site_{v}" for v in rng.integers(0, 2, rows)],
            "recipe_id": [f"recipe_{v}" for v in recipe],
            "chamber_id": [f"chamber_{v}" for v in chamber],
            "label": [f"label{v + 1}" for v in labels],
        }
    )
    df = pd.concat([meta, features], axis=1)
    df.index = pd.Index([f"run_{i:06d}" for i in range(rows)], name="run_id")
    parts = {
        "train": df.iloc[:train_end],
        "dev": df.iloc[train_end:dev_end],
        "test": df.iloc[dev_end:],
    }
    for name, part in parts.items():
        part.to_parquet(directory / f"{name}.parquet")
    catalog = [
        {
            "name": f,
            "sensor": f.split("__")[0],
            "step": f.split("__")[1],
            "statistic": f.split("__")[2],
            "modality": ["thermal", "pressure", "electrical"][i // 20 % 3],
            "unit": "synthetic_unit",
        }
        for i, f in enumerate(feature_names)
    ]
    write_json(directory / "catalog.json", catalog)
    manifest = {
        "schema_version": 1,
        "seed": seed,
        "rows": rows,
        "features": 2000,
        "metadata": METADATA,
        "label": "label",
        "split": "chronological 60/20/20",
        "batch_size": 20,
        "counts": {k: len(v) for k, v in parts.items()},
        "sha256": {
            k: hashlib.sha256((directory / f"{k}.parquet").read_bytes()).hexdigest() for k in parts
        },
    }
    write_json(directory / "manifest.json", manifest)
    write_json(
        directory / "truth.private.json",
        {
            "raw_signal": raw,
            "difference": {"operation": "difference", "columns": [after, before]},
            "unstable_signal": nuisance,
            "meaning": "Synthetic associations, not physical causes",
        },
    )
    return manifest
