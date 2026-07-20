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

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    """One pipe (single layer — no supply/return expansion)."""

    from_node: str
    to_node: str
    length_km: float = Field(gt=0)
    inner_diameter_mm: float = Field(gt=0)
    # Integral roughness (DVGW GW 303-1 practice: 0.1 transport / 0.4 mains /
    # 1.0 mm meshed old nets). Explicit per pipe so a pandapipes default
    # change can never silently move results.
    k_mm: float = Field(default=0.1, ge=0)
    sections: int = Field(default=1, ge=1)
    geometry: Optional[list[tuple[float, float]]] = None  # [(lat, lon), ...]

    @model_validator(mode="after")
    def _no_self_loop(self) -> "PipeSpec":
        if self.from_node == self.to_node:
            raise ValueError(f"pipe {self.from_node}->{self.to_node}: self-loop")
        return self


class PipesFile(_StrictModel):
    pipes: list[PipeSpec] = Field(min_length=1)


# ---------------------------------------------------------------------------
# consumers.json
# ---------------------------------------------------------------------------

class ConsumerSpec(_StrictModel):
    """One consumer = one ``sink`` element (fixed demand in M0)."""

    node: str
    name: Optional[str] = None
    mdot_kg_per_s: float = Field(gt=0)


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


class SupplyFile(_StrictModel):
    supplies: list[SupplySpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _exactly_one_ext_grid(self) -> "SupplyFile":
        n = sum(1 for s in self.supplies if s.kind == "ext_grid")
        if n != 1:
            raise ValueError(
                f"exactly one slack (ext_grid head source) required, got {n} "
                "(every hydraulically connected net needs exactly one fixed-pressure "
                "node in M0; tanks/stations with controllers arrive in M2)"
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
