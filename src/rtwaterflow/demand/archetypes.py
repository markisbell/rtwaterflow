"""Archetype demand profiles (roadmap §4.8; every number from TF §6).

Per-consumer demand = base mdot × diurnal class shape × day factor
(weekday/weekend × season × weather) × noise. The 24-value hourly shapes
below are hand-tuned to the documented structure — night minimum 02–04
≈ 1/3 of mean, morning peak 06–09, evening peak 18–21, weekend peaks
shifted to late morning — and NORMALIZED to mean 1.0 in code, so the base
``mdot_kg_per_s`` keeps its contract meaning (mean demand on a normal
workday). Aggregate validation against W 410 fd/fh lives in the tests,
never in the generator (TF §6: the curves are targets with a known
small-area bias — itself a teaching point).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: pool (Freibad) season, day-of-year window (May–September, TF §6)
POOL_SEASON = (121, 273)
#: hot-dry irrigation regime trigger (2018 measurements, TF §6)
IRRIGATION_T_MAX_C = 28.0
IRRIGATION_DRYNESS = 0.5


def _norm(values: list[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    return arr / arr.mean()


# ---------------------------------------------------------------------------
# diurnal shapes (24 hourly values, normalized to mean 1.0)
# ---------------------------------------------------------------------------

RES_VILLAGE_WORKDAY = _norm([
    0.45, 0.38, 0.33, 0.33, 0.40, 0.65, 1.30, 1.60, 1.50, 1.25, 1.10, 1.10,
    1.15, 1.05, 0.95, 0.95, 1.05, 1.20, 1.40, 1.45, 1.30, 1.05, 0.80, 0.60])
RES_VILLAGE_WEEKEND = _norm([
    0.50, 0.40, 0.33, 0.33, 0.36, 0.45, 0.70, 1.00, 1.35, 1.60, 1.65, 1.55,
    1.40, 1.20, 1.05, 1.00, 1.05, 1.15, 1.30, 1.35, 1.20, 1.00, 0.80, 0.60])
RES_CITY_WORKDAY = _norm([
    0.45, 0.38, 0.33, 0.35, 0.45, 0.80, 1.45, 1.65, 1.45, 1.25, 1.15, 1.20,
    1.25, 1.15, 1.05, 1.05, 1.10, 1.25, 1.35, 1.30, 1.15, 0.95, 0.75, 0.58])
INDUSTRY_WORKDAY = _norm([
    0.15, 0.15, 0.15, 0.15, 0.25, 0.60, 1.60, 1.90, 1.95, 1.90, 1.85, 1.70,
    1.75, 1.80, 1.60, 1.30, 0.90, 0.55, 0.35, 0.25, 0.20, 0.15, 0.15, 0.15])
SCHOOL_WORKDAY = _norm([
    0.05, 0.05, 0.05, 0.05, 0.05, 0.10, 0.40, 1.50, 2.60, 3.20, 4.20, 3.60,
    3.00, 1.80, 0.90, 0.40, 0.20, 0.15, 0.10, 0.05, 0.05, 0.05, 0.05, 0.05])
OFFICE_WORKDAY = _norm([
    0.10, 0.10, 0.10, 0.10, 0.15, 0.30, 0.90, 1.70, 2.10, 2.10, 2.00, 1.90,
    2.05, 1.95, 1.80, 1.60, 1.30, 0.80, 0.40, 0.25, 0.15, 0.10, 0.10, 0.10])
HOSPITAL_ALLDAYS = _norm([
    0.55, 0.45, 0.40, 0.40, 0.50, 0.75, 1.10, 1.45, 1.55, 1.45, 1.35, 1.30,
    1.35, 1.25, 1.15, 1.10, 1.15, 1.25, 1.30, 1.20, 1.05, 0.90, 0.75, 0.65])
FARM_DAIRY_ALLDAYS = _norm([
    0.30, 0.25, 0.25, 0.25, 0.60, 2.40, 2.60, 1.30, 0.80, 0.70, 0.65, 0.70,
    0.75, 0.70, 0.65, 0.80, 2.20, 2.50, 1.40, 0.70, 0.50, 0.40, 0.35, 0.30])
FARM_PIGS_ALLDAYS = _norm([
    0.45, 0.40, 0.40, 0.40, 0.55, 1.10, 1.60, 1.40, 1.10, 0.95, 0.90, 0.95,
    1.20, 1.30, 1.10, 1.00, 1.20, 1.30, 1.10, 0.85, 0.70, 0.60, 0.50, 0.45])
#: pool: opening-hours bulk (visitor showers, attraction fill) + the
#: nightly filter backwash pulse at 02:00–03:00 (DIN 19643)
POOL_SEASON_DAY = _norm([
    0.20, 0.15, 3.50, 0.90, 0.20, 0.20, 0.30, 0.60, 1.20, 1.80, 2.00, 2.10,
    2.20, 2.20, 2.10, 2.00, 1.90, 1.70, 1.40, 1.00, 0.60, 0.40, 0.30, 0.25])
FLAT = np.ones(24, dtype=float)

#: hot-dry irrigation surge (ADDED to residential shapes; the 2018 regime:
#: daily maximum migrates to 19–21 h with the garden-irrigation evening
#: block AND night flows stay elevated — agricultural/timer irrigation).
#: Tuned so extreme hot-dry days roughly DOUBLE the residential volume
#: (TF §6: heat waves 1.35–2×; Wiesbaden 1976 record 1.59×) while the
#: evening peak lands near the W 410 fh corridor, not beyond it.
IRRIGATION_SURGE = np.asarray([
    0.55, 0.50, 0.45, 0.40, 0.40, 0.30, 0.05, 0.00, 0.00, 0.00, 0.05, 0.05,
    0.10, 0.10, 0.15, 0.20, 0.30, 0.70, 1.20, 1.95, 2.15, 1.65, 0.95, 0.65])


@dataclass
class ArchetypeParams:
    """Per-class profile parameters (weekday/weekend shapes + factors)."""

    workday: np.ndarray
    saturday: np.ndarray
    sunday: np.ndarray
    #: multiplies the whole day's volume on saturday / sunday
    saturday_factor: float = 1.0
    sunday_factor: float = 1.0
    #: seasonal swing amplitude (± around 1.0, peak mid-July)
    season_amp: float = 0.0
    #: temperature coupling: 1 + slope·max(0, T_day_mean − t_ref), capped
    temp_slope: float = 0.0
    temp_ref_c: float = 15.0
    temp_cap: float = 1.0
    #: residential irrigation surge participates on hot-dry days
    irrigation: bool = False
    #: pool: outside POOL_SEASON only maintenance flow remains
    pool_season: bool = False
    off_season_factor: float = 1.0
    extra: dict = field(default_factory=dict)


ARCHETYPES: dict[str, ArchetypeParams] = {
    "residential_village": ArchetypeParams(
        workday=RES_VILLAGE_WORKDAY, saturday=RES_VILLAGE_WEEKEND,
        sunday=RES_VILLAGE_WEEKEND,
        saturday_factor=1.05, sunday_factor=1.05,   # dormitory weekend peak
        season_amp=0.08, temp_slope=0.0, irrigation=True),
    "residential_city": ArchetypeParams(
        workday=RES_CITY_WORKDAY, saturday=RES_VILLAGE_WEEKEND,
        sunday=RES_VILLAGE_WEEKEND,
        saturday_factor=0.92, sunday_factor=0.88,   # out-commuting weekend
        season_amp=0.08, irrigation=True),
    "industry": ArchetypeParams(
        workday=INDUSTRY_WORKDAY, saturday=FLAT, sunday=FLAT,
        saturday_factor=0.30, sunday_factor=0.20),
    "school": ArchetypeParams(
        workday=SCHOOL_WORKDAY, saturday=FLAT, sunday=FLAT,
        saturday_factor=0.06, sunday_factor=0.06),
    "office": ArchetypeParams(
        workday=OFFICE_WORKDAY, saturday=FLAT, sunday=FLAT,
        saturday_factor=0.12, sunday_factor=0.10),
    "hospital": ArchetypeParams(
        workday=HOSPITAL_ALLDAYS, saturday=HOSPITAL_ALLDAYS,
        sunday=HOSPITAL_ALLDAYS),
    "farm_dairy": ArchetypeParams(
        workday=FARM_DAIRY_ALLDAYS, saturday=FARM_DAIRY_ALLDAYS,
        sunday=FARM_DAIRY_ALLDAYS,
        temp_slope=0.07, temp_ref_c=15.0, temp_cap=2.0),   # ≈2× at 29 °C
    "farm_pigs": ArchetypeParams(
        workday=FARM_PIGS_ALLDAYS, saturday=FARM_PIGS_ALLDAYS,
        sunday=FARM_PIGS_ALLDAYS,
        temp_slope=0.04, temp_ref_c=18.0, temp_cap=1.5),
    "pool": ArchetypeParams(
        workday=POOL_SEASON_DAY, saturday=POOL_SEASON_DAY,
        sunday=POOL_SEASON_DAY,
        saturday_factor=1.25, sunday_factor=1.30,    # visitor peak
        temp_slope=0.05, temp_ref_c=18.0, temp_cap=1.8,  # bathers ~ weather
        pool_season=True, off_season_factor=0.05),
    "other": ArchetypeParams(workday=FLAT, saturday=FLAT, sunday=FLAT),
}

#: M1 generic kinds map to concrete archetypes (no bundle rewrite — the
#: promise made when kind/storeys shipped in M1)
KIND_ALIASES = {
    "residential": "residential_village",
    "farm": "farm_dairy",
}


def params_for(kind: str) -> ArchetypeParams:
    return ARCHETYPES[KIND_ALIASES.get(kind, kind)]
