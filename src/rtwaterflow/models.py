"""Pydantic v2 models for the five-file water data contract (roadmap §3, M0 subset).

The five input documents of a drinking-water network bundle:

* ``network_structure.json`` — one entry per node; single pipe layer, so one
  entry = one pandapipes junction. Every node carries ``elevation_m``
  (DHHN2016 metres, fed into pandapipes ``height_m`` — the hydrostatic term).
* ``pipes.json`` — one entry per pipe (``create_pipe_from_parameters`` with
  ``inner_diameter_mm`` and integral roughness ``k_mm`` per DVGW GW 303-1).
* ``consumers.json`` — one entry per consumer = one ``sink`` element
  (row order = element index). M0: fixed demand ``mdot_kg_per_s``; the
  demand engine (M3) replaces the scalar with archetype profiles.
* ``supply.json`` — head sources. M0: exactly one ``ext_grid`` slack
  (fixed-pressure node, e.g. a Hochbehälter water surface).
* ``environment.json`` — horizon owner (steps × resolution) plus the
  environment drivers for later milestones (air temperature → demand
  weather coupling, M3).

Cross-document validation lives in :mod:`rtwaterflow.data_loader`.
"""
from __future__ import annotations

import math
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .pipe_catalog import default_k_mm, inner_diameter_mm

_EARTH_R_KM = 6371.0088


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lon) points [km]."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * _EARTH_R_KM * math.asin(math.sqrt(h))


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# network_structure.json
# ---------------------------------------------------------------------------

class StructureJunction(_StrictModel):
    name: str
    kind: Literal["node", "consumer", "source"] = "node"
    geo: tuple[float, float]  # (lat, lon) — WGS84, Leaflet-native order
    elevation_m: float        # metres above sea level → pandapipes height_m
    pn_bar: float = Field(gt=0)  # nominal/init pressure (warm-start seed)


class NetworkStructure(_StrictModel):
    name: str
    junctions: list[StructureJunction] = Field(min_length=2)
    #: visible-credit strings for geodata-derived bundles (M8, TF §11) — OSM
    #: (ODbL) + the DEM source; rendered as a map attribution. Hand-authored
    #: synthetic bundles omit it.
    attribution: Optional[list[str]] = None

    @field_validator("junctions")
    @classmethod
    def _unique_names(cls, v: list[StructureJunction]) -> list[StructureJunction]:
        names = [j.name for j in v]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate junction names: {sorted(dupes)}")
        return v


# ---------------------------------------------------------------------------
# pipes.json
# ---------------------------------------------------------------------------

class PipeSpec(_StrictModel):
    """One pipe (single layer — no supply/return expansion).

    Sizing comes either from the **pipe catalog** (``dn`` + ``material`` —
    the bundle-author-friendly path; inner diameter and default integral
    roughness are resolved per :mod:`rtwaterflow.pipe_catalog`) or from an
    explicit ``inner_diameter_mm``. An explicit ``k_mm`` always wins.
    ``length_km`` may be omitted when a ``geometry`` polyline is given —
    the great-circle length of the polyline is used then.
    """

    from_node: str
    to_node: str
    length_km: Optional[float] = Field(default=None, gt=0)
    # catalog path
    dn: Optional[int] = Field(default=None, gt=0)
    material: Optional[Literal["PE", "PVC", "GGG", "GG", "St", "AZ"]] = None
    year_laid: Optional[int] = Field(default=None, ge=1850, le=2100)
    # explicit path
    inner_diameter_mm: Optional[float] = Field(default=None, gt=0)
    # Integral roughness (DVGW GW 303-1 practice: 0.1 transport / 0.4 mains /
    # 1.0 mm meshed old nets). Resolved explicitly at validation so a
    # pandapipes default change can never silently move results.
    k_mm: Optional[float] = Field(default=None, ge=0)
    sections: int = Field(default=1, ge=1)
    geometry: Optional[list[tuple[float, float]]] = None  # [(lat, lon), ...]

    @model_validator(mode="after")
    def _resolve(self) -> "PipeSpec":
        if self.from_node == self.to_node:
            raise ValueError(f"pipe {self.from_node}->{self.to_node}: self-loop")
        label = f"pipe {self.from_node}->{self.to_node}"
        # sizing: catalog (dn+material) XOR explicit inner_diameter_mm
        if self.dn is not None or self.material is not None:
            if self.inner_diameter_mm is not None:
                raise ValueError(
                    f"{label}: give either dn+material (catalog) or "
                    "inner_diameter_mm, not both")
            if self.dn is None or self.material is None:
                raise ValueError(f"{label}: dn and material belong together")
            try:
                self.inner_diameter_mm = inner_diameter_mm(self.dn, self.material)
            except KeyError as exc:
                raise ValueError(f"{label}: {exc.args[0]}")
            if self.k_mm is None:
                self.k_mm = default_k_mm(self.material)
        elif self.inner_diameter_mm is None:
            raise ValueError(
                f"{label}: needs dn+material (catalog) or inner_diameter_mm")
        if self.k_mm is None:
            self.k_mm = 0.1
        # length: explicit, or derived from the geometry polyline
        if self.length_km is None:
            if not self.geometry or len(self.geometry) < 2:
                raise ValueError(
                    f"{label}: needs length_km or a geometry polyline "
                    "(>= 2 points) to derive it")
            self.length_km = round(sum(
                _haversine_km(self.geometry[i], self.geometry[i + 1])
                for i in range(len(self.geometry) - 1)), 6)
            if self.length_km <= 0:
                raise ValueError(f"{label}: degenerate geometry (zero length)")
        return self


class PipesFile(_StrictModel):
    pipes: list[PipeSpec] = Field(min_length=1)


# ---------------------------------------------------------------------------
# consumers.json
# ---------------------------------------------------------------------------

class ConsumerSize(_StrictModel):
    """Archetype sizing (roadmap §3 consumers.json): drives the M3 demand
    profiles' peak structure and the W 410 aggregate validation. The
    fields are per-archetype alternatives — at least ONE must be set (an
    empty ``size: {}`` would silently flip the consumer from the bit-exact
    legacy path onto archetype shaping; M3 review finding)."""

    population: Optional[int] = Field(default=None, gt=0)
    employees: Optional[int] = Field(default=None, gt=0)
    pupils: Optional[int] = Field(default=None, gt=0)
    beds: Optional[int] = Field(default=None, gt=0)
    animals: Optional[int] = Field(default=None, gt=0)
    visitors_design: Optional[int] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _at_least_one(self) -> "ConsumerSize":
        if not any(v is not None for v in (
                self.population, self.employees, self.pupils, self.beds,
                self.animals, self.visitors_design)):
            raise ValueError(
                "consumer size: at least one field required (population/"
                "employees/pupils/beds/animals/visitors_design) — an empty "
                "size object would still switch the consumer onto the "
                "archetype demand path")
        return self


class ConsumerSpec(_StrictModel):
    """One consumer = one ``sink`` element. ``mdot_kg_per_s`` is the MEAN
    base demand; since M3 the demand engine shapes it with the archetype's
    diurnal/weekly/seasonal/weather profile (``kind`` + ``size``). The M1
    generic kinds stay legal and map to defaults (residential → village,
    farm → dairy). ``storeys`` feeds the M4 W 400-1 minimum-pressure check
    (2.0 + 0.35 bar per storey)."""

    node: str
    name: Optional[str] = None
    mdot_kg_per_s: float = Field(gt=0)
    kind: Literal[
        "residential", "residential_city", "residential_village",
        "industry", "farm", "farm_dairy", "farm_pigs",
        "school", "office", "hospital", "pool", "other"] = "residential"
    storeys: int = Field(default=1, ge=1, le=8)
    size: Optional[ConsumerSize] = None


class ConsumersFile(_StrictModel):
    consumers: list[ConsumerSpec] = Field(min_length=1)


# ---------------------------------------------------------------------------
# supply.json
# ---------------------------------------------------------------------------

class SupplySpec(_StrictModel):
    """One head source. M0: ``ext_grid`` — a fixed-pressure slack node
    (tank water surface / external feed). Wire kind stays ``slack``."""

    node: str
    name: Optional[str] = None
    kind: Literal["ext_grid"] = "ext_grid"
    p_bar: float = Field(gt=0)


class PrvSpec(_StrictModel):
    """A pressure-reducing valve (Druckminderer) between two zones:
    pandapipes ``press_control`` holding ``p_out_bar`` at its outlet
    junction. M1 supports the static Durchlauf case (flow always passes
    downhill through the PRV); runtime supervision (reverse-flow closing,
    setpoint conflicts) arrives with the M2 zone controllers."""

    from_node: str
    to_node: str
    name: Optional[str] = None
    p_out_bar: float = Field(gt=0)

    @model_validator(mode="after")
    def _no_self_loop(self) -> "PrvSpec":
        if self.from_node == self.to_node:
            raise ValueError(f"prv {self.from_node}->{self.to_node}: self-loop")
        return self


class TankSpec(_StrictModel):
    """An elevated tank (Hochbehälter / Wasserturm): the floating head of
    its zone. Modeled as an ext_grid at *node* whose pressure the
    :class:`~rtwaterflow.assets.tank.WaterTank` controller re-writes each
    tick from the integrated water level (level → head, 1 bar ≈ 10.2 m).
    The tank BOTTOM sits at the node's ``elevation_m``; ``level_*`` are
    water columns above it."""

    node: str
    name: Optional[str] = None
    area_m2: float = Field(gt=0)
    level_min_m: float = Field(ge=0)
    level_max_m: float = Field(gt=0)
    level_initial_m: float = Field(gt=0)
    #: dedicated Löschwasserreserve [m³] held ABOVE level_min (W 405/W 300-1)
    fire_reserve_m3: float = Field(default=0.0, ge=0)
    #: ``break`` is the Reinwasserbehälter (M6): the raw/network decoupling
    #: tank a well field fills; hydraulically an ext_grid like the others
    kind: Literal["durchlauf", "gegen", "break"] = "durchlauf"

    @model_validator(mode="after")
    def _levels(self) -> "TankSpec":
        if not (self.level_min_m < self.level_max_m):
            raise ValueError(f"tank at {self.node!r}: level_min < level_max required")
        if not (self.level_min_m <= self.level_initial_m <= self.level_max_m):
            raise ValueError(
                f"tank at {self.node!r}: level_initial outside [min, max]")
        if self.fire_reserve_m3 > (self.level_max_m - self.level_min_m) * self.area_m2:
            raise ValueError(
                f"tank at {self.node!r}: fire reserve exceeds the usable volume")
        return self


class StationControl(_StrictModel):
    """Pump-station control. ``hysteresis`` is the canonical German pattern
    (TF §5): start below ``on_below_m`` tank level, stop above
    ``off_above_m``. ``manual`` runs fixed until the operator toggles it."""

    mode: Literal["hysteresis", "manual"] = "hysteresis"
    tank: Optional[str] = None          # tank NAME (hysteresis mode)
    on_below_m: Optional[float] = Field(default=None, gt=0)
    off_above_m: Optional[float] = Field(default=None, gt=0)
    running: bool = True                # manual mode initial state

    @model_validator(mode="after")
    def _mode_fields(self) -> "StationControl":
        if self.mode == "hysteresis":
            if not self.tank or self.on_below_m is None or self.off_above_m is None:
                raise ValueError(
                    "hysteresis control needs tank + on_below_m + off_above_m")
            if not (self.on_below_m < self.off_above_m):
                raise ValueError("hysteresis band: on_below_m < off_above_m")
        return self


class StationSpec(_StrictModel):
    """A pump station: pandapipes ``pump`` branch with a Q-H characteristic
    (regression std_type from the given curve points)."""

    from_node: str
    to_node: str
    name: Optional[str] = None
    #: characteristic curve [[flow m³/h, pressure lift bar], ...] — at least
    #: 3 points, strictly decreasing lift over increasing flow
    curve: list[tuple[float, float]] = Field(min_length=3)
    control: StationControl = Field(default_factory=StationControl)

    @model_validator(mode="after")
    def _curve_shape(self) -> "StationSpec":
        if self.from_node == self.to_node:
            raise ValueError(
                f"station {self.from_node}->{self.to_node}: self-loop")
        flows = [p[0] for p in self.curve]
        lifts = [p[1] for p in self.curve]
        if flows != sorted(flows) or len(set(flows)) != len(flows):
            raise ValueError("station curve: flows must strictly increase")
        if lifts != sorted(lifts, reverse=True):
            raise ValueError("station curve: lift must decrease with flow")
        return self

    @model_validator(mode="after")
    def _curve_fit(self) -> "StationSpec":
        """Validate the degree-2 REGRESSION the engine actually runs on
        (pandapipes fits the same np.polyfit polynomial): decreasing input
        points can still fit to a convex parabola whose minimum lies inside
        the Q-range — the operating-point iteration's g-strictly-decreasing
        invariant would silently break and every running tick degrade (M2
        review finding). Reject loudly at load time instead."""
        import numpy as np

        x = np.asarray([p[0] for p in self.curve], dtype=float)
        y = np.asarray([p[1] for p in self.curve], dtype=float)
        reg = np.polyfit(x, y, 2)
        q_hi = 1.2 * float(x[-1])
        qs = np.linspace(0.0, q_hi, 25)
        slope = np.polyval(np.polyder(reg), qs)
        if np.any(slope >= 0):
            raise ValueError(
                "station curve: the degree-2 regression fitted to these "
                "points is not strictly decreasing over "
                f"[0, {q_hi:.0f} m³/h] — pandapipes runs on the FIT, not "
                "the points; supply points closer to a downward parabola")
        resid = float(np.max(np.abs(np.polyval(reg, x) - y)))
        if resid > max(0.3, 0.05 * float(y[0])):
            raise ValueError(
                "station curve: the degree-2 regression deviates from the "
                f"declared points by up to {resid:.2f} bar — the engine "
                "would run a materially different characteristic; supply "
                "points a parabola can follow")
        return self


class WellSpec(_StrictModel):
    """One vertical filter well (Vertikalfilterbrunnen, W 118/W 123, M6)."""

    name: str
    static_level_m: float                              # Ruhewasserspiegel
    spec_capacity_m3h_per_m: float = Field(gt=0)       # Q/s
    screen_top_m: float                                # Filteroberkante
    rated_m3_h: float = Field(gt=0)
    q_s_decay_per_a: float = Field(default=0.03, ge=0, le=0.5)
    protection_margin_m: float = Field(default=1.0, ge=0)

    @model_validator(mode="after")
    def _levels(self) -> "WellSpec":
        if not (self.screen_top_m < self.static_level_m):
            raise ValueError(
                f"well {self.name}: screen_top_m must be below static_level_m")
        return self


class AquiferSpec(_StrictModel):
    """Single linear reservoir (Einzellinearspeicher, TF §5, M6)."""

    storativity_area_m2: float = Field(gt=0)   # S_y·A [m²/m] drop per m³
    level_initial_m: float
    recharge_m3_per_d_mean: float = Field(ge=0)


class WaterRightSpec(_StrictModel):
    """Abstraction permit (WHG §§8–10) — a compliance cap, not a physical
    limit (exceeding it is a warning, not a hydraulic failure)."""

    m3_per_a: Optional[float] = Field(default=None, gt=0)
    m3_per_d: Optional[float] = Field(default=None, gt=0)


class WellFieldSpec(_StrictModel):
    """A well field feeding a break tank (Reinwasserbehälter). The raw side
    is pure Python (assets/wellfield.py); the break tank is the only shared
    hydraulic state (M6, TF §5)."""

    name: str
    wells: list[WellSpec] = Field(min_length=1)
    aquifer: AquiferSpec
    #: the Reinwasserbehälter this field fills — a TankSpec with kind "break"
    break_tank: str
    pump_head_m: float = Field(gt=0)           # well → break-tank lift
    efficiency: float = Field(default=0.62, gt=0.1, le=1.0)
    water_right: WaterRightSpec = Field(default_factory=WaterRightSpec)
    interference_fraction: float = Field(default=0.15, ge=0, le=1.0)
    #: break-tank level hysteresis for the well pumps (fill below on, stop
    #: above off) — the canonical two-point control (TF §5)
    on_below_m: float = Field(gt=0)
    off_above_m: float = Field(gt=0)

    @model_validator(mode="after")
    def _band(self) -> "WellFieldSpec":
        if not (self.on_below_m < self.off_above_m):
            raise ValueError(
                f"well field {self.name}: on_below_m < off_above_m")
        names = [w.name for w in self.wells]
        if len(set(names)) != len(names):
            raise ValueError(f"well field {self.name}: duplicate well names")
        return self


class SupplyFile(_StrictModel):
    supplies: list[SupplySpec] = Field(default_factory=list)
    prvs: list[PrvSpec] = Field(default_factory=list)
    tanks: list[TankSpec] = Field(default_factory=list)
    stations: list[StationSpec] = Field(default_factory=list)
    wellfields: list[WellFieldSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _head_sources(self) -> "SupplyFile":
        n_heads = (sum(1 for s in self.supplies if s.kind == "ext_grid")
                   + len(self.tanks))
        if n_heads < 1:
            raise ValueError(
                "at least one head source (ext_grid or tank) required — "
                "pipeflow needs a pressure-fixed node")
        nodes = ([s.node for s in self.supplies]
                 + [t.node for t in self.tanks])
        dupes = {n for n in nodes if nodes.count(n) > 1}
        if dupes:
            raise ValueError(
                f"head sources collide on node(s) {sorted(dupes)} — one "
                "fixed-pressure element per junction")
        names = [t.name or f"tank_{t.node}" for t in self.tanks]
        if len(set(names)) != len(names):
            raise ValueError("tank names must be unique")
        # station names are load-bearing keys (pump std_type registry,
        # station_modes, rule bindings, the _solve_step bracket state) —
        # a duplicate silently overwrites the first station's curve and
        # collapses its control (M2 review finding)
        snames = [s.name or f"station_{s.from_node}" for s in self.stations]
        if len(set(snames)) != len(snames):
            raise ValueError(
                "station names must be unique (unnamed stations resolve to "
                "station_<from_node> — two unnamed stations may not share "
                "a from_node)")
        # two pump branches on one node pair leave the flow split
        # indeterminate — every solve fails with a singular Jacobian
        # (upstream pandapipes issue #693; M2 review finding)
        edges = [frozenset((s.from_node, s.to_node)) for s in self.stations]
        if len(set(edges)) != len(edges):
            raise ValueError(
                "two stations on the same node pair (parallel pump "
                "branches) — pandapipes cannot solve this (issue #693); "
                "model parallel pumps as ONE station with a combined curve")
        # M6 well fields must fill a real break tank
        break_tanks = {t.name or f"tank_{t.node}" for t in self.tanks
                       if t.kind == "break"}
        wf_names = [w.name for w in self.wellfields]
        if len(set(wf_names)) != len(wf_names):
            raise ValueError("well field names must be unique")
        for wf in self.wellfields:
            if wf.break_tank not in break_tanks:
                raise ValueError(
                    f"well field {wf.name}: break_tank {wf.break_tank!r} is "
                    "not a tank with kind 'break'")
        return self


# ---------------------------------------------------------------------------
# environment.json
# ---------------------------------------------------------------------------

class EnvironmentFile(_StrictModel):
    """Horizon owner + environment drivers (air temperature is unused until
    the M3 demand engine couples it to irrigation/pool/livestock demand).

    ``demand_factor`` is the M2 interim diurnal modulation: a global
    multiplicative factor per step applied to every consumer's base demand.
    Since M3 it is the LEGACY fallback — bundles whose consumers carry
    archetype ``size`` data get engine-generated per-consumer profiles
    instead (demand/engine.py).

    M3 drivers:

    * ``day_types`` — one label per day of the horizon; defaults to a
      Monday-anchored week (day i % 7 → Mo..So).
    * ``dryness`` — one 0..1 drought index per day (irrigation trigger).
    * ``season_day_of_year`` — calendar anchor of day 0 (pool season,
      seasonal factor); default 1 (January 1st).
    """

    resolution_minutes: int = Field(gt=0)
    steps: int = Field(gt=0)
    t_air_c: list[float]
    demand_factor: Optional[list[float]] = None
    day_types: Optional[list[Literal["workday", "saturday", "sunday"]]] = None
    dryness: Optional[list[float]] = None
    #: capped at 365: the engine runs an idealized 365-day year (the %365
    #: doy wrap would fold 366 onto January 1st — M3 review finding)
    season_day_of_year: int = Field(default=1, ge=1, le=365)

    @model_validator(mode="after")
    def _lengths(self) -> "EnvironmentFile":
        if len(self.t_air_c) != self.steps:
            raise ValueError(
                f"environment.t_air_c: length {len(self.t_air_c)} != steps {self.steps}"
            )
        if self.demand_factor is not None:
            if len(self.demand_factor) != self.steps:
                raise ValueError(
                    f"environment.demand_factor: length "
                    f"{len(self.demand_factor)} != steps {self.steps}")
            if any(f <= 0 for f in self.demand_factor):
                raise ValueError("environment.demand_factor: factors must be > 0")
        total_min = self.steps * self.resolution_minutes
        n_days = max(1, total_min // (24 * 60))
        if self.day_types is not None and len(self.day_types) != n_days:
            raise ValueError(
                f"environment.day_types: length {len(self.day_types)} != "
                f"{n_days} horizon days")
        if self.dryness is not None:
            if len(self.dryness) != n_days:
                raise ValueError(
                    f"environment.dryness: length {len(self.dryness)} != "
                    f"{n_days} horizon days")
            if any(not 0.0 <= d <= 1.0 for d in self.dryness):
                raise ValueError("environment.dryness: values must be in [0, 1]")
        return self
