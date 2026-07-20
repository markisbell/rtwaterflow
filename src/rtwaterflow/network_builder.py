"""Build-once network construction (single pipe layer, water hydraulics).

``build_network(inputs, ...)`` constructs the pandapipes net **once** per
scenario load and returns ``(net, ProfileArrays)``. Each simulation tick only
overwrites element values from the dense profile arrays, solves, and reads
results — the net is never rebuilt per step.

Conventions (all binding, see IMPLEMENTATION_ROADMAP.md):

* **Single layer**: one node from ``network_structure.json`` = one pandapipes
  junction; one entry in ``pipes.json`` = one pipe. (The district-heating fork
  parent expanded every node into a supply/return pair — deleted in M0.)
* **Elevation** goes into pandapipes ``height_m`` (metres above sea level) —
  the hydrostatic term the whole teaching value hangs on. ``junction_geodata``
  is plotting-only and has no hydraulic effect.
* Consumers are ``create_sink`` elements (fixed mdot in M0; the demand engine
  lands in M3, pressure-driven demand in M5).
* The head source is ``create_ext_grid(type="p")`` — fixed pressure, mass
  flow is a result. Exactly one per net in M0 (wire kind stays ``slack``).
* Element creation order == input row order, so profile row order *is* the
  element index (blueprint "profiles-as-definitions"); the explicit index
  records in :class:`NetIndex` guard the coupling anyway.

Profile resampling: file resolution → engine tick resolution as a
**staircase** (piecewise-constant repeat), kept from the fork parent for the
M3 demand engine; in M0 only ``t_air_c`` and the constant demand rows use it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandapipes as pp

from .net_inputs import NetInputs

log = logging.getLogger(__name__)


@dataclass
class NetIndex:
    """Element index records + static per-element metadata.

    Row order of every array equals the input-file row order (which equals
    the pandapipes element index by construction — guarded, not assumed).
    """

    # consumers (row order = consumers.json order = sink element index)
    consumers: np.ndarray
    consumer_names: list[str]
    consumer_nodes: list[str]
    # junctions (single layer: one junction per node)
    junction: dict[str, int]   # node name -> pandapipes junction index
    junction_names: list[str]  # pandapipes junction index -> node name
    init_pn_bar: np.ndarray    # build-time pn_bar per junction (failure reset)
    # pipes (row order = pipes.json order == pandapipes pipe index)
    pipes: np.ndarray
    # supply. producer_meta rows carry a platform-unique "pid" (the wire id —
    # element indices live in per-component tables and collide across kinds)
    # plus the pandapipes "element" index within the kind's own table.
    ext_grid: int              # ext_grid table index of the slack
    ext_grid_node: str
    # pressure-reducing valves (press_control elements, zone boundaries)
    prvs: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.int64))
    producer_meta: list[dict] = field(default_factory=list)
    # per-consumer kind (future: hydrant/leak emitters share the sink table)
    consumer_kinds: list[str] = field(default_factory=list)

    def next_pid(self) -> int:
        return 1 + max((int(m["pid"]) for m in self.producer_meta), default=-1)


@dataclass
class ProfileArrays:
    """Dense ``[n_elements, ticks]`` profile arrays at engine tick resolution."""

    steps: int                 # total ticks over the whole horizon
    steps_per_day: int
    n_days: int
    mdot_kg_per_s: np.ndarray  # consumer demand [n_cons, T] (constant rows in M0)
    t_air_c: np.ndarray        # [T] environment driver (unused until M3)
    index: NetIndex


def _resample_staircase(values, n_ticks: int) -> np.ndarray:
    """Piecewise-constant resample of a profile array onto the tick grid."""
    src = np.asarray(values, dtype=float)
    idx = (np.arange(n_ticks, dtype=np.int64) * len(src)) // n_ticks
    return src[idx]


def build_network(
    inputs: NetInputs,
    steps_per_day: int = 1440,
) -> tuple[pp.pandapipesNet, ProfileArrays]:
    """Construct the pandapipes net + dense profiles from validated inputs."""
    n_ticks = inputs.n_days * steps_per_day

    t_air_c = _resample_staircase(inputs.environment.t_air_c, n_ticks)

    net = pp.create_empty_network(fluid="water", name=inputs.name)

    # --- junctions: one per node, elevation in height_m ---
    junction: dict[str, int] = {}
    junction_names: list[str] = []
    for j in inputs.structure.junctions:
        lat, lon = j.geo
        # pandapipes geodata is (x, y) = (lon, lat); hydraulically inert.
        jj = pp.create_junction(net, pn_bar=j.pn_bar, tfluid_k=293.15,
                                height_m=float(j.elevation_m),
                                name=j.name, geodata=(lon, lat))
        junction[j.name] = jj
        junction_names.append(j.name)

    # --- pipes: one per entry, explicit k_mm (resolved by the contract:
    # catalog dn/material or explicit values), no thermal parameters ---
    pipe_idx: list[int] = []
    for i, p in enumerate(inputs.pipes.pipes):
        pi = pp.create_pipe_from_parameters(
            net, junction[p.from_node], junction[p.to_node],
            length_km=float(p.length_km), inner_diameter_mm=float(p.inner_diameter_mm),
            k_mm=float(p.k_mm), sections=p.sections, name=f"pipe{i}")
        pipe_idx.append(pi)

    # --- consumers: sinks with fixed demand (M0) ---
    consumers = inputs.consumers.consumers
    n_cons = len(consumers)
    mdot = np.empty((n_cons, n_ticks))
    consumer_idx: list[int] = []
    for i, c in enumerate(consumers):
        mdot[i] = float(c.mdot_kg_per_s)
        name = c.name or f"consumer_{c.node}"
        sk = pp.create_sink(net, junction=junction[c.node],
                            mdot_kg_per_s=float(c.mdot_kg_per_s), name=name)
        consumer_idx.append(sk)

    # --- supply: exactly one ext_grid slack (validated upstream) ---
    slack = next(s for s in inputs.supply.supplies if s.kind == "ext_grid")
    name = slack.name or f"slack_{slack.node}"
    eg = pp.create_ext_grid(net, junction=junction[slack.node],
                            p_bar=float(slack.p_bar), type="p", name=name)
    producer_meta = [{"pid": 0, "kind": "slack", "element": eg,
                      "node": slack.node, "name": name}]

    # --- pressure-reducing valves (Druckminderer): press_control holding
    # p_out_bar at the outlet junction — the static zone boundary (M1;
    # runtime supervision arrives with the M2 zone controllers) ---
    prv_idx: list[int] = []
    for v in inputs.supply.prvs:
        vname = v.name or f"prv_{v.from_node}_{v.to_node}"
        pc = pp.create_pressure_control(
            net, from_junction=junction[v.from_node],
            to_junction=junction[v.to_node],
            controlled_junction=junction[v.to_node],
            controlled_p_bar=float(v.p_out_bar), name=vname)
        prv_idx.append(pc)
        producer_meta.append({"pid": len(producer_meta), "kind": "prv",
                              "element": int(pc), "node": v.to_node,
                              "from_node": v.from_node, "name": vname})

    index = NetIndex(
        consumers=np.asarray(consumer_idx, dtype=np.int64),
        consumer_names=[c.name or f"consumer_{c.node}" for c in consumers],
        consumer_nodes=[c.node for c in consumers],
        consumer_kinds=[c.kind for c in consumers],
        junction=junction,
        junction_names=junction_names,
        init_pn_bar=net.junction["pn_bar"].to_numpy(copy=True),
        pipes=np.asarray(pipe_idx, dtype=np.int64),
        ext_grid=eg,
        ext_grid_node=slack.node,
        prvs=np.asarray(prv_idx, dtype=np.int64),
        producer_meta=producer_meta,
    )
    profiles = ProfileArrays(
        steps=n_ticks,
        steps_per_day=steps_per_day,
        n_days=inputs.n_days,
        mdot_kg_per_s=mdot,
        t_air_c=t_air_c,
        index=index,
    )
    return net, profiles
