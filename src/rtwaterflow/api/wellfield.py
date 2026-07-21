"""M6 raw-water side — well fields, aquifer, drought, regeneration.

* ``GET  /wellfields``                  — live raw-side states.
* ``POST /wellfield/drought``           — set the recharge drought factor.
* ``POST /wellfield/{name}/well/{well}/regenerate`` — well regeneration.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .runtime import get_app

log = logging.getLogger(__name__)

router = APIRouter(tags=["wellfield"])


@router.get("/wellfields", summary="Well-field states (raw-water side)")
def wellfields() -> dict:
    """Live raw-side states: aquifer level, production, capacity, well
    ageing, water-right accounting, energy KPI."""
    sim = get_app().sim
    return {"wellfields": [wf.payload() for wf in sim.wellfields]}


class DroughtBody(BaseModel):
    #: recharge scaling — 1.0 normal, 0 no recharge (a severe drought)
    factor: float = Field(ge=0.0, le=2.0)


@router.post("/wellfield/drought", summary="Set the drought factor")
def set_drought(body: DroughtBody) -> dict:
    """Scale groundwater recharge on every aquifer. Below normal the level
    declines under abstraction, capping well production — the Lauenau
    drought mechanism."""
    return get_app().sim.set_drought(body.factor)


@router.post("/wellfield/{name}/well/{well}/regenerate",
             summary="Regenerate a well")
def regenerate_well(name: str, well: str) -> dict:
    """Well regeneration (W 130): restore ~90 % of the nameplate specific
    capacity (the maintenance action against Verockerung/ageing)."""
    try:
        return get_app().sim.regenerate_well(name, well)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
