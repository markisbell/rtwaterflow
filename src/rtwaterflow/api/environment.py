"""Environment endpoints — the M3 weather knob.

* ``GET /environment`` — bundle drivers + the active runtime overrides.
* ``POST /environment`` — set/clear overrides (temperature offset on the
  bundle's series, dryness override); the demand engine rebuilds the
  archetype profiles immediately. Configuration, not physics state —
  scenario recipes save and replay it.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..sensors import _r
from .runtime import get_app

log = logging.getLogger(__name__)

router = APIRouter(tags=["environment"])


def _payload(app) -> dict:
    sim = app.sim
    env_file = sim.inputs.environment
    engine = app.engine
    tick = sim._tick(engine.step, engine.day)
    return {
        **sim.environment.as_dict(),
        "t_air_now_c": _r(float(sim.profiles.t_air_c[tick]), 2),
        "season_day_of_year": env_file.season_day_of_year,
        "has_day_types": env_file.day_types is not None,
        "has_dryness": env_file.dryness is not None,
        "legacy_demand_factor": env_file.demand_factor is not None,
        "n_profiled_consumers": sum(
            1 for c in sim.inputs.consumers.consumers if c.size is not None),
    }


@router.get("/environment", summary="Environment drivers + overrides")
def environment() -> dict:
    """Bundle environment drivers and the active runtime overrides."""
    return _payload(get_app())


class EnvironmentBody(BaseModel):
    """Partial update: absent fields keep their current value. Clearing
    the dryness override back to the bundle's series requires
    ``clear_dryness: true`` (an explicit ``dryness: null`` is treated as
    absent, i.e. a no-op)."""

    t_offset_c: Optional[float] = Field(default=None, ge=-30.0, le=30.0)
    dryness: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    clear_dryness: bool = False


@router.post("/environment", summary="Set weather overrides")
def set_environment(body: EnvironmentBody) -> dict:
    """Apply runtime weather overrides (Hitzetag: ``t_offset_c`` +
    ``dryness`` 1.0). The demand engine rebuilds the archetype profiles
    from the next tick; legacy demand_factor bundles only shift their
    displayed temperature."""
    app = get_app()
    kwargs: dict = {}
    if body.t_offset_c is not None:
        kwargs["t_offset_c"] = body.t_offset_c
    if body.clear_dryness:
        kwargs["dryness_override"] = None
    elif body.dryness is not None:
        kwargs["dryness_override"] = body.dryness
    try:
        app.sim.set_environment(**kwargs)
    except Exception as exc:  # never-500: racing CRUD may poison one rebuild
        log.exception("environment rebuild failed")
        raise HTTPException(
            409, f"environment rebuild failed: {type(exc).__name__}: {exc}")
    log.info("environment overrides -> %s", app.sim.environment.as_dict())
    return _payload(app)
