"""FDC-scale species/surface balances, with explicitly illustrative coefficients.

Inventories are dimensionless proxies, not identified chemical concentrations.
The two-reservoir backward-Euler balance conserves generated product minus
pumped product, including reversible adsorption on the chamber surface.
"""

from dataclasses import dataclass, field

import numpy as np

WALL_DESORPTION_PER_S = 0.0008


@dataclass
class Chemistry:
    wall: float = 0.8
    recombination: float = 1.0
    product: float = 0.0
    radical: float = 0.0
    partial_mtorr: np.ndarray = field(default_factory=lambda: np.zeros(9))
    balance_error: float = 0.0

    @property
    def coverage(self) -> float:
        return self.wall / (5.0 + self.wall)

    def transport(self, flows, pressure_mtorr, speed_l_s, source_w, activity, dt):
        residence = 25.0 / speed_l_s
        fraction = flows / max(float(np.sum(flows)), 1e-12)
        target = fraction * pressure_mtorr
        self.partial_mtorr = target + (self.partial_mtorr - target) * np.exp(-dt / residence)
        # Source-driven neutral activation, residence loss, wall recombination.
        generation = activity * (max(source_w, 0) / 800) ** 0.7 * self.partial_mtorr[0] / 12.5 * 4.0
        loss = 1.0 / residence + self.recombination * (0.4 + 0.8 * self.coverage) + 0.15
        equilibrium = generation / loss
        self.radical = equilibrium + (self.radical - equilibrium) * np.exp(-dt * loss)
        return residence

    def products(self, generation_per_s, residence_s, dt):
        ka, kd = 0.08, WALL_DESORPTION_PER_S
        before = self.product + self.wall
        a = 1 + dt * (1 / residence_s + ka)
        b = 1 + dt * kd
        product = (self.product + dt * generation_per_s + dt * kd * self.wall / b) / (
            a - dt * dt * kd * ka / b
        )
        wall = (self.wall + dt * ka * product) / b
        exhausted = dt * product / residence_s
        self.balance_error = max(
            self.balance_error,
            abs(product + wall - before - dt * generation_per_s + exhausted),
        )
        self.product, self.wall = product, wall


def ion_response(source_w, bias_dc_v, pressure_mtorr, product, ring_recession_mm):
    """Independent source-flux and bias-energy proxies; no IEDF/profile solver."""
    flux = (max(source_w, 0) / 800) ** 0.5 * (max(pressure_mtorr, 0.1) / 20) ** 0.15
    flux /= 1 + 0.15 * product
    energy = (20 + 0.45 * abs(bias_dc_v)) * np.exp(-pressure_mtorr / 80)
    radial_energy = energy * np.array([1.0, 1 / (1 + 0.4 * ring_recession_mm)])
    yield_proxy = np.sqrt(np.maximum(radial_energy - 25, 0) / 120)
    return flux, radial_energy, yield_proxy
