"""Truth assessment for the bundled etch simulator only; never an agent prior."""

import json

import numpy as np
import polars as pl

MECHANISMS = {
    "backside_he_leak": ("edge_leak_sccm_per_torr", 0.1, ["he_edge_"], [4, 8]),
    "wafer_esc_contact": ("edge_contact_factor", 0.1, ["heater_z", "he_edge_"], [4, 8]),
    "pump_restriction": ("pump_conductance_factor", 0.05, ["chamber_pressure", "pcv_"], [4, 8]),
    "surface_condition": ("wall_recombination_factor", 0.1, ["oes_", "rf_source_"], [4, 8]),
    "edge_ring": ("edge_ring_recession_mm", 0.1, ["rf_bias_", "oes_"], [4, 8]),
}


def hardware_truth(source, metadata):
    explicit = source / "truth.private/assessment.json"
    if explicit.exists():
        return json.loads(explicit.read_text())
    hardware = json.loads((source / "truth.private/hardware.json").read_text())
    targets = {r["run_id"]: r for r in metadata.filter(~pl.col("is_control")).iter_rows(named=True)}
    grouped = {}
    for row in hardware:
        if row["run_id"] in targets:
            grouped.setdefault(targets[row["run_id"]]["label"], []).append(row["parameters"])
    labels = sorted(grouped)
    pairs = {}
    for i, a in enumerate(labels):
        for b in labels[i + 1 :]:
            pairs[a + "|" + b] = [
                name
                for name, (column, tolerance, _, _) in MECHANISMS.items()
                if abs(
                    np.mean([r[column] for r in grouped[a]])
                    - np.mean([r[column] for r in grouped[b]])
                )
                > tolerance
            ]
    return {
        "expected_by_pair": pairs,
        "localization": {
            name: {"prefixes": value[2], "steps": value[3]} for name, value in MECHANISMS.items()
        },
    }
