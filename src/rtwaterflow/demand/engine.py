"""Demand engine: per-consumer profile arrays for the whole horizon.

``build_demand_profiles(inputs, steps_per_day)`` returns
``mdot[n_consumers, steps_per_day · n_days]`` in kg/s:

* consumers WITH archetype ``size`` data → engine profiles: hourly class
  shape (linearly interpolated to the tick raster, wrap-around midnight)
  × day factor (day type × season × temperature coupling) + the hot-dry
  irrigation surge for residential classes × seeded noise;
* consumers WITHOUT ``size`` → the legacy path, bit-identical to M2:
  base × environment.demand_factor staircase (or constant when the bundle
  carries no factor — the M0 behavior).

Determinism (binding): noise is seeded per consumer from ``crc32(name)``
— stable across processes and Python versions (``hash()`` is salted), so
generators stay byte-stable and replays deterministic.

Weather/scenario overrides (``EnvironmentState``): a temperature offset
and a dryness override, applied on top of the bundle's environment —
the ``POST /environment`` knob and the M5+ scenario actions feed these.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np

from ..net_inputs import NetInputs
from .archetypes import (
    IRRIGATION_DRYNESS,
    IRRIGATION_SURGE,
    IRRIGATION_T_MAX_C,
    POOL_SEASON,
    params_for,
)

#: multiplicative per-tick noise sigma (lognormal-ish, clipped)
NOISE_SIGMA = 0.05


@dataclass
class EnvironmentState:
    """Runtime environment overrides (config; scenario-saved)."""

    t_offset_c: float = 0.0
    dryness_override: float | None = None

    def as_dict(self) -> dict:
        return {"t_offset_c": self.t_offset_c,
                "dryness_override": self.dryness_override}


def _resample_staircase(values: list[float], n_ticks: int) -> np.ndarray:
    """File resolution → tick resolution as piecewise-constant repeat."""
    arr = np.asarray(values, dtype=float)
    idx = (np.arange(n_ticks) * len(arr)) // n_ticks
    return arr[idx]


def _hourly_to_ticks(shape24: np.ndarray, steps_per_day: int) -> np.ndarray:
    """Linear interpolation of a 24-value hourly shape onto the tick raster
    (hour centers, wrap-around midnight), preserving the mean ≈ 1."""
    hours = np.arange(24, dtype=float) + 0.5
    tick_h = (np.arange(steps_per_day, dtype=float) + 0.5) * (24.0 / steps_per_day)
    ext_h = np.concatenate(([hours[-1] - 24.0], hours, [hours[0] + 24.0]))
    ext_v = np.concatenate(([shape24[-1]], shape24, [shape24[0]]))
    return np.interp(tick_h, ext_h, ext_v)


def _seed_for(name: str) -> int:
    return zlib.crc32(name.encode("utf-8"))


def _season_factor(doy: int, amp: float) -> float:
    """± amp swing, peak mid-July (doy 196), trough mid-January."""
    if amp == 0.0:
        return 1.0
    return 1.0 + amp * float(np.cos(2.0 * np.pi * (doy - 196) / 365.0))


def _default_day_type(day_index: int) -> str:
    """Calendar-free default: Monday-anchored week."""
    wd = day_index % 7
    return "workday" if wd < 5 else ("saturday" if wd == 5 else "sunday")


def build_demand_profiles(
    inputs: NetInputs,
    steps_per_day: int,
    env: EnvironmentState | None = None,
) -> np.ndarray:
    """The full-horizon demand array ``[n_consumers, steps_per_day · n_days]``."""
    env = env or EnvironmentState()
    n_days = inputs.n_days
    n_ticks = steps_per_day * n_days
    consumers = inputs.consumers.consumers
    out = np.zeros((len(consumers), n_ticks), dtype=float)

    environment = inputs.environment
    t_air = _resample_staircase(environment.t_air_c, n_ticks) + env.t_offset_c
    factor = (_resample_staircase(environment.demand_factor, n_ticks)
              if environment.demand_factor is not None else None)

    # per-day drivers
    day_t_mean = t_air.reshape(n_days, steps_per_day).mean(axis=1)
    day_t_max = t_air.reshape(n_days, steps_per_day).max(axis=1)
    if env.dryness_override is not None:
        dryness = np.full(n_days, float(env.dryness_override))
    elif environment.dryness is not None:
        dryness = np.asarray(environment.dryness, dtype=float)
    else:
        dryness = np.zeros(n_days)
    day_types = (list(environment.day_types)
                 if environment.day_types is not None
                 else [_default_day_type(d) for d in range(n_days)])

    irrigation_ticks = _hourly_to_ticks(IRRIGATION_SURGE, steps_per_day)

    for ci, spec in enumerate(consumers):
        base = float(spec.mdot_kg_per_s)
        if spec.size is None:
            # legacy path — bit-identical to M2/M0
            out[ci, :] = base * (factor if factor is not None else 1.0)
            continue

        p = params_for(spec.kind)
        rng = np.random.default_rng(_seed_for(spec.name or f"c{ci}"))
        shapes = {
            "workday": _hourly_to_ticks(p.workday, steps_per_day),
            "saturday": _hourly_to_ticks(p.saturday, steps_per_day),
            "sunday": _hourly_to_ticks(p.sunday, steps_per_day),
        }
        for d in range(n_days):
            dt = day_types[d]
            shape = shapes[dt]
            day_factor = (p.saturday_factor if dt == "saturday"
                          else p.sunday_factor if dt == "sunday" else 1.0)
            doy = (environment.season_day_of_year - 1 + d) % 365 + 1
            day_factor *= _season_factor(doy, p.season_amp)
            if p.temp_slope:
                day_factor *= min(
                    p.temp_cap,
                    1.0 + p.temp_slope * max(0.0, day_t_mean[d] - p.temp_ref_c))
            if p.pool_season and not (POOL_SEASON[0] <= doy <= POOL_SEASON[1]):
                day_factor *= p.off_season_factor
            series = shape * day_factor
            # the 2018 hot-dry regime: evening irrigation surge on
            # residential classes — daily max migrates to 19–21 h, peak
            # hour beyond 2× normal, elevated night flows (TF §6)
            if (p.irrigation and day_t_max[d] >= IRRIGATION_T_MAX_C
                    and dryness[d] >= IRRIGATION_DRYNESS):
                heat = min(1.0, (day_t_max[d] - IRRIGATION_T_MAX_C) / 6.0)
                gain = (0.7 + 1.3 * heat) * (0.5 + 0.5 * dryness[d])
                series = series + gain * irrigation_ticks
            sl = slice(d * steps_per_day, (d + 1) * steps_per_day)
            noise = np.clip(
                rng.normal(1.0, NOISE_SIGMA, steps_per_day), 0.3, None)
            out[ci, sl] = base * series * noise

    return out
