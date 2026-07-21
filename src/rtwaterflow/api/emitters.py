"""M5 pressure-dependent hydraulics — emitters + PDA toggle.

* ``GET  /emitters``          — live leak/hydrant/burst states.
* ``POST /hydrant``           — open a fire hydrant (target flow, duration).
* ``POST /burst``             — place a pipe burst (orifice area).
* ``POST /leakage``           — seed distributed background leakage.
* ``DELETE /leakage``         — clear the background leaks.
* ``DELETE /emitter/{name}``  — remove one emitter.
* ``GET/POST /pda``           — pressure-driven-demand toggle.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .runtime import get_app

log = logging.getLogger(__name__)

router = APIRouter(tags=["emitters"])


def _minutes_to_ticks(app, minutes: float | None) -> int | None:  # noqa: D401
    if minutes is None:
        return None
    spd = app.engine.steps_per_day
    return max(1, int(round(float(minutes) * spd / 1440.0)))


@router.get("/emitters", summary="Emitter states (leaks/hydrants/bursts)")
def emitters() -> dict:
    """Live emitter states + the PDA toggle."""
    sim = get_app().sim
    return {
        "emitters": [e.payload() for e in sim.emitters.emitters.values()],
        "pda_enabled": sim.pda.enabled,
    }


class HydrantBody(BaseModel):
    node: str
    target_m3_h: float = Field(gt=0, le=1000)
    duration_minutes: Optional[float] = Field(default=120.0, gt=0)
    name: Optional[str] = None


@router.post("/hydrant", summary="Open a fire hydrant")
def open_hydrant(body: HydrantBody) -> dict:
    app = get_app()
    sim = app.sim
    try:
        em = sim.open_hydrant(
            node=body.node, target_m3_h=body.target_m3_h,
            duration_ticks=_minutes_to_ticks(app, body.duration_minutes),
            name=body.name)
    except KeyError as exc:
        raise HTTPException(400, str(exc))
    log.info("hydrant opened at %s (%.0f m3/h)", body.node, body.target_m3_h)
    return em


class BurstBody(BaseModel):
    node: str
    area_m2: float = Field(gt=0, le=1.0)  # orifice area (a few cm² typical)
    name: Optional[str] = None


@router.post("/burst", summary="Place a pipe burst")
def place_burst(body: BurstBody) -> dict:
    app = get_app()
    try:
        em = app.sim.place_burst(node=body.node, area_m2=body.area_m2,
                                 name=body.name)
    except KeyError as exc:
        raise HTTPException(400, str(exc))
    log.info("burst placed at %s (A=%.4f m2)", body.node, body.area_m2)
    return em


class LeakageBody(BaseModel):
    #: FAVAD coefficient per km of incident pipe (mdot = C·p^1.15) — a knob
    #: the operator raises to age the network / lower to repair
    coefficient_per_km: float = Field(ge=0, le=1.0)


@router.post("/leakage", summary="Seed background leakage")
def set_leakage(body: LeakageBody) -> dict:
    return get_app().sim.set_leakage(
        coefficient_per_km=body.coefficient_per_km)


@router.delete("/leakage", summary="Clear background leakage")
def clear_leakage() -> dict:
    return {"cleared": get_app().sim.clear_leakage()}


@router.delete("/emitter/{name}", summary="Remove an emitter")
def remove_emitter(name: str) -> dict:
    if not get_app().sim.remove_emitter(name):
        raise HTTPException(404, f"unknown emitter {name!r}")
    return {"removed": name}


class PdaBody(BaseModel):
    enabled: bool


@router.get("/pda", summary="Pressure-driven-demand toggle")
def pda() -> dict:
    return {"pda_enabled": get_app().sim.pda.enabled}


@router.post("/pda", summary="Toggle pressure-driven demand")
def set_pda(body: PdaBody) -> dict:
    """ON (default): undersupplied taps deliver less (Wagner). OFF: fixed
    demand — undersupply shows as impossible negative pressure (the M0
    contrast that motivates PDA)."""
    return get_app().sim.set_pda(body.enabled)
