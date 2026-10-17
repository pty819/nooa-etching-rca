"""Illustrative two-stage ICP silicon etch recipe, not a production recipe."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class Step:
    name: str
    seconds: float
    temperature_c: float
    pressure_mtorr: float
    source_w: float = 0.0
    bias_w: float = 0.0
    sf6_sccm: float = 0.0
    ar_sccm: float = 0.0
    o2_sccm: float = 0.0
    n2_sccm: float = 0.0


STEPS = [
    Step("prepare", 20, 35, 5, n2_sccm=15),
    Step("heat_1", 45, 60, 20, ar_sccm=30),
    Step("stable_1", 20, 60, 20, sf6_sccm=60, ar_sccm=30, o2_sccm=6),
    Step("etch_1", 55, 60, 20, 800, 130, 60, 30, 6),
    Step("cool_1", 30, 40, 10, ar_sccm=25),
    Step("heat_2", 40, 80, 16, ar_sccm=30),
    Step("stable_2", 18, 80, 16, sf6_sccm=45, ar_sccm=30, o2_sccm=4),
    Step("etch_2", 65, 80, 16, 950, 160, 45, 30, 4),
    Step("cool_2", 35, 35, 8, ar_sccm=20),
    Step("release_handoff", 16, 35, 0.2),
    Step("exit", 12, 35, 0.2),
]

GASES = ["sf6", "ar", "o2", "n2", "c4f8", "cl2", "bcl3", "he_purge"]
RECIPE_ID = "icp_si_fixed_v2"
ZONE_OFFSETS_C = (-0.25, 0.15, 0.0, 0.2, -0.1, 0.05)


def recipe_document() -> dict:
    """The complete programmed process, separate from feedback controller outputs."""
    return {
        "recipe_id": RECIPE_ID,
        "steps": [asdict(s) for s in STEPS],
        "rf_mode": "continuous_wave",
        "rf_source_frequency_mhz": 13.56,
        "rf_bias_frequency_mhz": 13.56,
        "heater_zone_offsets_c": list(ZONE_OFFSETS_C),
        "backside_he_pressure_torr": 10.0,
        "esc_clamp_voltage_v": 1200.0,
        "coolant_temperature_c": {"cool_steps": 18.0, "other_steps": 20.0},
        "coolant_flow_lpm": {"cool_steps": 9.0, "other_steps": 6.0},
        "sequence_version": "transfer-interlocks-v1",
    }


def recipe_sha256() -> str:
    return hashlib.sha256(
        json.dumps(recipe_document(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def make_schedule(dt: float, recipe: str = RECIPE_ID) -> list[dict]:
    if recipe != RECIPE_ID:
        raise ValueError("Only the fixed comparison recipe is supported")
    result, start = [], 0.0
    for i, s in enumerate(STEPS, 1):
        count = round(s.seconds / dt)
        if not np.isclose(count * dt, s.seconds, rtol=0, atol=1e-9):
            raise ValueError("Sample period must divide every fixed step duration exactly")
        end = start + count * dt
        result.append(
            {
                "step_id": i,
                "step_name": s.name,
                "start_s": start,
                "end_s": end,
                "samples": count,
                "duration_s": count * dt,
                "temperature_sp_c": s.temperature_c,
                "pressure_sp_mtorr": s.pressure_mtorr,
                "source_sp_w": s.source_w,
                "bias_sp_w": s.bias_w,
                "rf_mode": "continuous_wave",
                **{f"{g}_sp_sccm": getattr(s, f"{g}_sccm", 0.0) for g in GASES},
            }
        )
        start = end
    return result


@dataclass(frozen=True)
class Hardware:
    """Unobserved hardware properties; the simulator never receives a label."""

    edge_leak_sccm_per_torr: float = 0.010
    edge_contact_factor: float = 1.0
    pump_conductance_factor: float = 1.0
    gas_gain: float = 1.0
    thermal_gain: float = 1.0
    calibration_offset_c: float = 0.0
    ambient_c: float = 23.0
    wall_initial_inventory: float = 0.8
    wall_recombination_factor: float = 1.0
    edge_ring_recession_mm: float = 0.0


def hardware_for_period(period: int, chamber: int, rng: np.random.Generator) -> Hardware:
    # The chamber-A fault appears in period 2. A seal repair removes the leak,
    # while imperfect thermal contact remains in period 3. Chamber B is a control.
    leak, contact = 0.010, 1.0
    if chamber == 0 and period == 2:
        leak, contact = 0.38, 0.42
    elif chamber == 0 and period == 3:
        leak, contact = 0.018, 0.72
    return Hardware(
        edge_leak_sccm_per_torr=leak * rng.uniform(0.95, 1.05),
        edge_contact_factor=contact * rng.uniform(0.97, 1.03),
        pump_conductance_factor=(1.0 if chamber == 0 else 0.97) * rng.uniform(0.99, 1.01),
        gas_gain=(1.0 if chamber == 0 else 1.015) * rng.uniform(0.995, 1.005),
        thermal_gain=(1.0 if chamber == 0 else 0.985),
        # Independent instrumentation/facility event coincides with a case period.
        calibration_offset_c=0.0 if period == 1 else 0.7,
        ambient_c=23 + (1.2 if period > 1 else 0.0) + rng.normal(0, 0.15),
    )
