"""Sensor placement endpoints and the estimation policy.

The measurable layer's CRUD: water meters (Wasserzähler) at consumers,
pressure sensors (Drucksensoren) at nodes, the bulk fidelity mode
(``full``/``standard``) and the placement presets. Every verb returns the
fresh placement payload (placement + coverage), so the UI panel and the map
markers re-sync from the response — the blueprint convention.

``GET/POST /estimation/config`` configures the estimation policy (M7): the
forward observer's ``enabled`` / ``prior_basis`` / ``throttle_factor``. The
policy is an operator setting — it survives grid swaps (held on the engine).

Error discipline: 404 unknown element / no device to remove, 422 invalid
mode/preset (pydantic ``Literal``/bounds).
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..estimator import EstimationConfig
from .runtime import App, get_app

log = logging.getLogger(__name__)

router = APIRouter(tags=["measurements"])


def _placement(app: App) -> dict:
    """Placement + coverage + the strict-mode hint the panel shows."""
    return {
        **app.sim.measurement_placement(),
        "expose_ground_truth": bool(app.store.expose_ground_truth),
    }


@router.get("/measurements", summary="Sensor placement + coverage")
def measurements() -> dict:
    """Which consumers carry a water meter, which nodes a pressure sensor,
    the fidelity mode, and coverage fractions per element class. Source SCADA
    is always measured (real waterworks are) and does not appear as a
    placement."""
    return _placement(get_app())


@router.post("/measurements/consumer/{consumer_id}",
             summary="Place a water meter")
def add_consumer_meter(consumer_id: int) -> dict:
    """Install a Wasserzähler at the consumer. In standard mode the new
    meter starts cold: readings stay null until its first 15-minute window
    closes."""
    app = get_app()
    sim = app.sim
    if int(consumer_id) not in {int(c) for c in sim.index.consumers}:
        raise HTTPException(404, f"unknown consumer {consumer_id}")
    sim.measurements.add_consumer_meter(int(consumer_id))
    return _placement(app)


@router.delete("/measurements/consumer/{consumer_id}",
               summary="Remove a water meter")
def remove_consumer_meter(consumer_id: int) -> dict:
    app = get_app()
    if not app.sim.measurements.remove_consumer_meter(int(consumer_id)):
        raise HTTPException(
            404, f"no water meter at consumer {consumer_id}")
    return _placement(app)


@router.post("/measurements/node/{node_id}", summary="Place a pressure sensor")
def add_node_sensor(node_id: str) -> dict:
    """Install a pressure sensor at a node: it reads ``p_bar`` at that
    node's junction."""
    app = get_app()
    sim = app.sim
    if node_id not in sim.index.junction:
        raise HTTPException(404, f"unknown node '{node_id}'")
    sim.measurements.add_node_sensor(node_id)
    return _placement(app)


@router.delete("/measurements/node/{node_id}",
               summary="Remove a pressure sensor")
def remove_node_sensor(node_id: str) -> dict:
    app = get_app()
    if not app.sim.measurements.remove_node_sensor(node_id):
        raise HTTPException(404, f"no pressure sensor at node '{node_id}'")
    return _placement(app)


class ModeBody(BaseModel):
    mode: Literal["full", "standard"]


@router.post("/measurements/mode", summary="Set the meter fidelity mode")
def set_mode(body: ModeBody) -> dict:
    """Bulk fidelity switch for every placed device: ``full`` = every channel
    every step; ``standard`` = 15-min-window means aligned to simulated time,
    null until the first window closes (honest cold start — the window state
    resets on every switch). Source SCADA stays live either way."""
    app = get_app()
    app.sim.measurements.set_mode(body.mode)
    log.info("measurement mode -> %s", body.mode)
    return _placement(app)


class PresetBody(BaseModel):
    preset: Literal["all_consumers", "scada", "plant_only",
                    "key_points", "clear"]


@router.post("/measurements/preset", summary="Apply a placement preset")
def set_preset(body: PresetBody) -> dict:
    """Replace the placement wholesale: ``all_consumers`` (meter at every
    consumer — the default), ``plant_only`` (source pressure only),
    ``key_points`` (source + net ends + a meter at the currently known worst
    point), ``clear`` (no devices — the operator flies blind)."""
    app = get_app()
    app.sim.measurements.apply_preset(body.preset)
    log.info("measurement preset -> %s", body.preset)
    return _placement(app)


# ---------------------------------------------------------------------------
# Estimation policy (M7): the forward observer's knobs
# ---------------------------------------------------------------------------

class EstimationBody(BaseModel):
    """Partial update of the estimation policy (422 outside the bounds)."""

    enabled: bool | None = None
    prior_basis: Literal["archetype", "design"] | None = Field(
        default=None,
        description="unmetered-consumer expectation: 'archetype' (the "
                    "expected demand profile) or 'design' (flat base demand)")
    throttle_factor: float | None = Field(
        default=None, ge=0.0, le=20.0,
        description="wall-clock self-throttle: a new estimate only after "
                    "throttle_factor × its own runtime has elapsed")


def _estimation_payload(app: App) -> dict:
    cfg = app.sim.est_config
    obs = app.sim._observer
    return {
        **cfg.as_dict(),
        "seq": obs.seq if obs is not None else 0,
        "last_solve_ms": obs._ms if obs is not None else None,
    }


@router.get("/estimation/config", summary="Estimation policy")
def get_estimation_config() -> dict:
    """The estimation policy (enabled / prior basis / throttle) plus the
    current estimate sequence number and last-solve runtime. M7: the forward
    observer produces the ``estimated`` layer; ``enabled`` defaults to true."""
    return _estimation_payload(get_app())


@router.post("/estimation/config", summary="Configure the estimation")
def set_estimation_config(body: EstimationBody) -> dict:
    """Partial update. The policy survives grid swaps and scenario loads
    (held on the engine). The forward observer (M7) refreshes the estimate
    on converged frames per the metering raster + self-throttle."""
    app = get_app()
    cur = app.sim.est_config
    cfg = EstimationConfig(
        enabled=cur.enabled if body.enabled is None else bool(body.enabled),
        prior_basis=(cur.prior_basis if body.prior_basis is None
                     else str(body.prior_basis)),
        throttle_factor=(cur.throttle_factor if body.throttle_factor is None
                         else float(body.throttle_factor)),
    )
    app.engine.set_est_config(cfg)
    log.info("estimation config -> %s", cfg)
    return _estimation_payload(app)
