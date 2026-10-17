"""Compute all statistics from the saved, observed traces using Polars."""

from __future__ import annotations

import polars as pl

STATISTICS = [
    "mean",
    "std",
    "min",
    "max",
    "p05",
    "p95",
    "rms",
    "slope",
    "delta",
    "missing_fraction",
]


def extract_features(trace: pl.DataFrame, steps: pl.DataFrame, sensors: list[dict]) -> dict:
    clean = trace.with_columns(pl.col(pl.Float32, pl.Float64).fill_nan(None))
    expressions = []
    for sensor in sensors:
        name = sensor["name"]
        x = pl.col(name)
        t = pl.col("time_s").filter(x.is_not_null())
        var_t = t.var(ddof=0)
        slope = pl.when(var_t > 0).then(pl.cov(x, pl.col("time_s"), ddof=0) / var_t).otherwise(None)
        stats = {
            "mean": x.mean(),
            "std": x.std(ddof=0),
            "min": x.min(),
            "max": x.max(),
            "p05": x.quantile(0.05, interpolation="linear"),
            "p95": x.quantile(0.95, interpolation="linear"),
            "rms": (x * x).mean().sqrt(),
            "slope": slope,
            "delta": x.drop_nulls().last() - x.drop_nulls().first(),
            "missing_fraction": x.null_count() / pl.len(),
        }
        expressions += [value.alias(name + "::" + stat) for stat, value in stats.items()]
    grouped = clean.group_by("step_id", maintain_order=True).agg(expressions)
    if grouped.height != steps.height:
        raise ValueError("Trace must contain every declared step")
    result = {}
    for row in grouped.iter_rows(named=True):
        step = row.pop("step_id")
        for key, value in row.items():
            name, stat = key.split("::")
            result[f"{name}__step_{step:02d}__{stat}"] = value
    return result


def feature_catalog(sensors: list[dict], steps: list[dict]) -> list[dict]:
    result = []
    for step in steps:
        for sensor in sensors:
            for stat in STATISTICS:
                unit = "fraction" if stat == "missing_fraction" else sensor["unit"]
                if stat == "slope":
                    unit += "/s"
                result.append(
                    {
                        "name": f"{sensor['name']}__step_{step['step_id']:02d}__{stat}",
                        "sensor": sensor["name"],
                        "step": f"step_{step['step_id']:02d}",
                        "step_name": step["step_name"],
                        "statistic": stat,
                        "unit": unit,
                        "modality": sensor["subsystem"],
                        "role": sensor["role"],
                        "description": sensor["description"],
                    }
                )
    return result
