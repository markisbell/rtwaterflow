"""Producer endpoints — M0: read-only head-source inventory.

* ``GET /producers`` — the head sources (M0: exactly one ``slack`` = the
  fixed-pressure ext_grid, e.g. a Hochbehälter water surface).

Source CRUD (wells, pump stations, tanks with level controllers) arrives in
M2 of the roadmap as new water code; the fork parent's thermal producer CRUD
(heat_exchanger/pump_mass placement, plant dispatch models) was removed in M0.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

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
