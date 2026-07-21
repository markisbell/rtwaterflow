"""Supply endpoints: head sources, tanks and pump stations.

* ``GET /producers`` — inventory of head sources / PRVs / stations.
* ``GET /tanks`` — live tank states (level, volume, buffer time, alarms).
* ``GET /stations`` — pump stations with control config + operator mode.
* ``POST /station/{name}`` — operator override: ``auto`` (rule decides),
  ``on`` / ``off`` (forced past the hysteresis rule).
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..sensors import _r
from .runtime import App, get_app

log = logging.getLogger(__name__)

router = APIRouter(tags=["producers"])


def _producer_list(app: App) -> list[dict]:
    sim = app.sim
    net, idx = sim.net, sim.index
    out = []
    for meta in idx.producer_meta:
        # id = platform-unique pid; element indices collide across kinds
        entry = {"id": int(meta["pid"]), "kind": meta["kind"],
                 "name": meta["name"], "node": meta["node"]}
        if meta["kind"] == "slack":
            entry["p_bar"] = _r(net.ext_grid.at[meta["element"], "p_bar"])
        elif meta["kind"] == "prv":
            # configuration view (the wire carries the live telemetry)
            entry["from_node"] = meta.get("from_node")
            entry["p_set_bar"] = _r(net.press_control.at[
                meta["element"], "controlled_p_bar"])
        out.append(entry)
    return out


@router.get("/producers", summary="Head-source inventory")
def producers() -> list[dict]:
    """All head sources with their current configuration (live table values)."""
    return _producer_list(get_app())


@router.get("/tanks", summary="Tank states")
def tanks() -> list[dict]:
    """Live tank states: level, usable volume, buffer time at the current
    draw, and the alarm flags (overflow / empty / fire-reserve breached).
    ``id`` is the platform producer pid (joinable with /producers)."""
    sim = get_app().sim
    return [t.payload() for t in sim.tanks]


@router.get("/stations", summary="Pump stations")
def stations() -> list[dict]:
    """Pump stations: control configuration, operator mode and the live
    running state."""
    sim = get_app().sim
    out = []
    for st in sim.inputs.supply.stations:
        sname = st.name or f"station_{st.from_node}"
        meta = next(m for m in sim.index.producer_meta
                    if m["kind"] == "station" and m["name"] == sname)
        out.append({
            "name": sname,
            "from_node": st.from_node,
            "to_node": st.to_node,
            "control": st.control.model_dump(),
            "mode": sim.station_modes.get(sname, "auto"),
            "running": bool(sim.net.pump.at[meta["element"], "in_service"]),
            "curve": [list(p) for p in st.curve],
        })
    return out


class StationModeBody(BaseModel):
    mode: Literal["auto", "on", "off"]


@router.post("/station/{name}", summary="Override a station")
def set_station_mode(name: str, body: StationModeBody) -> dict:
    """Operator override for one pump station: ``auto`` hands control back
    to the hysteresis rule (manual-control stations return to their
    CONFIGURED running state); ``on``/``off`` force the state. Applied on
    the next tick (pre-solve, like every operating rule)."""
    sim = get_app().sim
    if name not in sim.station_modes:
        raise HTTPException(404, f"unknown station '{name}' (see /stations)")
    sim.station_modes[name] = body.mode
    log.info("station %s -> mode %s", name, body.mode)
    return {"name": name, "mode": body.mode}
