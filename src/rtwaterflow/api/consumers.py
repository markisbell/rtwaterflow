"""Consumer endpoints.

* ``POST /consumer {node, mdot_kg_per_s}`` — place a fixed-demand sink at an
  existing node (M0; the archetype demand engine returns in M3).
* ``DELETE /consumer/{id}`` — removes a consumer (409 for the last one).

Every placement lands in the simulator's consumer op log as a *recipe*, so
scenario save/load replays it deterministically.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .runtime import App, get_app

log = logging.getLogger(__name__)

router = APIRouter(tags=["consumers"])


class ConsumerBody(BaseModel):
    node: str
    name: Optional[str] = None
    mdot_kg_per_s: float = Field(gt=0, le=1000)


def build_consumer_op(app: App, op: dict) -> tuple[float, dict]:
    """Resolve a consumer placement recipe into a demand (+ echo recipe).

    Shared by ``POST /consumer`` and the scenario replay — the recipe is
    what the op log stores, so both paths are bit-identical.
    """
    mdot = op.get("mdot_kg_per_s")
    if not mdot or float(mdot) <= 0:
        raise HTTPException(
            status_code=400,
            detail="consumer placement needs 'mdot_kg_per_s' > 0 "
                   "(fixed demand; archetype profiles arrive in M3)")
    return float(mdot), {"mdot_kg_per_s": float(mdot)}


@router.post("/consumer", summary="Place a consumer")
def add_consumer(body: ConsumerBody) -> dict:
    """Place a fixed-demand sink at an existing node. 400 on unknown node."""
    app = get_app()
    sim = app.sim
    if body.node not in sim.index.junction:
        raise HTTPException(
            status_code=400,
            detail=f"unknown node {body.node!r} (see GET /network)")
    mdot, recipe = build_consumer_op(app, body.model_dump())
    added = sim.add_consumer(
        node=body.node, mdot_kg_per_s=mdot, name=body.name, recipe=recipe)
    log.info("placed consumer %s at %s (%.3f kg/s)",
             added["name"], body.node, mdot)
    return {"added": added}


@router.delete("/consumer/{consumer_id}", summary="Remove a consumer")
def remove_consumer(consumer_id: int) -> dict:
    """Remove by the sink element id reported in the frame's ``consumers``
    list. 404 unknown; 409 for the last remaining consumer."""
    app = get_app()
    try:
        removed = app.sim.remove_consumer(consumer_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"no consumer with id {consumer_id} (see /state)")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    log.info("removed %s %s", removed["kind"], removed["name"])
    return {"removed": removed}
