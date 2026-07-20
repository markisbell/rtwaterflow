"""Scenarios: save the configured live setup as a recipe file; load it back
(network + runtime consumer ops + sensor placement + the engine clock).

Replay is **tolerant per entry** (blueprint convention): a hand-edited file
that no longer matches the network skips the offending op with a warning —
non-atomic scenario load is an accepted blueprint pain point.

M0 recipe scope: name/description, network_id, consumer_ops, measurements,
engine clock. Controller/equipment blocks return with the M2 water assets
(tanks, pump stations, rule engine).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..scenarios import ScenarioStore
from .consumers import build_consumer_op
from .networks import apply_network
from .runtime import get_app, recording_meta, status_payload

log = logging.getLogger(__name__)

router = APIRouter(tags=["scenarios"])


class ScenarioSaveRequest(BaseModel):
    name: str
    description: str = ""


def _store(app) -> ScenarioStore:
    return ScenarioStore(app.settings.scenarios_dir)


@router.get("/scenarios", summary="Saved scenarios")
def scenarios_list() -> dict:
    """Saved scenario recipes (name, description, network, created)."""
    return {"scenarios": _store(get_app()).list()}


@router.post("/scenarios", summary="Save the live setup as a scenario")
def scenarios_save(req: ScenarioSaveRequest) -> dict:
    """Save the CURRENT live setup as a recipe: network id + runtime
    consumer ops + sensor placement + the engine clock. Recipes, not
    snapshots — same name overwrites."""
    if not req.name.strip():
        raise HTTPException(422, "scenario name must not be empty")
    app = get_app()
    sim = app.sim
    engine = app.engine

    doc = {
        "name": req.name.strip(),
        "description": req.description.strip(),
        "network_id": app.active.get("network_id", app.network_id),
        "consumer_ops": list(sim.consumer_ops),
        # sensor placement: meters are stored by consumer NAME (element ids
        # shift across replay; the consumer-op replay recreates the same
        # names deterministically), node sensors by node name.
        "measurements": {
            "preset": sim.measurements.preset,
            "mode": sim.measurements.mode,
            "consumer_meters": sorted(
                sim.index.consumer_names[i]
                for i in range(len(sim.index.consumers))
                if int(sim.index.consumers[i])
                in sim.measurements.consumer_meters),
            "node_sensors": sorted(sim.measurements.node_sensors),
        },
        "engine": {"day": engine.day, "step": engine.step,
                   "interval_seconds": engine.interval},
    }
    sid = _store(app).write(doc)
    log.info("saved scenario '%s' (%s)", req.name, sid)
    return {"id": sid, "name": doc["name"], "description": doc["description"],
            "network_id": doc["network_id"]}


@router.delete("/scenarios/{sid}", summary="Delete a scenario")
def scenarios_delete(sid: str) -> dict:
    if not _store(get_app()).delete(sid):
        raise HTTPException(404, f"unknown scenario '{sid}'")
    return {"deleted": sid}


@router.post("/scenarios/{sid}/load", summary="Load a scenario")
async def scenarios_load(sid: str) -> dict:
    """Replay a scenario recipe: network swap, then the runtime layers
    (consumer ops, sensor placement), seek to the stored clock and run.
    Tolerant per entry — mismatching ops are skipped with a warning, never a
    partial 500."""
    app = get_app()
    doc = _store(app).read(sid)
    if doc is None:
        raise HTTPException(404, f"unknown scenario '{sid}'")
    gid = doc.get("network_id")
    if not gid:
        raise HTTPException(409, f"scenario '{sid}' names no network")

    # 1) the network (the deterministic base)
    await apply_network(app, gid, "scenario")
    sim = app.sim

    # 2) runtime consumer ops, tolerant per entry
    for op in doc.get("consumer_ops", []):
        try:
            if op.get("op") == "add_consumer":
                mdot, recipe = build_consumer_op(app, op)
                sim.add_consumer(
                    node=op["node"], mdot_kg_per_s=mdot,
                    name=op.get("name"), recipe=recipe)
            elif op.get("op") == "remove_consumer":
                idx = sim.index
                pos = idx.consumer_names.index(op["name"])
                sim.remove_consumer(int(idx.consumers[pos]))
        except Exception:  # noqa: BLE001
            log.warning("scenario '%s': skipped consumer op %s", sid, op)

    # 3) sensor placement — after the consumer ops, so meters saved by name
    # find their replayed consumers. Explicit placement lists win (config,
    # faithfully restored); preset-only docs re-apply the preset.
    meas_doc = doc.get("measurements") or {}
    preset = meas_doc.get("preset")
    if "consumer_meters" in meas_doc or "node_sensors" in meas_doc:
        idx = sim.index
        sim.measurements.apply_preset("clear")
        for cname in meas_doc.get("consumer_meters", []):
            if cname in idx.consumer_names:
                pos = idx.consumer_names.index(cname)
                sim.measurements.add_consumer_meter(int(idx.consumers[pos]))
            else:
                log.warning("scenario '%s': no consumer '%s' for its "
                            "water meter", sid, cname)
        for node in meas_doc.get("node_sensors", []):
            if node in idx.junction:
                sim.measurements.add_node_sensor(node)
            else:
                log.warning("scenario '%s': no node '%s' for its pressure "
                            "sensor", sid, node)
        if preset:  # keep the label the placement was authored under
            sim.measurements.preset = preset
    elif preset:
        try:
            sim.measurements.apply_preset(preset)
        except ValueError:
            log.warning("scenario '%s': skipped measurement preset %s",
                        sid, preset)
    if meas_doc.get("mode"):
        try:
            sim.measurements.set_mode(meas_doc["mode"])
        except ValueError:
            log.warning("scenario '%s': skipped measurement mode %s",
                        sid, meas_doc["mode"])

    # 4) the engine clock, then run
    eng = doc.get("engine") or {}
    if eng.get("interval_seconds"):
        app.engine.set_interval(float(eng["interval_seconds"]))
    app.engine.seek_day(int(eng.get("day", 0)))
    app.engine.seek(int(eng.get("step", 0)))
    await app.engine.start()

    app.active.update(source="scenario", scenario=doc.get("name"))
    if app.settings.record and app.recorder is not None:
        # continuous operation: the swap auto-stopped the previous pack
        # (apply_network); the fully replayed scenario starts the next one
        app.recorder.start(recording_meta(app))
    # topology may have grown (replayed consumers) — rebuild for the reply
    from .runtime import build_topology
    topo = build_topology(app.network_id, sim)
    app.topology = topo
    log.info("loaded scenario '%s' onto %s", sid, gid)
    return {"status": status_payload(app), "active": app.active,
            "network": topo}
