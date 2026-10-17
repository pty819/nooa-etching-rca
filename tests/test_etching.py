import numpy as np
import polars as pl
import pytest

from factor_demo.etch_chemistry import Chemistry, ion_response
from factor_demo.etch_dataset import generate_etching
from factor_demo.etch_features import extract_features
from factor_demo.etch_model import simulate_run
from factor_demo.etch_recipe import STEPS, Hardware, make_schedule
from factor_demo.etch_sensors import sensor_catalog
from factor_demo.etch_validation import validate_etching


def test_actual_traces_and_boundaries_are_reproducible():
    a = simulate_run(Hardware(), 1234, dirty=False)
    b = simulate_run(Hardware(), 1234, dirty=False)
    assert a.trace.equals(b.trace)
    assert a.trace.width == 202
    assert a.steps["step_name"].to_list() == [s.name for s in STEPS]
    assert a.steps["step_id"].to_list() == list(range(1, 12))
    assert a.steps["end_s"][-1] == a.trace["time_s"][-1] + 0.5
    assert set(a.trace.columns) - {"time_s", "step_id"} == {s["name"] for s in sensor_catalog()}
    assert not any("wafer_center_c" in name or "contact_factor" in name for name in a.trace.columns)


def test_fixed_recipe_is_independent_of_fault_and_seed():
    good = simulate_run(Hardware(), 1)
    bad = simulate_run(Hardware(edge_leak_sccm_per_torr=0.38, edge_contact_factor=0.42), 999)
    commands = [s["name"] for s in sensor_catalog() if s.get("command_scope") == "recipe"]
    assert good.steps.equals(bad.steps)
    assert good.trace.select("time_s", "step_id", *commands).equals(
        bad.trace.select("time_s", "step_id", *commands)
    )
    assert good.trace.height == 712
    with pytest.raises(ValueError, match="fixed comparison recipe"):
        simulate_run(Hardware(), 1, recipe="recipe_B")
    with pytest.raises(ValueError, match="divide every fixed step"):
        make_schedule(0.3)


def test_product_inventory_is_conservative_and_wall_has_memory():
    chemistry = Chemistry(wall=0)
    for tick in range(200):
        chemistry.products(1.0 if tick < 100 else 0.0, 0.5, 0.5)
        assert chemistry.product >= 0 and chemistry.wall >= 0
    assert chemistry.balance_error < 1e-12
    assert chemistry.wall > chemistry.product > 0  # desorption tail after generation stops
    slow, fast = Chemistry(wall=0), Chemistry(wall=0)
    for _ in range(100):
        slow.products(1, 2, 0.5)
        fast.products(1, 0.25, 0.5)
    assert fast.product < slow.product


def test_flux_energy_and_ring_response_are_distinct():
    f1, e1, y1 = ion_response(800, -150, 20, 0.1, 0)
    f2, e2, _ = ion_response(800, -300, 20, 0.1, 0)
    assert f1 == f2 and np.all(e2 > e1)
    f3, e3, _ = ion_response(1200, -150, 20, 0.1, 0)
    assert f3 > f1 and np.array_equal(e3, e1)
    _, e4, y4 = ion_response(800, -150, 20, 0.1, 0.5)
    assert e4[0] == e1[0] and e4[1] < e1[1] and y4[1] < y1[1]


def test_wall_state_changes_observed_process_without_changing_recipe():
    baseline = simulate_run(Hardware(), 42, dirty=False)
    coated = simulate_run(Hardware(wall_initial_inventory=8), 42, dirty=False)
    assert baseline.steps.equals(coated.steps)
    assert coated.outcomes["depth_mean_nm"] < baseline.outcomes["depth_mean_nm"]
    phase = lambda s: s.trace.filter(pl.col("step_id") == 4)
    assert phase(coated)["oes_sif_product"].mean() < phase(baseline)["oes_sif_product"].mean()
    assert (
        abs(phase(coated)["rf_source_c1_pf"].mean() - phase(baseline)["rf_source_c1_pf"].mean())
        > 0.1
    )


def test_hardware_fault_and_partial_repair_change_heat_and_outcomes():
    good = simulate_run(Hardware(), 42, dirty=False)
    bad = simulate_run(
        Hardware(edge_leak_sccm_per_torr=0.38, edge_contact_factor=0.42), 42, dirty=False
    )
    repaired = simulate_run(
        Hardware(edge_leak_sccm_per_torr=0.018, edge_contact_factor=0.72), 42, dirty=False
    )
    g, b, r = [x.outcomes["center_edge_nonuniformity_pct"] for x in [good, bad, repaired]]
    assert g < r < b and b - g > 5
    steady = lambda s: s.trace.filter(pl.col("step_id") == 8)
    assert (
        steady(bad)["he_edge_pressure_torr"].mean()
        < steady(good)["he_edge_pressure_torr"].mean() - 1
    )
    assert (
        abs(
            steady(repaired)["he_edge_pressure_torr"].mean()
            - steady(good)["he_edge_pressure_torr"].mean()
        )
        < 0.2
    )
    assert steady(bad)["he_edge_inlet_sccm"].mean() > steady(good)["he_edge_inlet_sccm"].mean() + 1


def test_feature_statistics_are_computed_from_same_observed_samples():
    s = simulate_run(Hardware(), 9876, dirty=True)
    features = extract_features(s.trace, s.steps, sensor_catalog())
    assert len(features) == 22000
    column = "rf_source_forward_w"
    raw = s.trace.filter(pl.col("step_id") == 4)[column].to_numpy()
    prefix = column + "__step_04__"
    assert features[prefix + "mean"] == pytest.approx(np.nanmean(raw), rel=1e-6)
    assert features[prefix + "std"] == pytest.approx(np.nanstd(raw), rel=1e-6)
    for key, value in features.items():
        if key.endswith("__std") and value is not None:
            assert value >= 0
        if key.endswith("__min"):
            base = key[:-3]
            assert value <= features[base + "mean"] <= features[base + "max"]


@pytest.fixture(scope="module")
def etch_data(tmp_path_factory):
    path = tmp_path_factory.mktemp("etch") / "case"
    generate_etching(path, runs=45, seed=8)
    return path


def test_saved_dataset_invariants_splits_and_recomputation(etch_data):
    result = validate_etching(etch_data)
    assert result["passed"] and result["sensor_channels"] == 200
    assert result["target_runs"] == 45 and result["raw_runs"] == 54
    assert result["feature_count"] == 22000
    assert result["fixed_recipe_verified"] and result["recipe_count"] == 1
    assert result["feature_runs_fully_recomputed"] == 3
    meta = pl.read_parquet(etch_data / "runs.parquet")
    control = meta.filter(pl.col("is_control"))
    assert set(control["label"]) == {"label1"}
    target = meta.filter(~pl.col("is_control")).sort("timestamp")
    assert target["label"].to_list() == sorted(target["label"].to_list())
    # Maintenance creates a six-hour gap rather than shuffling period labels.
    before = target.filter(pl.col("period_id") == 2)["timestamp"].max()
    after = target.filter(pl.col("period_id") == 3)["timestamp"].min()
    assert (after - before).total_seconds() > 6 * 3600


def test_raw_trace_tampering_is_detected(etch_data):
    path = etch_data / "traces" / "run_A_00000.parquet"
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"corruption")
        with pytest.raises(ValueError, match="trace hash"):
            validate_etching(etch_data, recompute=0)
    finally:
        path.write_bytes(original)
