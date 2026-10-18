"""Coupled reduced-order ICP etch dynamics and FDC-rate telemetry.

This is not a plasma chemistry or equipment digital twin. Mass/heat balances,
an explicit passive RF network, actuator dynamics and transfer interlocks create
coherent observable traces. Numerical parameters are illustrative assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from .etch_chemistry import Chemistry, ion_response
from .etch_recipe import GASES, RECIPE_ID, STEPS, ZONE_OFFSETS_C, Hardware, make_schedule
from .etch_sensors import sensor_catalog


def lag(old, target, dt, tau):
    return target + (old - target) * np.exp(-dt / tau)


def clip(x, lo, hi):
    return min(hi, max(lo, x))


def smooth(x):
    x = clip(x, 0, 1)
    return x * x * (3 - 2 * x)


class RF:
    def __init__(self, bias=False):
        self.bias = bias
        self.forward = 0.0
        self.c1, self.c2 = 145.0, 650.0
        self.temperature = 25.0

    def update(self, setpoint, pressure, temperature, activity, dt, composition=0.0):
        frequency = 13.56e6
        omega = 2 * np.pi * frequency
        inductance = (1.6 if self.bias else 1.5) * (1 + 8e-5 * (self.temperature - 25))
        r_lit = (9 if self.bias else 6) + 0.045 * pressure + 0.007 * (temperature - 60)
        r_lit *= 1 + 0.08 * composition
        resistance = (1 - activity) * 85 + activity * max(2, r_lit)
        reactance = (-34 if self.bias else -25) + 0.015 * (temperature - 60) + 2 * composition
        # Series C1 + fixed L + plasma, with shunt C2 at the input.
        r_target = clip(resistance, 2, 45)
        x_target = np.sqrt(r_target * (50 - r_target))
        c1_target = 1e12 / (omega * (omega * inductance * 1e-6 + reactance - x_target))
        c2_target = 1e12 * x_target / (omega * 50 * r_target)
        old1, old2 = self.c1, self.c2
        if setpoint > 0:
            self.c1 = float(lag(self.c1, clip(c1_target, 55, 490), dt, 0.9))
            self.c2 = float(lag(self.c2, clip(c2_target, 110, 1700), dt, 1.2))
        self.forward = float(lag(self.forward, setpoint, dt, 0.35))
        if self.forward < 0.01:
            self.forward = 0.0
        z_series = complex(
            resistance, reactance + omega * inductance * 1e-6 - 1 / (omega * self.c1 * 1e-12)
        )
        z_in = 1 / (1 / z_series + complex(0, omega * self.c2 * 1e-12))
        gamma2 = clip(abs((z_in - 50) / (z_in + 50)) ** 2, 0, 1)
        reflected = self.forward * gamma2
        delivered = self.forward - reflected
        current = np.sqrt(delivered / max(z_in.real, 0.1))
        voltage = current * abs(z_in)
        phase = np.degrees(np.angle(z_in))
        self.temperature += dt * (0.014 * self.forward - 1.8 * (self.temperature - 25)) / 25
        return {
            "power_sp_w": setpoint,
            "forward_w": self.forward,
            "reflected_w": reflected,
            "delivered_w": delivered,
            "voltage_v_rms": voltage,
            "current_a_rms": current,
            "phase_deg": phase if self.forward else 0,
            "load_r_ohm": resistance,
            "load_x_ohm": reactance,
            "c1_pf": self.c1,
            "c2_pf": self.c2,
            "c1_motor_pct": (self.c1 - 50) / 4.5,
            "c2_motor_pct": (self.c2 - 100) / 17,
            "match_error": np.sqrt(gamma2) if self.forward else 0,
            "match_motor_current_a": 0.05
            + 0.003 * (abs(self.c1 - old1) + abs(self.c2 - old2)) / dt,
            "frequency_mhz": frequency / 1e6,
            "inductance_uh": inductance,
            "inductor_temp_c": self.temperature,
            "dc_bias_v": -15 * np.sqrt(delivered) if self.bias else 0,
            "match_ready": float(self.forward > 1 and gamma2 < 0.03),
        }


@dataclass
class Simulation:
    trace: pl.DataFrame
    steps: pl.DataFrame
    latent: pl.DataFrame
    outcomes: dict


def simulate_run(
    hardware: Hardware, seed: int, recipe: str = RECIPE_ID, dt: float = 0.5, dirty: bool = True
) -> Simulation:
    if not 0.1 <= dt <= 1.0:
        raise ValueError("Telemetry sample period must be between 0.1 and 1 second")
    rng = np.random.default_rng(seed)
    schedule = make_schedule(dt, recipe)
    catalog = sensor_catalog()
    names = [r["name"] for r in catalog]
    count = sum(s["samples"] for s in schedule)
    values = np.zeros((count, 200), dtype=np.float64)
    times = np.arange(count, dtype=float) * dt
    step_ids = np.empty(count, dtype=np.int16)
    latent = np.empty((count, 16), dtype=float)
    chemistry = Chemistry(hardware.wall_initial_inventory, hardware.wall_recombination_factor)
    gas = np.zeros(8)
    gas_line = np.zeros(8)
    gas_gains = hardware.gas_gain * rng.uniform(0.99, 1.01, 8)
    zone_t = np.full(6, 35.0) + rng.normal(0, 0.1, 6)
    heater_power, heater_i = np.full(6, 15.0), np.full(6, 15.0)
    zone_offsets = np.array(ZONE_OFFSETS_C)
    base_t, coolant_in, coolant_out = 25.0, 20.0, 21.0
    wafer_t = np.full(2, 35.0)
    depth = np.zeros(2)
    pressure = 1.0  # mTorr
    pcv = 0.65
    pressure_integral = 0.0
    he_p, he_flow, he_i = np.zeros(2), np.zeros(2), np.zeros(2)
    esc_v, plasma = 0.0, 0.0
    rf_source, rf_bias = RF(), RF(bias=True)
    old_radial, blade_t = 0.0, hardware.ambient_c
    row_index = 0
    for si, timing in enumerate(schedule):
        step = STEPS[si]
        for tick in range(timing["samples"]):
            u = tick / timing["samples"]
            is_prepare = step.name == "prepare"
            handoff = step.name == "release_handoff"
            exiting = step.name == "exit"
            cooling = step.name.startswith("cool")
            wafer_present = not is_prepare or u >= 0.30
            if exiting and u > 0.55:
                wafer_present = False
            releasing = handoff or exiting
            he_safe = max(he_p) < 0.3
            rf_safe = rf_source.forward + rf_bias.forward < 0.1
            clamp_wanted = (
                wafer_present
                and (not is_prepare or u >= 0.40)
                and not (releasing and he_safe and rf_safe)
            )
            esc_v = float(lag(esc_v, 1200.0 if clamp_wanted else 0.0, dt, 0.25))
            transfer_safe = he_safe and rf_safe and esc_v < 50 and pressure < 25
            if is_prepare:
                radial = 300 * (smooth(u / 0.2) if u < 0.2 else 1 - smooth((u - 0.4) / 0.3))
                on_blade = u < 0.32
                slit = u < 0.75
                pins = 5.0 if 0.20 < u < 0.38 else 0.0
            elif handoff:
                radial = 300 * smooth((u - 0.35) / 0.45) if transfer_safe else 0.0
                on_blade = transfer_safe and u > 0.82
                slit = transfer_safe
                pins = 5.0 if transfer_safe else 0.0
            elif exiting:
                radial = 300 * (1 - smooth(u / 0.70)) if transfer_safe else 0.0
                on_blade = transfer_safe and u < 0.92
                slit = transfer_safe and u < 0.78
                pins = 0.0
            else:
                radial, on_blade, slit, pins = 0.0, False, False, 0.0
            robot_in = radial > 20
            he_sp = 10.0 if wafer_present and esc_v > 900 and not slit and not releasing else 0.0
            leak_coeff = np.array([0.012, hardware.edge_leak_sccm_per_torr])
            he_error = he_sp - he_p
            he_raw = 0.45 * he_error + he_i + 0.01 * he_sp
            he_free = ((he_raw < 3) | (he_error < 0)) & ((he_raw > 0) | (he_error > 0))
            he_i = np.clip(he_i + 0.04 * he_error * dt * he_free, 0, 3)
            he_demand = np.clip(0.45 * he_error + he_i + 0.01 * he_sp, 0, 3)
            if he_sp == 0:
                he_demand[:] = 0
                he_i[:] = 0
            he_flow = lag(he_flow, he_demand, dt, 0.7)
            if he_sp > 0:
                # V*dP/dt = 0.0126667*(Qin - conductance*P), Torr L/s.
                equilibrium = he_flow / leak_coeff
                he_p = equilibrium + (he_p - equilibrium) * np.exp(
                    -0.0126667 * leak_coeff * dt / 0.02
                )
            else:
                he_p = lag(he_p, np.zeros(2), dt, 0.65)
            he_p = np.clip(he_p, 0, 19.5)
            he_leak = he_p * leak_coeff
            flow_sp = np.array(
                [step.sf6_sccm, step.ar_sccm, step.o2_sccm, step.n2_sccm, 0.0, 0.0, 0.0, 0.0]
            )
            gas = lag(gas, flow_sp * gas_gains, dt, np.linspace(0.65, 1.3, 8))
            gas_line = lag(gas_line, gas, dt, 1.8)
            total_flow = float(gas_line.sum() + he_leak.sum())
            throughput = 0.0126667 * total_flow
            p_sp = step.pressure_mtorr
            pressure_integral = clip(pressure_integral + (pressure - p_sp) * dt, -100, 100)
            speed_ff = throughput / max(p_sp / 1000, 0.0001)
            opening_ff = (clip((speed_ff - 0.3) / 200, 0, 1)) ** (1 / 1.5)
            opening_sp = clip(
                opening_ff + 0.005 * (pressure - p_sp) + 0.0008 * pressure_integral, 0.025, 1
            )
            pcv_old = pcv
            pcv = float(lag(pcv, opening_sp, dt, 0.8))
            valve_speed = 0.3 + 200 * pcv**1.5
            path_conductance = 400 * hardware.pump_conductance_factor
            speed_nominal = 1 / (1 / valve_speed + 1 / 400)
            speed = 1 / (1 / valve_speed + 1 / path_conductance)
            p_equilibrium = max(0.05, 1000 * throughput / speed)
            pressure = float(lag(pressure, p_equilibrium, dt, 25 / speed))
            process_allowed = (
                wafer_present
                and esc_v > 900
                and not slit
                and not robot_in
                and pressure < 80
                and min(he_p) > 2
            )
            source_sp = step.source_w if process_allowed else 0.0
            bias_sp = step.bias_w if process_allowed and plasma > 0.6 else 0.0
            plasma = float(lag(plasma, 1.0 if source_sp > 100 else 0.0, dt, 0.6))
            composition = chemistry.product + chemistry.coverage
            source = rf_source.update(source_sp, pressure, wafer_t.mean(), plasma, dt, composition)
            bias = rf_bias.update(bias_sp, pressure, wafer_t.mean(), plasma, dt, composition)
            # Requested recipe values remain fixed even while interlocks delay ignition.
            source["power_sp_w"], bias["power_sp_w"] = step.source_w, step.bias_w
            residence = chemistry.transport(
                np.r_[gas_line, he_leak.sum()],
                pressure,
                speed,
                source["delivered_w"],
                plasma,
                dt,
            )
            ion_flux, ion_energy, ion_yield = ion_response(
                source["delivered_w"],
                bias["dc_bias_v"],
                pressure,
                chemistry.product,
                hardware.edge_ring_recession_mm,
            )
            absorbed = 0.035 * source["delivered_w"] + 0.65 * bias["delivered_w"]
            # Gas-gap/contact heat transfer, with the actual wafer temperatures unobserved.
            contact = np.array(
                [
                    2 + 5 * he_p[0] / (he_p[0] + 4),
                    hardware.edge_contact_factor * (2 + 5 * he_p[1] / (he_p[1] + 4)),
                ]
            )
            esc_surface = np.array([zone_t[:3].mean(), zone_t[3:].mean()])
            contact_active = wafer_present and not on_blade and pins < 0.1
            heat_to_esc = contact * (wafer_t - esc_surface) if contact_active else np.zeros(2)
            radial_heat = 0.8 * (wafer_t[1] - wafer_t[0])
            wafer_t += (
                dt
                * (
                    absorbed * np.array([0.5, 0.5])
                    - heat_to_esc
                    + np.array([radial_heat, -radial_heat])
                    - 0.03 * (wafer_t - hardware.ambient_c)
                )
                / np.array([48.0, 40.0])
            )
            setpoints = np.full(6, step.temperature_c) + zone_offsets
            error = setpoints - zone_t
            raw_power = 35 * error + heater_i
            free = ((raw_power < 220) | (error < 0)) & ((raw_power > 0) | (error > 0))
            heater_i = np.clip(heater_i + 0.5 * error * dt * free, 0, 220)
            desired_power = np.clip(35 * error + heater_i, 0, 220)
            heater_power = lag(heater_power, desired_power, dt, 0.5)
            coolant_in = float(lag(coolant_in, 18.0 if cooling else 20.0, dt, 3))
            flow_lpm = 9.0 if cooling else 6.0
            zone_base_g = 4.0 if cooling else 1.2
            heat_to_base = zone_base_g * (zone_t - base_t)
            wafer_back = np.repeat(heat_to_esc / 3, 3)
            coupling = 0.4 * (np.roll(zone_t, 1) + np.roll(zone_t, -1) - 2 * zone_t)
            zone_t += (
                dt
                * (hardware.thermal_gain * heater_power + wafer_back - heat_to_base + coupling)
                / 180
            )
            coolant_heat = 45 * (base_t - coolant_in)
            base_t += dt * (heat_to_base.sum() - coolant_heat) / 2500
            coolant_out = coolant_in + max(0, coolant_heat) / (flow_lpm / 60 * 4180)
            rate = np.zeros(2)
            if step.source_w > 0 and plasma > 0.8:
                # Neutral supply, ion flux, ion-assisted yield and surface inhibition.
                rate = 8.0 * chemistry.radical * ion_flux * ion_yield
                rate /= (1 + 0.5 * chemistry.product) * (1 + 0.3 * chemistry.coverage)
                rate = rate * np.exp(np.clip(0.012 * (wafer_t - 70), -0.5, 0.7))
                depth += rate * dt
            chemistry.products(float(rate.mean()) / 8 * 0.6, residence, dt)
            row = {}
            for prefix, block in [("rf_source", source), ("rf_bias", bias)]:
                row.update({prefix + "_" + k: float(v) for k, v in block.items()})
            for z in range(6):
                power = heater_power[z]
                row.update(
                    {
                        f"heater_z{z + 1}_sp_c": setpoints[z],
                        f"heater_z{z + 1}_temp_c": zone_t[z],
                        f"heater_z{z + 1}_power_w": power,
                        f"heater_z{z + 1}_current_a": np.sqrt(power / 200),
                        f"heater_z{z + 1}_voltage_v": np.sqrt(power * 200),
                        f"heater_z{z + 1}_redundant_c": zone_t[z]
                        + (hardware.calibration_offset_c if z == 2 else 0),
                    }
                )
            row.update(
                {
                    "thermal_esc_center_c": zone_t[:3].mean(),
                    "thermal_esc_edge_c": zone_t[3:].mean(),
                    "thermal_base_c": base_t,
                    "thermal_coolant_in_c": coolant_in,
                    "thermal_coolant_out_c": coolant_out,
                    "thermal_coolant_flow_lpm": flow_lpm,
                    "thermal_supply_kpa": 240 + flow_lpm * 3,
                    "thermal_return_kpa": 210,
                    "thermal_heater_bus_v": 230.0,
                    "thermal_heater_bus_a": heater_power.sum() / 230,
                }
            )
            for gi, gas_name in enumerate(GASES):
                prefix = "gas_" + gas_name
                row.update(
                    {
                        prefix + "_sp_sccm": flow_sp[gi],
                        prefix + "_flow_sccm": gas[gi],
                        prefix + "_valve_pct": clip(gas[gi] / 150 * 100, 0, 100),
                        prefix + "_inlet_bar": 2.2 - 0.002 * gas[gi],
                        prefix + "_line_torr": pressure / 1000 + 0.04 * gas_line[gi],
                        prefix + "_line_c": hardware.ambient_c + 2 + gi * 0.15,
                    }
                )
            row.update(
                {
                    "vac_pressure_sp_mtorr": p_sp,
                    "vac_pressure_mtorr": pressure,
                    "vac_redundant_mtorr": pressure * 1.003,
                    "vac_pcv_sp_pct": 100 * opening_sp,
                    "vac_pcv_pct": 100 * pcv,
                    "vac_pcv_error_pct": 100 * (opening_sp - pcv),
                    "vac_pcv_current_a": 0.15 + abs(pcv - pcv_old) / dt * 2,
                    "vac_pump_rpm": 60000.0,
                    "vac_pump_current_a": 3 + 0.01 * total_flow,
                    "vac_foreline_mtorr": 30 + 1000 * throughput / 8,
                    "vac_turbo_c": 40 + 0.01 * total_flow,
                    "vac_foreline_c": 35 + 0.02 * total_flow,
                    "vac_gate_open": 1.0,
                    "vac_slit_open": float(slit),
                    "vac_roughing_open": 0.0,
                    "vac_exhaust_sccm": total_flow,
                    "vac_pump_speed_est_l_s": speed_nominal,
                    "vac_pressure_error_mtorr": pressure - p_sp,
                    "vac_residence_est_s": 25 / speed_nominal,
                    "vac_valve_body_c": 55 + 0.005 * total_flow,
                }
            )
            for hi, he_zone in enumerate(["center", "edge"]):
                prefix = "he_" + he_zone
                row.update(
                    {
                        prefix + "_pressure_sp_torr": he_sp,
                        prefix + "_pressure_torr": he_p[hi],
                        prefix + "_redundant_torr": he_p[hi] * 1.002,
                        prefix + "_inlet_sccm": he_flow[hi],
                        prefix + "_flow_demand_sccm": he_demand[hi],
                        prefix + "_valve_pct": he_flow[hi] / 3 * 100,
                        prefix + "_error_torr": he_sp - he_p[hi],
                        prefix + "_supply_bar": 2.1 - 0.02 * he_flow[hi],
                        prefix + "_line_c": hardware.ambient_c + 4,
                        prefix + "_regulator_torr": 18 - 0.2 * he_flow[hi],
                    }
                )
            row.update(
                {
                    "esc_voltage_v": esc_v,
                    "esc_current_ua": 2 + 0.02 * abs(1200 * clamp_wanted - esc_v),
                    "esc_clamp_force_est_n": 0.0002 * esc_v**2,
                    "esc_wafer_present": float(wafer_present),
                }
            )
            velocity = (radial - old_radial) / dt
            old_radial = radial
            blade_t = float(
                lag(blade_t, wafer_t.mean() if on_blade else hardware.ambient_c, dt, 25)
            )
            row.update(
                {
                    "robot_radial_mm": radial,
                    "robot_rotation_deg": 15 * radial / 300,
                    "robot_z_mm": 10 + pins,
                    "robot_velocity_mm_s": velocity,
                    "robot_grip_force_n": 4.0 if on_blade else 0.0,
                    "robot_motor_current_a": 0.2 + 0.015 * abs(velocity),
                    "robot_blade_c": blade_t,
                    "robot_lift_pin_mm": pins,
                    "robot_slit_position_pct": 100.0 * slit,
                    "robot_wafer_on_blade": float(on_blade),
                    "robot_in_chamber": float(robot_in),
                    "robot_transfer_dp_mtorr": 0.1 * pressure if slit else 0.0,
                    "robot_vibration_mm_s": 0.01 + 0.002 * abs(velocity),
                    "robot_clamp_release_cmd": float(releasing),
                }
            )
            ar_light = plasma * source["delivered_w"] * 0.7 * gas_line[1] / 30
            # Excitation depends on plasma; the inventory persists after RF off.
            product = plasma * chemistry.product * source["delivered_w"] * 0.6
            row.update(
                {
                    "oes_f_703": plasma * source["delivered_w"] * 0.65 * chemistry.radical,
                    "oes_ar_750": ar_light,
                    "oes_o_777": plasma * source["delivered_w"] * 0.18 * gas_line[2] / 6,
                    "oes_sf_fragment": plasma * source["delivered_w"] * 0.4 * gas_line[0] / 60,
                    "oes_sif_product": product,
                    "oes_cl_line": 0.0,
                    "oes_endpoint_ratio": product / max(ar_light, 1),
                    "oes_total_light": plasma * source["delivered_w"] * 1.4,
                }
            )
            if row_index == 0:
                assert set(row) == set(names), (set(names) - set(row), set(row) - set(names))
            values[row_index] = [row[n] for n in names]
            step_ids[row_index] = si + 1
            latent[row_index] = [
                *wafer_t,
                *contact,
                he_leak[1],
                *depth,
                chemistry.radical,
                chemistry.product,
                chemistry.wall,
                chemistry.coverage,
                residence,
                ion_flux,
                *ion_energy,
                chemistry.balance_error,
            ]
            row_index += 1
    # Temporally correlated measurement noise, sampled per channel; statistics
    # subsequently share these physical traces. No cross-channel noise covariance.
    floors = {
        "W": 0.08,
        "V": 0.02,
        "A": 0.0005,
        "degC": 0.015,
        "mTorr": 0.005,
        "Torr": 0.002,
        "sccm": 0.003,
        "pF": 0.02,
        "deg": 0.015,
        "bar": 0.0004,
        "kPa": 0.02,
        "rpm": 0.5,
        "uA": 0.005,
        "mm": 0.003,
        "N": 0.001,
        "mm/s": 0.0004,
        "a.u.": 0.05,
    }
    for ci, channel in enumerate(catalog):
        if channel["role"] != "measurement":
            continue
        noise = rng.normal(size=count)
        for ti in range(1, count):
            noise[ti] = 0.55 * noise[ti - 1] + 0.835 * noise[ti]
        sigma = floors.get(channel["unit"], 0.002) + 0.0008 * np.abs(values[:, ci])
        # Preserve exact zero for flow/power channels when their physical value is zero.
        zero_mask = values[:, ci] == 0
        values[:, ci] += noise * sigma
        if channel["name"].endswith(
            (
                "flow_sccm",
                "forward_w",
                "reflected_w",
                "voltage_v_rms",
                "current_a_rms",
                "dc_bias_v",
                "grip_force_n",
                "radial_mm",
                "lift_pin_mm",
            )
        ):
            values[zero_mask, ci] = 0
        values[:, ci] = np.clip(values[:, ci], channel["minimum"], channel["maximum"])
        if dirty:
            values[rng.random(count) < 0.0008, ci] = np.nan
    # Preserve the accounting identity of reported net RF power after measurement noise.
    for prefix in ["rf_source", "rf_bias"]:
        forward = values[:, names.index(prefix + "_forward_w")]
        reflected = values[:, names.index(prefix + "_reflected_w")]
        reflected[:] = np.minimum(reflected, forward)
        values[:, names.index(prefix + "_delivered_w")] = forward - reflected
    if dirty and seed % 5 == 0:
        ci = names.index("vac_redundant_mtorr")
        start = count // 3
        values[start : start + max(3, count // 30), ci] = np.nan
    if dirty and seed % 7 == 0:
        ci = names.index("gas_cl2_inlet_bar")
        values[count // 2 :, ci] = values[count // 2, ci]  # independent stuck transducer
    trace = (
        pl.DataFrame(values.astype(np.float32), schema=names, orient="row")
        .with_columns(pl.Series("time_s", times), pl.Series("step_id", step_ids))
        .select("time_s", "step_id", *names)
    )
    latent_frame = pl.DataFrame(
        latent,
        schema=[
            "wafer_center_c",
            "wafer_edge_c",
            "center_contact_w_k",
            "edge_contact_w_k",
            "edge_helium_leak_sccm",
            "depth_center_nm",
            "depth_edge_nm",
            "neutral_radical_proxy",
            "gas_product_inventory_proxy",
            "wall_product_inventory_proxy",
            "wall_coverage_proxy",
            "actual_residence_s",
            "ion_flux_proxy",
            "ion_energy_center_ev_proxy",
            "ion_energy_edge_ev_proxy",
            "product_balance_max_error",
        ],
        orient="row",
    ).with_columns(pl.Series("time_s", times))
    outcomes = {
        "depth_mean_nm": float(depth.mean() + rng.normal(0, 2)),
        "center_edge_nonuniformity_pct": float(
            100 * abs(depth[1] - depth[0]) / max(depth.mean(), 1)
        ),
        "center_depth_nm": float(depth[0]),
        "edge_depth_nm": float(depth[1]),
    }
    return Simulation(trace, pl.DataFrame(schedule), latent_frame, outcomes)
