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

class ConsumerSpec(_StrictModel):
    """One consumer = one ``sink`` element (fixed demand until the M3
    demand engine). ``kind`` and ``storeys`` are carried now so the M3
    archetype profiles and the M4 compliance checks (W 400-1 minimum
    pressure = 2.0 + 0.35 bar per storey above ground) need no bundle
    rewrite."""

    node: str
    name: Optional[str] = None
    mdot_kg_per_s: float = Field(gt=0)
    kind: Literal["residential", "industry", "farm", "school", "office",
                  "hospital", "pool", "other"] = "residential"
    storeys: int = Field(default=1, ge=1, le=8)


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


class SupplyFile(_StrictModel):
    supplies: list[SupplySpec] = Field(min_length=1)
    prvs: list[PrvSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _exactly_one_ext_grid(self) -> "SupplyFile":
        n = sum(1 for s in self.supplies if s.kind == "ext_grid")
        if n != 1:
            raise ValueError(
                f"exactly one slack (ext_grid head source) required, got {n} "
                "(every hydraulically connected net needs exactly one fixed-pressure "
                "node in M0/M1; tanks/stations with controllers arrive in M2)"
            )
        return self


# ---------------------------------------------------------------------------
# environment.json
# ---------------------------------------------------------------------------

class EnvironmentFile(_StrictModel):
    """Horizon owner + environment drivers (air temperature is unused in M0;
    the M3 demand engine couples it to irrigation/pool/livestock demand)."""

    resolution_minutes: int = Field(gt=0)
    steps: int = Field(gt=0)
    t_air_c: list[float]

    @model_validator(mode="after")
    def _lengths(self) -> "EnvironmentFile":
        if len(self.t_air_c) != self.steps:
            raise ValueError(
                f"environment.t_air_c: length {len(self.t_air_c)} != steps {self.steps}"
            )
        return self
