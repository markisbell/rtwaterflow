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
from pandapipes.std_types import create_pump_std_type
from pandapipes.std_types.std_type_class import PumpStdType

from .net_inputs import NetInputs

log = logging.getLogger(__name__)

#: water column → pressure (cold drinking water ~20 °C): 1 m ≈ 0.0979 bar
RHO_KG_M3 = 998.2
G_M_S2 = 9.81
BAR_PER_M = RHO_KG_M3 * G_M_S2 / 1e5


class StationLiftStdType(PumpStdType):
    """Constant-lift pump std_type — the curve lives OUTSIDE the solver.

    WHY (runtime-pinned on pandapipes 0.14): the pump component applies the
    curve lift EXPLICITLY per Newton iteration (``PL`` recomputed from the
    previous iterate's flow, no dPL/dQ in the Jacobian). With a realistic
    steep water curve against dominant static head that fixed-point diverges
    — it overshoots along the curve and falls into the reverse-flow bypass
    sink (upstream assumes zero lift AND zero resistance for Q < 0), where
    the Hochbehälter drains backwards through the running pump at runaway
    rates (−51 kg/s through a 45 m³/h pump on Musterdorf).

    Remedy: ``get_pressure`` returns a CONSTANT ``lift_bar`` (making every
    pipeflow call trivially stable) and :meth:`Simulator._solve_step` finds
    the honest curve operating point ``lift = curve(Q(lift))`` in an outer
    bracketed scalar root-find, with EPANET-style check-valve closure as the
    terminal case. Reverse flow keeps upstream's zero-lift semantics — the
    outer loop closes the valve before that state can persist.
    """

    def __init__(self, name, reg_par, sector=None):
        if sector is None:
            super().__init__(name, reg_par)
        else:
            super().__init__(name, reg_par, sector)
        #: the lift applied by the NEXT solve; starts at shutoff head (max
        #: effort — the outer loop iterates it down onto the curve). The
        #: builder overwrites both with the bundle's hydrostatic seed;
        #: reset_operations restores lift_seed_bar (deterministic replay).
        self.lift_bar = self.shutoff_bar()
        self.lift_seed_bar = self.lift_bar

    def shutoff_bar(self) -> float:
        # reg_par is descending-exponent: the constant term is the Q=0 lift
        return float(self.reg_par[-1])

    def curve_lift_bar(self, q_m3_per_h: float) -> float:
        """The bundle's actual Q-H curve (regression polynomial, ≥ 0) —
        evaluated by the outer operating-point iteration, never the solver."""
        return max(0.0, float(np.polyval(self.reg_par, float(q_m3_per_h))))

    def get_pressure(self, vdot_m3_per_s):
        if np.iterable(vdot_m3_per_s):
            vdot = np.asarray(vdot_m3_per_s, dtype=float)
            return np.where(vdot < 0, 0.0, self.lift_bar)
        return 0.0 if vdot_m3_per_s < 0 else self.lift_bar


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
    ext_grid: int              # first head source's ext_grid element (SCADA anchor)
    ext_grid_node: str         # first head source's node
    # ALL fixed-pressure elements (plain slacks + tank-owned ext_grids)
    ext_grids: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.int64))
    # pressure-reducing valves (press_control elements, zone boundaries)
    prvs: np.ndarray = field(
        default_factory=lambda: np.zeros(0, dtype=np.int64))
    # pump stations (pump elements, curve std_types)
    stations: np.ndarray = field(
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
    mdot_kg_per_s: np.ndarray  # consumer BASE demand [n_cons, T]
    demand_factor: np.ndarray  # [T] global diurnal factor (M2 interim; M3
    #                            archetype profiles replace it)
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

    # --- supply: head sources = plain ext_grids + tank-owned ext_grids
    # (a tank's pressure is re-written per tick by its WaterTank controller
    # from the integrated level; the builder seeds level_initial) ---
    producer_meta: list[dict] = []
    eg_elements: list[int] = []
    for s in inputs.supply.supplies:
        if s.kind != "ext_grid":
            continue
        name = s.name or f"slack_{s.node}"
        eg = pp.create_ext_grid(net, junction=junction[s.node],
                                p_bar=float(s.p_bar), type="p", name=name)
        eg_elements.append(int(eg))
        producer_meta.append({"pid": len(producer_meta), "kind": "slack",
                              "element": int(eg), "node": s.node,
                              "name": name})
    for tk in inputs.supply.tanks:
        name = tk.name or f"tank_{tk.node}"
        p0 = float(tk.level_initial_m) * BAR_PER_M
        eg = pp.create_ext_grid(net, junction=junction[tk.node],
                                p_bar=p0, type="p", name=name)
        eg_elements.append(int(eg))
        producer_meta.append({"pid": len(producer_meta), "kind": "tank",
                              "element": int(eg), "node": tk.node,
                              "name": name})

    # --- pressure-reducing valves (Druckminderer): press_control holding
    # p_out_bar at the outlet junction — the static zone boundary (M1;
    # runtime supervision arrives with the M2+ zone controllers) ---
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

    # --- pump stations: pump branches with regression Q-H std_types.
    # NEVER two hydraulically parallel pump branches (upstream issue #693).
    # Initial in_service: hysteresis rules decide pre-solve from tick 0;
    # manual stations start at their configured state.
    station_idx: list[int] = []
    for st in inputs.supply.stations:
        sname = st.name or f"station_{st.from_node}"
        curve = StationLiftStdType.from_list(
            sname, [float(p[0]) for p in st.curve],
            [float(p[1]) for p in st.curve], 2)
        # cold-start lift seed: the bundle's hydrostatic pn_bar estimates
        # put the initial operating point near the root — the outer
        # iteration then settles in 1-3 solves instead of hunting the
        # forward branch across the reverse-flow cliff from shutoff head
        dp_seed = (
            float(net.junction.at[junction[st.to_node], "pn_bar"])
            - float(net.junction.at[junction[st.from_node], "pn_bar"]))
        curve.lift_bar = min(max(dp_seed, 0.0) + 0.1, curve.shutoff_bar())
        # remembered for reset_operations: deterministic replay (scenario
        # load / bulk export) must not inherit the live warm operating point
        curve.lift_seed_bar = curve.lift_bar
        create_pump_std_type(net, sname, curve, True)
        running = (st.control.running if st.control.mode == "manual"
                   else True)
        pu = pp.create_pump(net, from_junction=junction[st.from_node],
                            to_junction=junction[st.to_node],
                            std_type=sname, name=sname,
                            in_service=running)
        station_idx.append(pu)
        producer_meta.append({"pid": len(producer_meta), "kind": "station",
                              "element": int(pu), "node": st.to_node,
                              "from_node": st.from_node, "name": sname})

    index = NetIndex(
        consumers=np.asarray(consumer_idx, dtype=np.int64),
        consumer_names=[c.name or f"consumer_{c.node}" for c in consumers],
        consumer_nodes=[c.node for c in consumers],
        consumer_kinds=[c.kind for c in consumers],
        junction=junction,
        junction_names=junction_names,
        init_pn_bar=net.junction["pn_bar"].to_numpy(copy=True),
        pipes=np.asarray(pipe_idx, dtype=np.int64),
        ext_grid=eg_elements[0],
        ext_grid_node=next(m["node"] for m in producer_meta
                           if m["kind"] in ("slack", "tank")),
        ext_grids=np.asarray(eg_elements, dtype=np.int64),
        prvs=np.asarray(prv_idx, dtype=np.int64),
        stations=np.asarray(station_idx, dtype=np.int64),
        producer_meta=producer_meta,
    )
    factor = (_resample_staircase(inputs.environment.demand_factor, n_ticks)
              if inputs.environment.demand_factor is not None
              else np.ones(n_ticks))
    profiles = ProfileArrays(
        steps=n_ticks,
        steps_per_day=steps_per_day,
        n_days=inputs.n_days,
        mdot_kg_per_s=mdot,
        demand_factor=factor,
        t_air_c=t_air_c,
        index=index,
    )
    return net, profiles
