"""Exactly 200 named FDC channels: measurements, commands, states and estimates."""

from __future__ import annotations


def sensor_catalog() -> list[dict]:
    rows = []

    def add(prefix, subsystem, specs):
        for name, unit, role, lo, hi in specs:
            rows.append(
                {
                    "name": f"{prefix}_{name}",
                    "subsystem": subsystem,
                    "unit": unit,
                    "role": role,
                    "minimum": lo,
                    "maximum": hi,
                    "description": f"{prefix}: {name}; {role}; {unit}",
                }
            )

    rf = [
        ("power_sp_w", "W", "command", 0, 2000),
        ("forward_w", "W", "measurement", 0, 2000),
        ("reflected_w", "W", "measurement", 0, 2000),
        ("delivered_w", "W", "derived", 0, 2000),
        ("voltage_v_rms", "V", "measurement", 0, 2000),
        ("current_a_rms", "A", "measurement", 0, 50),
        ("phase_deg", "deg", "measurement", -90, 90),
        ("load_r_ohm", "ohm", "estimate", 0.1, 150),
        ("load_x_ohm", "ohm", "estimate", -100, 100),
        ("c1_pf", "pF", "measurement", 50, 500),
        ("c2_pf", "pF", "measurement", 100, 1800),
        ("c1_motor_pct", "%", "actuator", 0, 100),
        ("c2_motor_pct", "%", "actuator", 0, 100),
        ("match_error", "fraction", "derived", 0, 1),
        ("match_motor_current_a", "A", "measurement", 0, 5),
        ("frequency_mhz", "MHz", "command", 1, 100),
        ("inductance_uh", "uH", "estimate", 0.1, 10),
        ("inductor_temp_c", "degC", "measurement", 0, 150),
        ("dc_bias_v", "V", "measurement", -1000, 10),
        ("match_ready", "bool", "state", 0, 1),
    ]
    for r in ["rf_source", "rf_bias"]:
        add(r, "rf", rf)
    zone = [
        ("sp_c", "degC", "command", 0, 150),
        ("temp_c", "degC", "measurement", 0, 150),
        ("power_w", "W", "measurement", 0, 250),
        ("current_a", "A", "measurement", 0, 10),
        ("voltage_v", "V", "measurement", 0, 250),
        ("redundant_c", "degC", "measurement", 0, 150),
    ]
    for i in range(1, 7):
        add(f"heater_z{i}", "thermal", zone)
    add(
        "thermal",
        "thermal",
        [
            ("esc_center_c", "degC", "measurement", 0, 150),
            ("esc_edge_c", "degC", "measurement", 0, 150),
            ("base_c", "degC", "measurement", 0, 100),
            ("coolant_in_c", "degC", "measurement", 0, 100),
            ("coolant_out_c", "degC", "measurement", 0, 100),
            ("coolant_flow_lpm", "L/min", "measurement", 0, 20),
            ("supply_kpa", "kPa", "measurement", 0, 600),
            ("return_kpa", "kPa", "measurement", 0, 600),
            ("heater_bus_v", "V", "measurement", 0, 250),
            ("heater_bus_a", "A", "measurement", 0, 20),
        ],
    )
    from .etch_recipe import GASES

    for gas in GASES:
        add(
            f"gas_{gas}",
            "gas",
            [
                ("sp_sccm", "sccm", "command", 0, 150),
                ("flow_sccm", "sccm", "measurement", 0, 150),
                ("valve_pct", "%", "actuator", 0, 100),
                ("inlet_bar", "bar", "measurement", 0, 5),
                ("line_torr", "Torr", "measurement", 0, 100),
                ("line_c", "degC", "measurement", 0, 80),
            ],
        )
    add(
        "vac",
        "vacuum",
        [
            ("pressure_sp_mtorr", "mTorr", "command", 0, 500),
            ("pressure_mtorr", "mTorr", "measurement", 0, 500),
            ("redundant_mtorr", "mTorr", "measurement", 0, 500),
            ("pcv_sp_pct", "%", "command", 0, 100),
            ("pcv_pct", "%", "actuator", 0, 100),
            ("pcv_error_pct", "%", "derived", -100, 100),
            ("pcv_current_a", "A", "measurement", 0, 5),
            ("pump_rpm", "rpm", "measurement", 0, 80000),
            ("pump_current_a", "A", "measurement", 0, 20),
            ("foreline_mtorr", "mTorr", "measurement", 0, 5000),
            ("turbo_c", "degC", "measurement", 0, 100),
            ("foreline_c", "degC", "measurement", 0, 100),
            ("gate_open", "bool", "state", 0, 1),
            ("slit_open", "bool", "state", 0, 1),
            ("roughing_open", "bool", "state", 0, 1),
            ("exhaust_sccm", "sccm", "estimate", 0, 200),
            ("pump_speed_est_l_s", "L/s", "estimate", 0, 250),
            ("pressure_error_mtorr", "mTorr", "derived", -500, 500),
            ("residence_est_s", "s", "estimate", 0, 60),
            ("valve_body_c", "degC", "measurement", 0, 150),
        ],
    )
    for zone_name in ["center", "edge"]:
        add(
            "he_" + zone_name,
            "backside_helium",
            [
                ("pressure_sp_torr", "Torr", "command", 0, 20),
                ("pressure_torr", "Torr", "measurement", 0, 20),
                ("redundant_torr", "Torr", "measurement", 0, 20),
                ("inlet_sccm", "sccm", "measurement", 0, 5),
                ("flow_demand_sccm", "sccm", "command", 0, 5),
                ("valve_pct", "%", "actuator", 0, 100),
                ("error_torr", "Torr", "derived", -20, 20),
                ("supply_bar", "bar", "measurement", 0, 5),
                ("line_c", "degC", "measurement", 0, 100),
                ("regulator_torr", "Torr", "measurement", 0, 25),
            ],
        )
    add(
        "esc",
        "backside_helium",
        [
            ("voltage_v", "V", "measurement", 0, 2000),
            ("current_ua", "uA", "measurement", 0, 500),
            ("clamp_force_est_n", "N", "estimate", 0, 1000),
            ("wafer_present", "bool", "state", 0, 1),
        ],
    )
    add(
        "robot",
        "robot",
        [
            ("radial_mm", "mm", "measurement", 0, 500),
            ("rotation_deg", "deg", "measurement", -180, 180),
            ("z_mm", "mm", "measurement", 0, 100),
            ("velocity_mm_s", "mm/s", "estimate", -500, 500),
            ("grip_force_n", "N", "measurement", 0, 20),
            ("motor_current_a", "A", "measurement", 0, 10),
            ("blade_c", "degC", "measurement", 0, 150),
            ("lift_pin_mm", "mm", "measurement", 0, 20),
            ("slit_position_pct", "%", "actuator", 0, 100),
            ("wafer_on_blade", "bool", "state", 0, 1),
            ("in_chamber", "bool", "state", 0, 1),
            ("transfer_dp_mtorr", "mTorr", "measurement", -500, 500),
            ("vibration_mm_s", "mm/s", "measurement", 0, 10),
            ("clamp_release_cmd", "bool", "command", 0, 1),
        ],
    )
    add(
        "oes",
        "optical",
        [
            ("f_703", "a.u.", "measurement", 0, 5000),
            ("ar_750", "a.u.", "measurement", 0, 5000),
            ("o_777", "a.u.", "measurement", 0, 5000),
            ("sf_fragment", "a.u.", "measurement", 0, 5000),
            ("sif_product", "a.u.", "measurement", 0, 5000),
            ("cl_line", "a.u.", "measurement", 0, 5000),
            ("endpoint_ratio", "ratio", "derived", 0, 20),
            ("total_light", "a.u.", "measurement", 0, 10000),
        ],
    )
    assert len(rows) == 200 and len({r["name"] for r in rows}) == 200
    descriptions = {
        "rf_source_voltage_v_rms": "RMS voltage at source matching-network input; not wafer-surface voltage",
        "rf_bias_voltage_v_rms": "RMS voltage at bias matching-network input; not RF carrier samples",
        "rf_source_c1_pf": "Series variable matching capacitance C1, in pF",
        "rf_source_c2_pf": "Shunt variable matching capacitance C2, in pF",
        "thermal_esc_center_c": "ESC center-region sensor proxy; does not directly measure wafer temperature",
        "thermal_esc_edge_c": "ESC edge-region sensor proxy; does not directly measure wafer temperature",
        "vac_pump_speed_est_l_s": "Nominal pumping-speed estimate from PCV opening; not a direct conductance measurement",
        "he_edge_inlet_sccm": "Helium supply flow readback; not a direct seal-leak measurement",
        "robot_grip_force_n": "Assumed mechanical edge-grip end-effector force; no vacuum grip is assumed",
        "rf_source_dc_bias_v": "Nominally zero source-side DC channel; useful as an inactive-channel control",
    }
    for row in rows:
        row["description"] = descriptions.get(row["name"], row["description"])
        if row["role"] == "command":
            row["command_scope"] = "recipe"
        if row["name"] == "vac_pcv_sp_pct" or row["name"].endswith("_flow_demand_sccm"):
            row["role"] = "controller_output"
            row["command_scope"] = "feedback"
        if row["name"].startswith("he_") and row["name"].endswith("_pressure_sp_torr"):
            row["command_scope"] = "interlocked_application"
        if row["name"] in ["rf_source_power_sp_w", "rf_bias_power_sp_w"]:
            row["description"] = "Fixed requested recipe RF power; actual output can be interlocked"
    return rows
