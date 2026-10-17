"""Physical, temporal, split and feature-recomputation checks for saved artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import polars as pl

from .etch_features import extract_features
from .etch_recipe import STEPS, make_schedule
from .synthetic import write_json


def validate_etching(directory: Path, recompute: int = 3) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text())
    sensors = json.loads((directory / "sensors.json").read_text())
    names = [s["name"] for s in sensors]
    assert len(names) == 200 and len(set(names)) == 200
    meta = pl.read_parquet(directory / "runs.parquet")
    steps = pl.read_parquet(directory / "steps.parquet")
    features = pl.read_parquet(directory / "features.parquet")
    assert features.height == manifest["rows"]
    assert features.width == manifest["features"] + 6
    expected_steps = [s.name for s in STEPS]
    failures = []
    fixed_recipe = manifest.get("schema_version", 0) >= 3
    reference_commands = reference_steps = None
    command_names = [s["name"] for s in sensors if s.get("command_scope") == "recipe"]
    if fixed_recipe:
        recipe = json.loads((directory / "recipe.json").read_text())
        digest = hashlib.sha256(
            json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert digest == manifest["recipe_sha256"], "recipe document hash mismatch"
        assert meta["recipe_sha256"].unique().to_list() == [digest]
        assert meta["recipe_id"].unique().to_list() == [recipe["recipe_id"]]
        expected_schedule = pl.DataFrame(
            make_schedule(manifest["sample_period_s"], recipe["recipe_id"])
        )
    max_rf_error = 0.0
    max_inventory_error = 0.0
    latent_checked = 0
    samples = 0
    missing = 0
    for run in meta.iter_rows(named=True):
        path = directory / run["trace_path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["trace_sha256"][run["run_id"]]:
            raise ValueError(run["run_id"] + ": trace hash mismatch")
        frame = pl.read_parquet(path)
        latent_path = directory / "truth.private" / f"{run['run_id']}.parquet"
        if fixed_recipe and latent_path.exists():
            latent = pl.read_parquet(latent_path)
            assert latent.height == frame.height
            for name in [
                "neutral_radical_proxy",
                "gas_product_inventory_proxy",
                "wall_product_inventory_proxy",
                "actual_residence_s",
                "ion_flux_proxy",
                "ion_energy_center_ev_proxy",
                "ion_energy_edge_ev_proxy",
            ]:
                assert np.isfinite(latent[name].to_numpy()).all() and latent[name].min() >= 0
            assert (
                0 <= latent["wall_coverage_proxy"].min() <= latent["wall_coverage_proxy"].max() <= 1
            )
            max_inventory_error = max(
                max_inventory_error, latent["product_balance_max_error"].max()
            )
            latent_checked += 1
        st = steps.filter(pl.col("run_id") == run["run_id"])
        if fixed_recipe:
            programmed = st.drop("run_id")
            assert programmed.equals(expected_schedule), "stored schedule differs from fixed recipe"
            commands = frame.select("time_s", "step_id", *command_names)
            if reference_commands is None:
                reference_commands, reference_steps = commands, programmed
            assert commands.equals(reference_commands), (
                "recipe command waveform changed between runs"
            )
            assert programmed.equals(reference_steps), "step program changed between runs"
        assert st["step_name"].to_list() == expected_steps
        time = frame["time_s"].to_numpy()
        assert frame.columns == ["time_s", "step_id", *names]
        assert np.allclose(np.diff(time), run["sample_period_s"])
        assert np.isclose(st["start_s"][0], 0)
        assert np.isclose(st["end_s"][-1], time[-1] + run["sample_period_s"])
        assert np.allclose(st["end_s"].to_numpy()[:-1], st["start_s"].to_numpy()[1:])
        for step in st.iter_rows(named=True):
            mask = (time >= step["start_s"] - 1e-8) & (time < step["end_s"] - 1e-8)
            assert mask.sum() == step["samples"]
            assert np.all(frame["step_id"].to_numpy()[mask] == step["step_id"])
        x = frame.select(names).to_numpy()
        assert not np.isinf(x).any()
        for i, sensor in enumerate(sensors):
            finite = x[np.isfinite(x[:, i]), i]
            if (finite < sensor["minimum"] - 1e-4).any() or (
                finite > sensor["maximum"] + 1e-4
            ).any():
                failures.append(run["run_id"] + ": bounds " + sensor["name"])
        for prefix in ["rf_source", "rf_bias"]:
            forward = frame[prefix + "_forward_w"].to_numpy()
            reflected = frame[prefix + "_reflected_w"].to_numpy()
            delivered = frame[prefix + "_delivered_w"].to_numpy()
            assert np.allclose(delivered, forward - reflected, equal_nan=True, atol=0.0002)
            valid = np.isfinite(forward) & np.isfinite(reflected)
            assert np.all(reflected[valid] <= forward[valid] + 0.001)
            v = frame[prefix + "_voltage_v_rms"].to_numpy()
            i = frame[prefix + "_current_a_rms"].to_numpy()
            phase = frame[prefix + "_phase_deg"].to_numpy()
            power_vi = v * i * np.cos(np.radians(phase))
            valid = np.isfinite(power_vi) & np.isfinite(delivered) & (delivered > 10)
            err = np.abs(power_vi[valid] - delivered[valid]) / delivered[valid]
            if len(err):
                max_rf_error = max(max_rf_error, float(err.max()))
        robot = frame["robot_in_chamber"].to_numpy() > 0
        power = frame["rf_source_forward_w"].to_numpy() + frame["rf_bias_forward_w"].to_numpy()
        assert not np.any(robot & (power > 0.1))
        he_cmd = frame["he_edge_pressure_sp_torr"].to_numpy() > 0
        voltage = frame["esc_voltage_v"].to_numpy()
        assert np.all(voltage[he_cmd & np.isfinite(voltage)] > 899)
        assert not np.any(he_cmd & robot)
        released = voltage < 50
        he = np.maximum(
            frame["he_edge_pressure_torr"].to_numpy(), frame["he_center_pressure_torr"].to_numpy()
        )
        assert not np.any(released & (he > 0.32))
        samples += frame.height
        missing += int(np.isnan(x).sum())
    assert max_rf_error < 0.05, max_rf_error
    assert max_inventory_error < 1e-10, max_inventory_error
    # Check all raw-derived summary constraints, not only the injected signals.
    minima = [c for c in features.columns if c.endswith("__min")]
    for lo in minima:
        base = lo[:-3]
        low = features[lo].to_numpy()
        mean = features[base + "mean"].to_numpy()
        high = features[base + "max"].to_numpy()
        assert np.all((low <= mean + 1e-4) | np.isnan(low) | np.isnan(mean))
        assert np.all((mean <= high + 1e-4) | np.isnan(mean) | np.isnan(high))
        assert np.all(features[base + "std"].fill_null(0).to_numpy() >= 0)
    for split, digest in manifest["sha256"].items():
        assert hashlib.sha256((directory / f"{split}.parquet").read_bytes()).hexdigest() == digest
    batch_sets = {
        s: set(meta.filter((pl.col("split") == s) & ~pl.col("is_control"))["batch_id"].to_list())
        for s in ["train", "dev", "test"]
    }
    assert not (
        batch_sets["train"] & batch_sets["dev"]
        or batch_sets["train"] & batch_sets["test"]
        or batch_sets["dev"] & batch_sets["test"]
    )
    target = meta.filter(~pl.col("is_control")).sort("timestamp")
    assert target["period_id"].to_list() == sorted(target["period_id"].to_list())
    for split in batch_sets:
        assert set(target.filter(pl.col("split") == split)["label"]) == {
            "label1",
            "label2",
            "label3",
        }
    chosen = target.group_by("period_id", maintain_order=True).first().head(recompute)
    checked = 0
    for row in chosen.iter_rows(named=True):
        raw = pl.read_parquet(directory / row["trace_path"])
        st = steps.filter(pl.col("run_id") == row["run_id"])
        actual = extract_features(raw, st, sensors)
        saved = features.filter(pl.col("run_id") == row["run_id"]).row(0, named=True)
        for key, value in actual.items():
            assert np.isclose(value, saved[key], equal_nan=True, rtol=2e-6, atol=1e-5), key
        checked += 1
    if failures:
        raise AssertionError(failures[:20])
    outcomes = pl.read_parquet(directory / "outcomes.parquet")
    study = (
        outcomes.filter(pl.col("chamber_id") == "chamber_A")
        .group_by("label", maintain_order=True)
        .agg(pl.col("center_edge_nonuniformity_pct").median(), pl.col("depth_mean_nm").median())
    )
    result = {
        "passed": True,
        "raw_runs": meta.height,
        "target_runs": target.height,
        "sensor_channels": 200,
        "step_count": 11,
        "raw_samples": samples,
        "feature_count": manifest["features"],
        "feature_runs_fully_recomputed": checked,
        "missing_fraction": missing / (samples * 200),
        "max_rf_vi_power_relative_error": max_rf_error,
        "summary_constraints": "all min<=mean<=max and std>=0",
        "interlocks": "robot/RF, ESC/backside-He checked on all traces",
        "fixed_recipe_verified": fixed_recipe,
        "recipe_count": meta["recipe_id"].n_unique(),
        "identical_recipe_command_channels": len(command_names) if fixed_recipe else None,
        "recipe_sha256": manifest.get("recipe_sha256"),
        "hidden_physics_runs_checked": latent_checked,
        "max_product_inventory_balance_error": max_inventory_error if latent_checked else None,
        "case_metrology_medians": study.to_dicts(),
        "case_metrology_by_recipe": outcomes.filter(pl.col("chamber_id") == "chamber_A")
        .group_by("recipe_id", "label")
        .agg(pl.col("center_edge_nonuniformity_pct").median())
        .sort("recipe_id", "label")
        .to_dicts(),
        "limits": "Internal consistency checks for a reduced-order simulation; not a validated fab digital twin",
    }
    write_json(directory / "validation.json", result)
    return result
