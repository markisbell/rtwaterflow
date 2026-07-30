"""Gamebridge: the co-simulation contract v1 surface for an external game clock.

Implements ``simgames/docs/contract/v1.md`` (the authoritative spec for all
``/gb/*`` traffic; mirrors rtheatflow's ``api/gamebridge.py`` — the closest
structural sibling). The game owns time; rtwaterflow never self-advances in
puppet mode (``RTWATERFLOW_EXTERNAL_CLOCK=true``). Divergence is data, not an
HTTP error — a failed solve is a ``status: "failed"`` step result with HTTP
200; the one 4xx in the step path is the out-of-order 409 (contract §0.3).

Surface (contract §1):

* ``GET  /gb/version``       — handshake (contract 1.1).
* ``POST /gb/net/reset``     — topology document → ``NetInputs`` built
  **directly from the pydantic models** of ``doc.native`` (the five-file
  bundle shape, verbatim — no temp-directory round-trip; the models +
  ``cross_validate`` ARE the five-file contract), ``engine.reconfigure``
  swap, JIT warmup solve (contract §0.5 / ADR-003).
* ``POST /gb/net/patch``     — device ops, tolerant per entry (contract §3.2).
* ``POST /gb/step`` + ``WS /gb/ws`` — one step request in, one contract step
  result out; identical semantics on both transports (contract §1).
* ``GET  /gb/result/latest`` — last result (404 before the first step).

Mapping decisions (documented here, mirrored in the tests; the game-side
WaterTopology builder is written against exactly this convention):

* **native.supply is MINIMAL** from the game: one ``ext_grid`` head supply
  (``p_bar`` 0.5 for a tower head — the junction's ``elevation_m`` already
  includes ``tower_height_m``, folded in game-side per contract §3.1; 4.0
  for a pressurized feed). Native MAY carry the full platform machinery
  (tanks/stations/wellfields) as fixed background — the platform runs it
  natively; gb devices never bind to it.
* **devices**: the HEAD source is FIRST in ``doc.devices`` (the slack-first
  convention shared with heat). Mapping:
  - ``water_tower`` → the platform's :class:`~rtwaterflow.assets.tank
    .WaterTank` machinery: the device's node gets a ``TankSpec`` (REPLACING
    any ext_grid supply entry there) with a SHALLOW basin independent of the
    tower height — area = ``volume_m3``/4 m, band 0.2..4.0 m, level_initial
    3.2 m — so pressure differences between towers come from
    ``tower_height_m`` alone (accepted but NOT re-added: the game already
    folded it into ``elevation_m``, contract §3.1). An EMPTY tower is a DEAD
    head (boundary collapsed ~0.05 bar above the lowest consumer junction —
    dry zones, not a weaker gravity feed; recovers with net inflow).
    Result: ``soc`` = level fraction of the band, ``detail.level_m``.
  - ``well``/``water_pump`` as the head (towerless net) → bind the first
    ext_grid supply; its ``p_bar`` stays fixed while alive; a disabled pump /
    a ``yield_factor`` of 0 collapses the head to ~0.05 bar so the Wagner
    PDD dries the zones (the game's "electric feed is dead" signal).
  - non-head ``well``/``water_pump`` → **source injections** at their
    junction (``pp.create_source``): well q = ``yield_factor × rated_m3_h``,
    pump q = ``rated_m3_h`` while enabled, 0 off.
  - ``slack`` → ext_grid supply at its node (bound, or synthesized with
    params ``p_bar`` default 4.0).
* **zones** map by consumer NAME in ``native`` (``doc.zones[].consumer``);
  ``zone_demand.value`` is m³/h → the consumer's engine-profile slot at the
  current tick (kg/s = m³/h ÷ 3.6 — the contract's nominal ρ = 1000 wire
  convention; the platform's internal physics keeps its density-true
  998.2 kg/m³). Sample-and-hold; zones never seen sit at 0.
* **supplied** = the Wagner PDD delivery fraction (delivered/demand) when
  PDA is enabled, else the v1 binary 1.0/0.0; zone detail ``{p_bar}``.
* **violations**: ``pressure_low`` for zones below their DVGW W 400-1
  minimum (reused from the M4 compliance table: 2.0 + 0.35 bar/storey above
  ground floor) — warning, critical below HALF the minimum; ``clamped`` for
  clamped setpoints/demands.
* **coupling_out**: per pump ``p_el_kw = ρ·g·Q·H/η / 1000`` ≥ 0 (draws
  while pumping, 0 off) — injection pumps use the rated head, the head-bound
  pump its actual solved feed.
* **weather.temp_c** is accepted and written into the environment series'
  current tick (``profiles.t_air_c``); it does NOT re-shape background
  archetype demand (that machinery bakes temperature at profile BUILD time,
  and game-driven zone rows are per-tick overrides anyway) — the honest
  reading of contract §4's "applied by backends that model weather-dependent
  physics".
* the estimation observer is **disabled** on a gb reset (the game consumes
  the contract layer, not the teaching view; keeps the step budget clean —
  re-enable any time via ``POST /estimation/config``).
* a gb reset auto-stops a running recording (a recording documents ONE
  configuration — same rule as ``/config/apply``).
* source injections are invisible to the native summary's feed/balance
  bookkeeping (it reads head sources only) — a documented cosmetic gap on
  the native wire; the contract result derives nothing from it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import ValidationError

import pandapipes as pp

from ..data_loader import DataContractError, cross_validate
from ..estimator import EstimationConfig
from ..models import (
    ConsumersFile,
    EnvironmentFile,
    NetworkStructure,
    PipesFile,
    SupplyFile,
)
from ..net_inputs import NetInputs
from .runtime import API_VERSION, App, build_topology, get_app

log = logging.getLogger(__name__)

router = APIRouter(prefix="/gb", tags=["gamebridge"])

CONTRACT_VERSION = "1.1"  # 1.1: water device rows (contract §3.1)

#: contract device kinds this (water) backend accepts (contract §3.1 table)
DEVICE_KINDS = ("slack", "well", "water_pump", "water_tower")

#: gb wire density convention: m³/h ↔ kg/s at the contract's nominal
#: ρ = 1000 kg/m³ (the game computes with ÷3.6; the platform's internal
#: physics keeps its density-true 998.2 — a documented 0.18 % convention gap)
M3H_PER_KG_S = 3.6
RHO_NOMINAL = 1000.0
G_M_S2 = 9.81

#: collapsed head pressure for a dead head source [bar] — low enough that
#: the Wagner PDD dries every zone, high enough to keep the solve off the
#: p=0 boundary. For a dead ELEVATED tower the collapse is referenced to
#: the LOWEST consumer junction (see _resolve_devices).
HEAD_COLLAPSED_BAR = 0.05

#: synthesized water_tower geometry (game-side acceptance pin): a SHALLOW
#: basin independent of tower height, so pressure differences between towers
#: come from ``tower_height_m`` alone (folded into the junction's
#: ``elevation_m`` game-side) — usable depth 4.0 m, area = volume_m3 / 4.0,
#: band 0.2..4.0 m, level_initial 3.2 m (80 %)
TOWER_DEPTH_M = 4.0
TOWER_LEVEL_MIN_M = 0.2
TOWER_LEVEL_MAX_M = 4.0
TOWER_LEVEL_INITIAL_M = 3.2

DEFAULT_SLACK_P_BAR = 4.0
DEFAULT_RATED_M3_H = 20.0
DEFAULT_HEAD_M = 30.0
DEFAULT_ETA = 0.6
DEFAULT_VOLUME_M3 = 100.0

_STATUS_MAP = {"ok": "converged", "degraded": "degraded", "failed": "failed"}

_NATIVE_KEYS = ("network_structure", "pipes", "consumers", "supply",
                "environment")


# --------------------------------------------------------------------- state

@dataclass
class GbDevice:
    """One game-controlled device and what it maps onto."""

    id: str
    kind: str                    # contract kind (slack/well/water_pump/water_tower)
    node: str | None
    params: dict
    #: "slack" (ext_grid, fixed) | "head_well"/"head_pump" (ext_grid with
    #: alive/collapse control) | "tank" (WaterTank) | "source" (injection)
    target: str
    element: int | None = None   # ext_grid or source element index
    tank_name: str | None = None
    base_p_bar: float = DEFAULT_SLACK_P_BAR   # head/slack boundary pressure
    yield_factor: float = 1.0    # well setpoint (sample-and-hold)
    enabled: bool = True         # water_pump setpoint (sample-and-hold)


@dataclass
class GbState:
    """Contract-session state, held on the runtime :class:`App`."""

    name: str
    steps_per_day: int
    zone_elements: dict[str, int]          # zone id -> sink element
    zone_names: dict[str, str]             # zone id -> consumer name (p_req key)
    zone_demand_m3h: dict[str, float]      # sample-and-hold, defaults 0.0
    devices: dict[str, GbDevice] = field(default_factory=dict)
    last_t: int | None = None              # idempotency/out-of-order cache …
    last_result: dict | None = None        # … (contract §0.3)


def _gb(app: App) -> GbState | None:
    return app.gb


# ---------------------------------------------------------------- validation

def _bad(msg: str) -> None:
    raise HTTPException(status_code=400, detail=msg)


async def _json_body(request: Request) -> Any:
    try:
        return json.loads(await request.body())
    except json.JSONDecodeError:
        _bad("body is not valid JSON")


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _as_int(v: Any) -> int | None:
    """JSON-semantics integer: a real int, or a float with zero fractional
    part (JSON Schema "integer"; e.g. Godot's JSON layer serializes every
    number as a float, so 96 arrives as 96.0). None if not an integer."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def _device_error(dev: Any, node_names: set[str]) -> str | None:
    """Structural check of one device spec (shared by reset and patch)."""
    if not isinstance(dev, dict):
        return "device must be an object"
    if not isinstance(dev.get("id"), str) or not dev["id"]:
        return "device needs a non-empty string 'id'"
    kind = dev.get("kind")
    if kind not in DEVICE_KINDS:
        return (f"device kind {kind!r} not supported by the water backend "
                f"(one of {DEVICE_KINDS})")
    if "params" in dev and dev["params"] is not None \
            and not isinstance(dev["params"], dict):
        return "'params' must be an object"
    node = dev.get("node")
    if not isinstance(node, str) or node not in node_names:
        return f"device {dev['id']!r}: 'node' must name a junction in native"
    return None


def _tower_tank_spec(dev: dict) -> dict:
    """Synthesize the TankSpec dict for a ``water_tower`` device.

    A SHALLOW basin whose geometry is independent of the tower height, so
    pressure differences between towers come from ``tower_height_m`` alone:
    area = volume_m3 / 4.0 m depth, band 0.2..4.0 m, level_initial 3.2 m.
    ``tower_height_m`` is deliberately NOT used hydraulically: the game
    already added it to the junction's ``elevation_m`` (contract §3.1 —
    adding it again would double-count the tower)."""
    params = dev.get("params") or {}
    volume = (float(params["volume_m3"]) if _is_num(params.get("volume_m3"))
              and params["volume_m3"] > 0 else DEFAULT_VOLUME_M3)
    # optional SoC replay (0..1 fraction of the usable band, clamped —
    # mirrors the wire's `soc`): the game restores a save / rebuilds the
    # topology without silently refilling the tower to 80 %
    level = TOWER_LEVEL_INITIAL_M
    if _is_num(params.get("soc")):
        frac = min(max(float(params["soc"]), 0.0), 1.0)
        level = TOWER_LEVEL_MIN_M + frac * (TOWER_LEVEL_MAX_M - TOWER_LEVEL_MIN_M)
    return {
        "node": dev["node"],
        "name": f"gb_{dev['id']}",
        "area_m2": round(volume / TOWER_DEPTH_M, 6),
        "level_min_m": TOWER_LEVEL_MIN_M,
        "level_max_m": TOWER_LEVEL_MAX_M,
        "level_initial_m": level,
        "kind": "durchlauf",
    }


def _validate_topology(doc: Any) -> tuple[NetInputs, int, list[dict],
                                          list[dict], list[GbDevice],
                                          list[str]]:
    """Full topology-document validation (contract §3.1) — everything is
    checked BEFORE the engine swap, so a bad document leaves the running
    network untouched. Returns ``(inputs, steps_per_day, zones, devices,
    device_records, warnings)`` or raises HTTP 400. Device records carry the
    supply-doc bindings; their runtime elements are resolved after the swap."""
    if not isinstance(doc, dict):
        _bad("topology document must be a JSON object")
    for key in ("contract", "network_kind", "name", "steps_per_day",
                "native", "zones", "devices"):
        if key not in doc:
            _bad(f"topology document missing required key '{key}'")
    major, _, _minor = str(doc["contract"]).partition(".")
    if major != "1":
        _bad(f"unsupported contract version {doc['contract']!r} "
             f"(this backend speaks {CONTRACT_VERSION})")
    if doc["network_kind"] != "water":
        _bad(f"network_kind {doc['network_kind']!r} — this backend serves 'water'")
    if not isinstance(doc["name"], str) or not doc["name"]:
        _bad("'name' must be a non-empty string")
    spd = _as_int(doc["steps_per_day"])
    if spd is None or not 24 <= spd <= 1440:
        _bad("'steps_per_day' must be an integer in [24, 1440]")
    doc["steps_per_day"] = spd  # canonical int downstream

    native = doc["native"]
    if not isinstance(native, dict):
        _bad("'native' must be an object (the rtwaterflow five-file bundle)")
    missing = [k for k in _NATIVE_KEYS if k not in native]
    if missing:
        _bad(f"'native' missing the five-file bundle document(s): {missing}")

    # per-document models FIRST (node/consumer names feed the device pass);
    # the merged supply doc (with device-synthesized entries) validates below
    try:
        structure = NetworkStructure.model_validate(native["network_structure"])
        pipes = PipesFile.model_validate(native["pipes"])
        consumers = ConsumersFile.model_validate(native["consumers"])
        environment = EnvironmentFile.model_validate(native["environment"])
    except ValidationError as exc:
        _bad(f"native bundle violates the five-file contract: {exc}")
    node_names = {j.name for j in structure.junctions}
    consumer_names = [c.name or f"consumer_{c.node}"
                      for c in consumers.consumers]

    # --- zones map by consumer NAME (contract §3.1 water notes) ---
    zones = doc["zones"]
    if not isinstance(zones, list):
        _bad("'zones' must be an array")
    seen: set[str] = set()
    for i, zone in enumerate(zones):
        if not isinstance(zone, dict) or not isinstance(zone.get("id"), str) \
                or not zone["id"]:
            _bad(f"zones[{i}] must be an object with a non-empty string 'id'")
        if zone["id"] in seen:
            _bad(f"duplicate zone id {zone['id']!r}")
        seen.add(zone["id"])
        consumer = zone.get("consumer")
        if not isinstance(consumer, str) or consumer not in consumer_names:
            _bad(f"zones[{i}] ({zone['id']!r}): 'consumer' must name a "
                 f"consumer in native (one of {consumer_names})")

    # --- devices: structural checks + supply-doc synthesis ---
    warnings: list[str] = []
    devices = doc["devices"]
    if not isinstance(devices, list):
        _bad("'devices' must be an array")
    supply_raw = native["supply"]
    if not isinstance(supply_raw, dict):
        _bad("'native.supply' must be an object")
    supplies: list[dict] = [dict(s) for s in supply_raw.get("supplies") or []
                            if isinstance(s, dict)]
    tanks: list[dict] = [dict(t) for t in supply_raw.get("tanks") or []
                         if isinstance(t, dict)]

    records: list[GbDevice] = []
    seen = set()
    for i, dev in enumerate(devices):
        err = _device_error(dev, node_names)
        if err:
            _bad(f"devices[{i}]: {err}")
        if dev["id"] in seen:
            _bad(f"duplicate device id {dev['id']!r}")
        seen.add(dev["id"])
        kind = dev["kind"]
        params = dict(dev.get("params") or {})
        node = dev["node"]
        is_head = not records  # FIRST device binds the head (slack-first)

        if kind == "water_tower":
            # tower → TankSpec at its node, REPLACING an ext_grid supply
            # entry there (the game seeds the head as ext_grid p_bar 0.5)
            spec = _tower_tank_spec(dev)
            supplies = [s for s in supplies if s.get("node") != node]
            tanks.append(spec)
            records.append(GbDevice(
                id=dev["id"], kind=kind, node=node, params=params,
                target="tank", tank_name=spec["name"]))
        elif kind == "slack":
            bound = next((s for s in supplies
                          if s.get("node") == node
                          and s.get("kind", "ext_grid") == "ext_grid"), None)
            if bound is None:
                bound = {"node": node, "kind": "ext_grid",
                         "p_bar": DEFAULT_SLACK_P_BAR,
                         "name": f"gb_{dev['id']}"}
                supplies.append(bound)
            if _is_num(params.get("p_bar")) and params["p_bar"] > 0:
                bound["p_bar"] = float(params["p_bar"])
            records.append(GbDevice(
                id=dev["id"], kind=kind, node=node, params=params,
                target="slack", base_p_bar=float(bound["p_bar"])))
        elif is_head:  # well/water_pump as the towerless HEAD source
            if not supplies:
                _bad(f"devices[{i}] ({dev['id']!r}): the head device needs "
                     "an ext_grid supply in native.supply to bind "
                     "(contract §3.1: a water network needs a pressure "
                     "boundary)")
            bound = supplies[0]
            if bound.get("node") != node:
                warnings.append(
                    f"head device {dev['id']!r} binds the ext_grid supply at "
                    f"node {bound.get('node')!r} (device said {node!r})")
            if _is_num(params.get("p_bar")) and params["p_bar"] > 0:
                bound["p_bar"] = float(params["p_bar"])
            records.append(GbDevice(
                id=dev["id"], kind=kind, node=node, params=params,
                target="head_well" if kind == "well" else "head_pump",
                base_p_bar=float(bound.get("p_bar") or DEFAULT_SLACK_P_BAR)))
        else:
            # non-head well/pump → source injection (created after the swap)
            records.append(GbDevice(
                id=dev["id"], kind=kind, node=node, params=params,
                target="source"))

    merged_supply = dict(supply_raw)
    merged_supply["supplies"] = supplies
    merged_supply["tanks"] = tanks
    try:
        supply = SupplyFile.model_validate(merged_supply)
        inputs = NetInputs(
            name=structure.name,
            structure=structure,
            pipes=pipes,
            consumers=consumers,
            supply=supply,
            environment=environment,
        )
        cross_validate(inputs)
    except (ValidationError, DataContractError) as exc:
        _bad(f"native bundle (with device-synthesized supply entries) "
             f"violates the five-file contract: {exc}")
    if inputs.environment.steps != spd or inputs.n_days != 1:
        _bad(f"the environment horizon in 'native' must be exactly "
             f"steps_per_day={spd} steps covering one day (contract §3.1); "
             f"got {inputs.environment.steps} steps over "
             f"{inputs.n_days} day(s)")
    return inputs, spd, zones, devices, records, warnings


# ------------------------------------------------------------ device helpers

def _rated_m3h(params: dict) -> float:
    v = params.get("rated_m3_h")
    return float(v) if _is_num(v) and v > 0 else DEFAULT_RATED_M3_H


def _eta(params: dict) -> float:
    v = params.get("eta")
    return float(v) if _is_num(v) and 0 < v <= 1 else DEFAULT_ETA


def _head_m(params: dict) -> float:
    v = params.get("head_m")
    return float(v) if _is_num(v) and v > 0 else DEFAULT_HEAD_M


def _pump_p_el_kw(q_m3h: float, head_m: float, eta: float) -> float:
    """ρ·g·Q·H/η in kW (contract §3.1 water rows; ρ nominal 1000, Q ≥ 0)."""
    q_m3s = max(0.0, q_m3h) / 3600.0
    return RHO_NOMINAL * G_M_S2 * q_m3s * head_m / max(eta, 0.05) / 1000.0


def _source_q_m3h(dev: GbDevice) -> float:
    """Commanded injection of a source-target well/pump [m³/h]."""
    if dev.kind == "well":
        return max(0.0, min(1.0, dev.yield_factor)) * _rated_m3h(dev.params)
    return _rated_m3h(dev.params) if dev.enabled else 0.0


def _write_source(sim, dev: GbDevice) -> None:
    if dev.element is not None and dev.element in sim.net.source.index:
        sim.net.source.at[dev.element, "mdot_kg_per_s"] = (
            _source_q_m3h(dev) / M3H_PER_KG_S)


def _write_head(sim, dev: GbDevice) -> None:
    """Alive/collapsed boundary pressure of a towerless head device."""
    if dev.element is None or dev.element not in sim.net.ext_grid.index:
        return
    if dev.target == "head_well":
        alive = min(1.0, max(0.0, dev.yield_factor)) > 0.0
    else:
        alive = bool(dev.enabled)
    sim.net.ext_grid.at[dev.element, "p_bar"] = (
        dev.base_p_bar if alive else HEAD_COLLAPSED_BAR)


def _resolve_devices(app: App, records: list[GbDevice]) -> None:
    """Bind the freshly built net's elements onto the device records
    (reset path, AFTER ``engine.reconfigure``): ext_grid elements for
    slack/head targets, WaterTank dead-head wiring for towers, and freshly
    created ``pp.create_source`` injections for non-head wells/pumps."""
    from ..network_builder import BAR_PER_M

    sim = app.engine.sim
    idx = sim.index
    slack_meta = [m for m in idx.producer_meta if m["kind"] == "slack"]
    eg_by_node = {m["node"]: m for m in slack_meta}
    elev = {j.name: float(j.elevation_m)
            for j in sim.inputs.structure.junctions}
    cons_elev = [elev[n] for n in idx.consumer_nodes if n in elev]
    min_elev = min(cons_elev) if cons_elev else min(elev.values())
    for dev in records:
        if dev.target in ("slack", "head_well", "head_pump"):
            # node match first; a head bound with a node-mismatch warning
            # falls back to the first plain ext_grid (if any survived the
            # tower supply-replacement — guarded, never a crash)
            meta = eg_by_node.get(dev.node) or (
                slack_meta[0] if slack_meta else None)
            if meta is None:
                continue  # boundary owned by a tank device — nothing to bind
            dev.element = int(meta["element"])
            if dev.target != "slack":
                _write_head(sim, dev)
        elif dev.target == "source":
            jj = idx.junction[dev.node]
            dev.element = int(pp.create_source(
                sim.net, junction=jj,
                mdot_kg_per_s=_source_q_m3h(dev) / M3H_PER_KG_S,
                name=f"gb_{dev.id}"))
        elif dev.target == "tank":
            # empty tank = DEAD head (game pin): at level_min the boundary
            # collapses to ~HEAD_COLLAPSED_BAR above the LOWEST consumer
            # junction — an empty ELEVATED tower must yield dry zones, and
            # only dropping the head below their elevation expresses "no
            # water" through a pressure boundary (the tower junction then
            # reads a negative boundary pressure; the M5 validity guard
            # honestly reports such frames "degraded"). Recovers as soon as
            # net inflow lifts the level off the floor.
            tank = sim._tanks_by_name.get(dev.tank_name)
            if tank is not None:
                tank.empty_head_p_bar = (
                    HEAD_COLLAPSED_BAR
                    + (min_elev - elev.get(dev.node, min_elev)) * BAR_PER_M)


# ------------------------------------------------------------------- version

@router.get("/version", summary="Co-simulation contract handshake")
def version() -> dict:
    """The game refuses to run on a contract MAJOR mismatch (contract §2)."""
    app = get_app()
    return {
        "contract": CONTRACT_VERSION,
        "backend": "rtwaterflow",
        "api": API_VERSION,
        "solver": f"pandapipes {pp.__version__}",
        "external_clock": bool(app.settings.external_clock),
    }


# --------------------------------------------------------------------- reset

@router.post("/net/reset", summary="Load a topology document (contract §3.1)")
async def net_reset(request: Request) -> dict:
    """Swap the engine onto the game's network: ``native`` five-file bundle
    (+ device-synthesized supply entries) → ``NetInputs`` →
    ``engine.reconfigure`` at the document's ``steps_per_day`` tick raster
    (contract ticks are engine ticks 1:1), then one throwaway warmup solve so
    numba JIT never lands on a live step (contract §0.5) — unwound via
    ``reset_operations`` so tank/aquifer state starts pristine.

    Clears ``last_t`` — the next step may carry any ``t`` (contract §3.1)."""
    app = get_app()
    doc = await _json_body(request)
    inputs, spd, zones, _devices, records, warnings = _validate_topology(doc)

    # a recording documents ONE configuration (same rule as /config/apply)
    if app.recorder is not None:
        await asyncio.to_thread(app.recorder.stop)

    engine = app.engine
    # the game's tick raster: steps_per_day 96 = 15-min ticks. The engine and
    # the new Simulator both read steps_per_day from the engine's settings.
    engine.settings = app.settings.model_copy(update={"steps_per_day": spd})
    engine.steps_per_day = spd
    await engine.reconfigure(inputs)
    # the game consumes the contract layer; the forward observer is a
    # teaching view and would eat into the step budget — off (documented;
    # POST /estimation/config re-enables at any time)
    engine.set_est_config(EstimationConfig(enabled=False))
    sim = engine.sim

    # game-controlled devices: bind elements / create source injections
    _resolve_devices(app, records)
    gb_devices = {r.id: r for r in records}

    # zones map by consumer NAME in native (contract §3.1)
    idx = sim.index
    zone_elements = {
        z["id"]: int(idx.consumers[idx.consumer_names.index(z["consumer"])])
        for z in zones
    }
    app.gb = GbState(
        name=str(doc["name"]),
        steps_per_day=spd,
        zone_elements=zone_elements,
        zone_names={z["id"]: z["consumer"] for z in zones},
        zone_demand_m3h={z["id"]: 0.0 for z in zones},
        devices=gb_devices,
    )

    # native /state, /network etc. keep working on the swapped net
    app.network_id = str(doc["name"])
    app.topology = build_topology(app.network_id, sim)
    app.loaded_at = time.time()
    app.active = {
        "network_id": app.network_id,
        "name": inputs.name,
        "source": "gamebridge",
        "applied_at": app.loaded_at,
        "n_consumers": len(inputs.consumers.consumers),
        "n_days": inputs.n_days,
    }

    if engine.running:
        warnings.append(
            "internal clock is running — pause it (or set "
            "RTWATERFLOW_EXTERNAL_CLOCK=true) before stepping via /gb")

    # JIT warmup (contract §0.5): one throwaway solve, no clock advance, no
    # publish — engine counters stay at step 0 / day 0. run_step integrates
    # tank levels / aquifer state, so the throwaway is unwound afterwards
    # (reset_operations is the platform's deterministic-replay normalizer);
    # the warm-start pressures remain — that IS the warmup's purpose.
    t0 = time.perf_counter()
    warm = await asyncio.to_thread(sim.run_step, 0, 0)
    warmup_ms = (time.perf_counter() - t0) * 1000.0
    sim.reset_operations()
    if warm.solver_status != "ok":
        warnings.append(
            f"warmup solve ended {warm.solver_status!r} (placeholder demand "
            "at tick 0) — throwaway, first live step re-solves from a clean "
            "init")

    log.info("gb reset: network %r, %d zones, %d devices, warmup %.0f ms",
             app.network_id, len(zone_elements), len(gb_devices), warmup_ms)
    return {
        "ok": True,
        "network_kind": "water",
        "n_zones": len(zone_elements),
        "n_devices": len(gb_devices),
        "warmup_solve_ms": round(warmup_ms, 3),
        "warnings": warnings,
    }


# --------------------------------------------------------------------- patch

@router.post("/net/patch", summary="Device ops (contract §3.2, tolerant per entry)")
async def net_patch(request: Request) -> dict:
    """``add_device`` / ``remove_device`` / ``set_device``. Tolerant per
    entry: applied ops stay applied even if later ops fail (contract §3.2).
    v1 water scope: non-head wells/pumps (source injections) are the
    patchable kinds — pressure boundaries (slack/tower/head) are built at
    reset and need a full ``/gb/net/reset``."""
    app = get_app()
    gb = _gb(app)
    if gb is None:
        _bad("no gb network loaded — POST /gb/net/reset first")
    ops = await _json_body(request)
    if not isinstance(ops, list):
        _bad("patch body must be a JSON array of ops")
    applied: list[str] = []
    errors: list[dict] = []
    for i, op in enumerate(ops):
        try:
            applied.append(_apply_op(app, gb, op))
        except ValueError as exc:
            errors.append({"index": i, "error": str(exc)})
    return {"applied": applied, "errors": errors}


def _apply_op(app: App, gb: GbState, op: Any) -> str:
    sim = app.engine.sim
    if not isinstance(op, dict):
        raise ValueError("op must be an object")
    name = op.get("op")
    if name == "add_device":
        dev = op.get("device")
        err = _device_error(dev, set(sim.index.junction))
        if err:
            raise ValueError(err)
        if dev["id"] in gb.devices:
            raise ValueError(f"duplicate device {dev['id']}")
        if dev["kind"] not in ("well", "water_pump"):
            raise ValueError(
                f"device {dev['id']}: kind {dev['kind']!r} is a pressure "
                "boundary — add it via a full /gb/net/reset")
        record = GbDevice(
            id=dev["id"], kind=dev["kind"], node=dev["node"],
            params=dict(dev.get("params") or {}), target="source")
        jj = sim.index.junction[record.node]
        record.element = int(pp.create_source(
            sim.net, junction=jj,
            mdot_kg_per_s=_source_q_m3h(record) / M3H_PER_KG_S,
            name=f"gb_{record.id}"))
        sim._reset_initialization()  # topology CRUD → cold init
        gb.devices[record.id] = record
        return record.id
    if name == "remove_device":
        did = op.get("id")
        if not isinstance(did, str) or did not in gb.devices:
            raise ValueError(f"unknown device {did}")
        dev = gb.devices[did]
        if dev.target != "source":
            raise ValueError(
                f"cannot remove {did}: it is bound to a pressure boundary "
                "(swap networks via /gb/net/reset instead)")
        if dev.element is not None and dev.element in sim.net.source.index:
            sim.net.source.drop(index=dev.element, inplace=True)
            if "res_source" in sim.net and len(sim.net.res_source):
                sim.net.res_source.drop(index=dev.element, inplace=True,
                                        errors="ignore")
        sim._reset_initialization()
        del gb.devices[did]
        return did
    if name == "set_device":
        did = op.get("id")
        if not isinstance(did, str) or did not in gb.devices:
            raise ValueError(f"unknown device {did}")
        params = op.get("params")
        if not isinstance(params, dict):
            raise ValueError("set_device needs a 'params' object")
        dev = gb.devices[did]
        dev.params.update(params)
        if dev.target == "source":
            _write_source(sim, dev)
        elif dev.target in ("slack", "head_well", "head_pump"):
            if _is_num(params.get("p_bar")) and params["p_bar"] > 0:
                dev.base_p_bar = float(params["p_bar"])
            if dev.target == "slack" and dev.element is not None \
                    and dev.element in sim.net.ext_grid.index:
                sim.net.ext_grid.at[dev.element, "p_bar"] = dev.base_p_bar
            else:
                _write_head(sim, dev)
        return did
    raise ValueError(f"unknown op {name!r}")


# ------------------------------------------------------------------ stepping

async def _gb_step(app: App, req: Any) -> tuple[int, dict]:
    """Shared step logic for HTTP ``POST /gb/step`` and ``WS /gb/ws``.

    Returns ``(http_status, payload)``: 200 with the contract step result,
    400 with ``{detail}``, or 409 with the out-of-order error frame
    (contract §4)."""
    gb = _gb(app)
    if gb is None:
        return 400, {"detail": "no gb network loaded — POST /gb/net/reset first"}
    if not isinstance(req, dict):
        return 400, {"detail": "step request must be a JSON object"}
    t = _as_int(req.get("t"))
    if t is None or t < 0:
        return 400, {"detail": "step request needs an integer 't' >= 0"}

    if gb.last_t is not None:
        if t == gb.last_t:
            # idempotent re-send: cached result, no re-solve (contract §0.3)
            return 200, gb.last_result  # type: ignore[return-value]
        if t != gb.last_t + 1:
            return 409, {"t": t, "status": "error", "error": "out_of_order",
                         "expected": [gb.last_t, gb.last_t + 1]}

    engine = app.engine
    sim = engine.sim
    violations: list[dict] = []
    tick = sim._tick(engine.step, engine.day)

    # --- weather BEFORE the solve: temp_c lands in the environment series'
    # current tick (visible on the native wire); it does not re-shape the
    # background archetype demand — profile temperature coupling is a
    # BUILD-time machinery, and game-driven zones are per-tick overrides
    # anyway (documented mapping decision).
    weather = req.get("weather") or {}
    if isinstance(weather, dict) and _is_num(weather.get("temp_c")) \
            and tick < len(sim.profiles.t_air_c):
        sim.profiles.t_air_c[tick] = float(weather["temp_c"])

    # --- zone demand: m³/h → kg/s (÷3.6) into the consumer's engine-profile
    # slot at the current tick; sample-and-hold, never-seen zones 0
    # (contract §4 — zero-demand sinks are legal water hydraulics).
    for zid, entry in (req.get("zone_demand") or {}).items():
        if zid not in gb.zone_demand_m3h or not isinstance(entry, dict):
            continue
        value = entry.get("value")
        if not _is_num(value):
            continue
        if value < 0:
            violations.append({"element": f"zone:{zid}", "kind": "clamped",
                               "severity": "info", "value": float(value)})
            value = 0.0
        gb.zone_demand_m3h[zid] = float(value)
    p, idx = sim.profiles, sim.index
    for zid, el in gb.zone_elements.items():
        pos = np.nonzero(idx.consumers == el)[0]
        if not len(pos):
            continue  # consumer removed through the native API — skip
        p.mdot_kg_per_s[int(pos[0]), tick] = (
            gb.zone_demand_m3h[zid] / M3H_PER_KG_S)

    # --- device setpoints (contract §3.1 water rows): well yield_factor
    # scales the injection / gates the head; water_pump enabled drives the
    # injection / collapses the head. Sample-and-hold.
    for did, sp in (req.get("device_setpoints") or {}).items():
        dev = gb.devices.get(did)
        if dev is None or not isinstance(sp, dict):
            continue
        if dev.kind == "well" and _is_num(sp.get("yield_factor")):
            yf = float(sp["yield_factor"])
            if not 0.0 <= yf <= 1.0:
                violations.append({
                    "element": f"device:{did}", "kind": "clamped",
                    "severity": "info", "value": yf})
                yf = min(1.0, max(0.0, yf))
            dev.yield_factor = yf
        elif dev.kind == "water_pump" and isinstance(sp.get("enabled"), bool):
            dev.enabled = sp["enabled"]
        if dev.target == "source":
            _write_source(sim, dev)
        elif dev.target in ("head_well", "head_pump"):
            _write_head(sim, dev)
    # coupling_in routes onto coupling_load devices — a power-network device
    # kind; the water backend has none, the key is accepted and ignored.

    try:
        result = await engine.external_step()
    except RuntimeError as exc:  # internal clock running — two clocks never race
        return 409, {"detail": str(exc)}

    contract_result = _build_result(app, gb, t, result, violations)
    gb.last_t, gb.last_result = t, contract_result
    return 200, contract_result


def _build_result(app: App, gb: GbState, t: int, result,
                  violations: list[dict]) -> dict:
    """StepResult → contract step result (contract §4).

    On failed frames the platform reuses the last converged physics payload;
    the contract mirrors that — device values may be stale, but
    ``status: "failed"`` and ``supplied: 0.0`` carry the gameplay signal."""
    sim = app.engine.sim
    if result is None:  # never-crash tick dropped the frame
        status, solve_ms = "failed", 0.0
        consumers, producers = [], []
    else:
        status = _STATUS_MAP.get(result.solver_status, "failed")
        solve_ms = float(result.solve_ms or 0.0)
        consumers = result.consumers or []
        producers = result.producers or []

    cons_by_el = {c["id"]: c for c in consumers}
    prod_by_node = {m["node"]: m for m in producers if m["kind"] == "slack"}
    failed = status == "failed"
    pda_on = bool(sim.pda.enabled)

    # --- zones: supplied = the Wagner PDD delivery fraction when PDA is on
    # (contract §3.1 water notes: weak taps before dry taps), else the v1
    # binary converged→1.0 / failed→0.0; quality detail {p_bar}. pressure_low
    # below the W 400-1 minimum from the M4 compliance table (warning;
    # critical below half the minimum).
    zones_out: dict[str, dict] = {}
    for zid, el in gb.zone_elements.items():
        entry = cons_by_el.get(el)
        detail: dict = {}
        supplied = 0.0 if failed else 1.0
        if entry is not None:
            detail = {"p_bar": entry.get("p_bar")}
            if not failed and pda_on:
                demand = entry.get("mdot_demand_kg_per_s")
                delivered = entry.get("mdot_kg_per_s")
                if demand and demand > 1e-12 and delivered is not None:
                    supplied = min(1.0, max(0.0, delivered / demand))
        zones_out[zid] = {"supplied": round(supplied, 6), "detail": detail}
        if entry is not None and not failed:
            p_bar = entry.get("p_bar")
            p_req = sim.compliance.p_req.get(gb.zone_names.get(zid))
            if p_bar is not None and p_req and p_bar < p_req:
                violations.append({
                    "element": f"zone:{zid}", "kind": "pressure_low",
                    "severity": ("critical" if p_bar < 0.5 * p_req
                                 else "warning"),
                    "value": p_bar})

    # --- devices + coupling_out (contract §3.1 water rows) ---
    devices_out: dict[str, dict] = {}
    coupling_out: dict[str, dict] = {}
    for did, dev in gb.devices.items():
        if dev.target == "tank":
            tank = sim._tanks_by_name.get(dev.tank_name)
            if tank is None:
                devices_out[did] = {"output_kw": None, "soc": None,
                                    "detail": {}}
                continue
            band = tank.level_max_m - tank.level_min_m
            soc = ((tank.level_m - tank.level_min_m) / band if band > 0
                   else 0.0)
            devices_out[did] = {
                "output_kw": None,
                "soc": round(min(1.0, max(0.0, soc)), 6),
                "detail": {"level_m": round(tank.level_m, 4)},
            }
        elif dev.target == "source":
            q = _source_q_m3h(dev)
            detail: dict = {"q_m3h": round(q, 6)}
            devices_out[did] = {"output_kw": None, "soc": None,
                                "detail": detail}
            if dev.kind == "water_pump":
                p_el = _pump_p_el_kw(q, _head_m(dev.params), _eta(dev.params))
                detail["p_el_kw"] = round(p_el, 6)
                coupling_out[did] = {"p_el_kw": round(p_el, 6)}
        else:  # slack / head_well / head_pump — the bound ext_grid boundary
            prod = prod_by_node.get(dev.node) or (
                producers[0] if producers else None)
            mdot = (prod or {}).get("mdot_kg_per_s")
            q_m3h = (float(mdot) * M3H_PER_KG_S) if mdot is not None else 0.0
            detail = {"q_m3h": round(q_m3h, 6),
                      "p_bar": (prod or {}).get("p_bar")}
            devices_out[did] = {"output_kw": None, "soc": None,
                                "detail": detail}
            if dev.kind == "water_pump":
                # head-bound pump: electric draw from its ACTUAL feed
                q_eff = max(0.0, q_m3h) if dev.enabled and not failed else 0.0
                p_el = _pump_p_el_kw(q_eff, _head_m(dev.params),
                                     _eta(dev.params))
                detail["p_el_kw"] = round(p_el, 6)
                coupling_out[did] = {"p_el_kw": round(p_el, 6)}

    return {
        "t": t,
        "status": status,
        "solve_ms": round(solve_ms, 3),
        "zones": zones_out,
        "devices": devices_out,
        "coupling_out": coupling_out,
        "violations": violations,
    }


@router.post("/step", summary="Advance one step under the external clock")
async def step(request: Request):
    """One §4 step request → one contract step result. Idempotent re-send of
    ``last_t`` returns the cached result; any other ``t`` ≠ ``last_t + 1`` is
    the one 4xx in the step path (409, contract §0.3). Debug fallback for the
    WebSocket step channel — identical behavior."""
    app = get_app()
    req = await _json_body(request)
    status, payload = await _gb_step(app, req)
    if status == 200:
        return payload
    return JSONResponse(status_code=status, content=payload)


@router.websocket("/ws")
async def step_ws(websocket: WebSocket) -> None:
    """Step channel (contract §1): one text frame in = one §4 step request,
    one text frame out = the step result. Strictly sequential; out-of-order
    ``t`` yields a ``status: "error"`` frame, other rejections a
    ``bad_request`` error frame — the socket stays open."""
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                req = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps(
                    {"status": "error", "error": "bad_request",
                     "detail": "frame is not valid JSON"}))
                continue
            app = get_app()
            status, payload = await _gb_step(app, req)
            if isinstance(payload, dict) and "status" in payload:
                # a step result (200) or the out-of-order error frame (409)
                await websocket.send_text(json.dumps(payload))
            else:
                t = req.get("t") if isinstance(req, dict) else None
                await websocket.send_text(json.dumps(
                    {"t": t, "status": "error", "error": "bad_request",
                     "detail": payload.get("detail")}))
    except WebSocketDisconnect:
        pass
    except Exception:  # broken transport — same as a disconnect
        log.debug("gb ws receive loop ended abnormally", exc_info=True)


# -------------------------------------------------------------------- latest

@router.get("/result/latest", summary="Last step result (crash recovery)")
def result_latest() -> dict:
    """The last contract step result; 404 before the first step. Together
    with idempotent re-send this is the crash-recovery path (contract §4)."""
    gb = _gb(get_app())
    if gb is None or gb.last_result is None:
        raise HTTPException(status_code=404, detail="no step result yet")
    return gb.last_result
