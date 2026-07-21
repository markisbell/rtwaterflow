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
        "stations": dict(sim.station_modes),   # operator overrides (config)
        "environment": sim.environment.as_dict(),   # M3 weather overrides
        # M5 pressure-dependent hydraulics: the PDA toggle, the background
        # leakage coefficient, and the live hydrants/bursts (re-opened with
        # a fresh duration on load — recipes keep configuration, not the
        # remaining countdown)
        "hydraulics": {
            "pda_enabled": sim.pda.enabled,
            "leak_coefficient_per_km": sim.leak_coefficient_per_km,
            "hydrants": [
                {"node": e.node, "target_m3_h": e.target_m3_h,
                 "name": e.name, "duration_ticks": e.duration_ticks}
                for e in sim.emitters.emitters.values()
                if e.kind == "hydrant"],
            "bursts": [
                {"node": e.node, "coefficient": e.coefficient, "name": e.name}
                for e in sim.emitters.emitters.values() if e.kind == "burst"],
        },
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

    # 1b) station operator modes (config, tolerant per entry)
    for sname, mode in (doc.get("stations") or {}).items():
        if sname in sim.station_modes and mode in ("auto", "on", "off"):
            sim.station_modes[sname] = mode
        elif sname not in sim.station_modes:
            log.warning("scenario '%s': no station '%s' for its mode", sid,
                        sname)

    # 1c) environment overrides (M3 weather knob — config, tolerant)
    env = doc.get("environment") or {}
    if env:
        try:
            sim.set_environment(
                t_offset_c=float(env.get("t_offset_c") or 0.0),
                dryness_override=env.get("dryness_override"))
        except Exception:  # noqa: BLE001
            log.warning("scenario '%s': skipped environment overrides %s",
                        sid, env)

    # 1d) M5 pressure-dependent hydraulics (config; tolerant PER ENTRY —
    # a single stale emitter must not abort the rest of the block, matching
    # the consumer-ops replay discipline). A saved hydrant re-opens with a
    # FRESH countdown of its original duration.
    hyd = doc.get("hydraulics") or {}
    if "pda_enabled" in hyd:
        try:
            sim.set_pda(bool(hyd["pda_enabled"]))
        except Exception:  # noqa: BLE001
            log.warning("scenario '%s': skipped pda flag", sid)
    if hyd.get("leak_coefficient_per_km"):
        try:
            sim.set_leakage(float(hyd["leak_coefficient_per_km"]))
        except Exception:  # noqa: BLE001
            log.warning("scenario '%s': skipped leakage", sid)
    for h in hyd.get("hydrants", []):
        try:
            sim.open_hydrant(node=h["node"],
                             target_m3_h=float(h["target_m3_h"]),
                             duration_ticks=h.get("duration_ticks"),
                             name=h.get("name"))
        except Exception:  # noqa: BLE001
            log.warning("scenario '%s': skipped hydrant %s", sid, h)
    for b in hyd.get("bursts", []):
        try:
            sim.emitters.add(
                b.get("name") or f"Rohrbruch {b['node']}", b["node"],
                "burst", float(b["coefficient"]), exponent=0.5,
                start_tick=sim._abs_tick, duration_ticks=None)
        except Exception:  # noqa: BLE001
            log.warning("scenario '%s': skipped burst %s", sid, b)

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
