"""Simulator — owns the net, profiles and the per-tick hydraulic solve.

``run_step(step, day)`` = ``_apply_step`` (demand profiles → sinks)
→ retry-ladder solve → ``_collect()`` (derived quantities)
→ platform warm start (``pn_bar`` only — no thermal state in water mode).

Failure policy (binding, inherited from the fork parent rtheatflow): try each
ladder tier, catching ``PipeflowNotConverged`` *and* a deliberate catch-all
``Exception`` arm (racing runtime mutations may poison one step — no locks by
design). If all tiers fail, **reuse the last converged state** and publish the
frame with ``converged=false`` / ``solver_status="failed"``. Never crash the
loop; non-convergence is data.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np
import pandapipes as pp
from pandapipes import pipeflow
from pandapipes.pf.pipeflow_setup import PipeflowNotConverged

from .assets.tank import WaterTank
from .assets.wellfield import Aquifer, Well, WellField
from .compliance import ComplianceEngine
from .compliance.engine import P_MIN_EG_BAR
from .config import Settings, get_settings
from .control.rules import HysteresisRule, RuleEngine
from .hydraulics import EmitterController, PDAController
from .hydraulics.pda import (
    DAMP as PDA_DAMP,
    MAX_ITERS as PDA_MAX_ITERS,
    TOL as PDA_TOL,
)
from .estimator import EstimationConfig, ForwardObserver
from .demand import EnvironmentState, build_demand_profiles
from .net_inputs import NetInputs
from .network_builder import (
    RHO_KG_M3,
    ProfileArrays,
    _resample_staircase,
    build_network,
)
from .sensors import WINDOW_MINUTES, MeasurementSet, _r

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# StepResult — the single wire format. REST /state, /history, WS frames and
# the recorder all use the same asdict() + projection path.
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    step: int
    day: int
    time_of_day: str          # "HH:MM"
    converged: bool
    solver_status: str        # "ok" | "degraded" | "failed"
    solve_ms: float
    timestamp: float
    # -- ground-truth layer (stripped in strict mode) --
    junctions: list = field(default_factory=list)
    pipes: list = field(default_factory=list)
    consumers: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    #: M4 compliance findings — derived FROM the truth layer, so strict
    #: mode strips them too (the observed-layer alarm view is M7)
    findings: list = field(default_factory=list)
    # -- supply/equipment (always visible) --
    producers: list = field(default_factory=list)
    tanks: list = field(default_factory=list)
    emitters: list = field(default_factory=list)   # M5 leaks/hydrants/bursts
    wellfields: list = field(default_factory=list)  # M6 raw-water side
    controls: dict = field(default_factory=dict)
    # -- observability layers --
    measurements: dict = field(default_factory=dict)
    observed_summary: dict | None = None
    estimated: dict | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Retry ladder — hydraulics-only. friction_model="colebrook" is deliberate:
# the pandapipes "nikuradse" default adds laminar + turbulent lambda instead
# of selecting by Reynolds regime (upstream issue #803) and must never be the
# primary model for water. The last tier falls back to nikuradse and reports
# "degraded" (friction fallback), preserving the ok|degraded|failed vocabulary.
# ---------------------------------------------------------------------------

def retry_attempts(iter_base: int) -> list[dict]:
    """The hydraulic ladder. ``RTWATERFLOW_SOLVER_ITER`` is the base ``iter``
    of tier 1; the retries use 3x (single-knob semantics).

    ``max_iter_colebrook`` raises the INNER Colebrook-White lambda Newton
    above the upstream default of 10 — near-stagnant stubs (noisy M3 night
    demands, laminar Re) otherwise fail the lambda iteration and needlessly
    drop healthy ticks off the primary model (runtime-verified: 4/96
    Musterdorf ticks at default, 2 at 100).

    Tier 3 is ``swamee-jain`` (M3): the EXPLICIT Colebrook approximation
    (~1–3 % on λ, no inner Newton) converges on transitional-Reynolds
    states (several pipes at Re 1700–3800) where the implicit model's
    outer Newton flip-flops across the laminar/turbulent switch —
    runtime-verified on Musterdorf's M3 demand profiles. Honest but far
    closer to the primary model than the nikuradse last resort (#803)."""
    n = int(iter_base)
    return [
        dict(mode="hydraulics", iter=n, friction_model="colebrook",
             max_iter_colebrook=100),
        dict(mode="hydraulics", iter=3 * n, friction_model="colebrook",
             max_iter_colebrook=300),
        dict(mode="hydraulics", iter=3 * n, friction_model="swamee-jain"),
        dict(mode="hydraulics", iter=3 * n, friction_model="nikuradse"),
    ]


#: reverse-flow threshold below which a running pump's check valve closes
#: (numerical near-zero flows must not trip the valve)
CV_EPS_KG_PER_S = 1e-4

#: station operating-point tolerance |curve(Q) − lift| and the cap on
#: pipeflow calls per tick spent settling it. Worst case is a cold start on
#: a marginally sized pump (root within CV_CLOSE_MARGIN of shutoff): one
#: max-effort solve + one bisection per solve until two forward points feed
#: the secant. The cap is a never-500 backstop, not a normal path — an
#: exhausted tick is honestly reported "degraded" and the warm start
#: recovers next tick.
LIFT_TOL_BAR = 0.02
MAX_STATION_SOLVES = 16

#: a pump still reversing this close to shutoff head has no forward
#: operating point — its check valve closes for the tick
CV_CLOSE_MARGIN_BAR = 0.05

#: an "ok" frame must not report gauge pressure below this (M5 review):
#: negative pressure is unphysical (cavitation / air draw), so such a
#: solve is downgraded to "degraded". A small negative tolerance absorbs
#: solver round-off at a legitimately near-zero head source.
NEG_PRESSURE_FLOOR_BAR = -0.05


@dataclass
class SolveOutcome:
    converged: bool
    status: str           # "ok" | "degraded" | "failed"
    tier: int             # 1-based tier that converged; 0 if none
    solve_ms: float
    error: str | None = None


def solve_with_retry(net, iter_base: int = 100) -> SolveOutcome:
    """Run the retry ladder on *net*. Never raises for non-convergence."""
    t0 = time.perf_counter()
    errors: list[str] = []
    attempts = retry_attempts(iter_base)
    for tier, kwargs in enumerate(attempts, start=1):
        try:
            # swamee-jain evaluates 5.74/Re^0.9 vectorized — Re = 0 on
            # zero-flow pipes raises a benign numpy divide warning (λ term
            # → 0 in the pressure-loss product). Silence it at the numpy
            # level so warnings-as-errors CI cannot knock out the tier
            # (M3 review finding).
            with np.errstate(divide="ignore"):
                pipeflow(net, **kwargs)
        except PipeflowNotConverged as exc:
            errors.append(f"tier {tier} {kwargs}: not converged ({exc})")
            continue
        except Exception as exc:  # deliberate catch-all arm (racing CRUD)
            errors.append(f"tier {tier} {kwargs}: {type(exc).__name__}: {exc}")
            continue
        ms = (time.perf_counter() - t0) * 1000.0
        if kwargs["friction_model"] == "colebrook":
            return SolveOutcome(True, "ok", tier, ms)
        if kwargs["friction_model"] == "swamee-jain":
            return SolveOutcome(
                True, "degraded", tier, ms,
                error="swamee-jain friction fallback: explicit Colebrook "
                      "approximation (transitional-flow tick)")
        return SolveOutcome(
            True, "degraded", tier, ms,
            error="nikuradse friction fallback: low-Re friction biased "
                  "(pandapipes issue #803)")
    ms = (time.perf_counter() - t0) * 1000.0
    err = "; ".join(errors) or "no solver tier attempted"
    log.warning("all retry-ladder tiers failed: %s", err)
    return SolveOutcome(False, "failed", 0, ms, error=err)


# ---------------------------------------------------------------------------
# Physics collection — shared by the Simulator's truth payload and the
# forward observer's twin (estimator.py): one set of formulas, never two.
# ---------------------------------------------------------------------------

def collect_physics(net, idx, tanks=None, emitters=None) -> dict:
    """The four ground-truth wire keys + aux values from a SOLVED *net*.

    Returns ``{junctions, pipes, consumers, summary, aux}`` where ``aux``
    carries ``worst_pos`` (consumer row of the min-pressure worst point) for
    the caller's blind-spot flag and the key_points measurement preset.

    *tanks* (the Simulator's WaterTank list) supplies the overflow-spill
    split of the stored flow; the estimator twin passes None (no tank
    objects — its stored figure then includes any spill). *emitters* (the
    M5 EmitterController) supplies the leak/hydrant/burst withdrawal that
    the head-source feed must also balance against.
    """
    rj, rp = net.res_junction, net.res_pipe
    rs = net.res_sink

    junctions = [
        {"id": int(i), "name": idx.junction_names[i],
         "p_bar": _r(rj.p_bar.iloc[i])}
        for i in range(len(rj))
    ]

    pipes = []
    for i in range(len(rp)):
        pid = int(net.pipe.index[i])
        pipes.append({
            "id": pid, "trench": pid,  # single layer: pipe id == trench id
            "mdot_kg_per_s": _r(rp.mdot_from_kg_per_s.iloc[i]),
            "v_m_per_s": _r(rp.v_mean_m_per_s.iloc[i]),
            "dp_bar": _r(rp.p_from_bar.iloc[i] - rp.p_to_bar.iloc[i]),
        })

    # consumer junction pressures — the compliance-relevant quantity
    cons_j = [idx.junction[n] for n in idx.consumer_nodes]
    p_cons = rj.p_bar.to_numpy()[cons_j] if cons_j else np.array([])

    mdot_demand = net.sink.loc[idx.consumers, "mdot_kg_per_s"].to_numpy(
        dtype=float) if len(idx.consumers) else np.array([])
    mdot_delivered = rs.loc[idx.consumers, "mdot_kg_per_s"].to_numpy(
        dtype=float) if len(idx.consumers) else np.array([])

    consumers = [
        {"id": int(idx.consumers[i]), "name": idx.consumer_names[i],
         "node": idx.consumer_nodes[i],
         "kind": (idx.consumer_kinds[i] if idx.consumer_kinds else "consumer"),
         "mdot_demand_kg_per_s": _r(mdot_demand[i]),
         "mdot_kg_per_s": _r(mdot_delivered[i]),
         "p_bar": _r(p_cons[i])}
        for i in range(len(idx.consumers))
    ]

    # min-pressure worst point over CONSUMER junctions (the source junction
    # legitimately sits at low gauge pressure — e.g. a tank surface at
    # 0.5 bar — and must not masquerade as the network's worst point)
    if len(p_cons):
        worst_pos = int(np.argmin(p_cons))
        p_min_bar = float(p_cons[worst_pos])
        worst_consumer = idx.consumer_names[worst_pos]
        worst_node = idx.consumer_nodes[worst_pos]
    else:
        worst_pos, p_min_bar, worst_consumer, worst_node = 0, None, None, None

    # feed over ALL head sources: pandapipes reports ext_grid withdrawal as
    # negative mdot (pinned) — negatives supply the net. Positives are split
    # BY ELEMENT KIND (M2 review finding — booking an absorbing plain slack
    # as "stored" fabricated tank storage on multi-slack nets):
    #   tank elements  -> stored (level-effective) + spill (overflow clamp:
    #                     the level integrator could not keep it, the tank
    #                     spills it — EPANET overflow semantics; *tanks*
    #                     supplies the clamp remainder, None for the
    #                     estimator twin which has no tank objects),
    #   slack elements -> exported (water leaving through a fixed-pressure
    #                     boundary, e.g. the downhill reservoir of a
    #                     two-source net).
    eg_ids = idx.ext_grids if len(idx.ext_grids) else np.asarray(
        [idx.ext_grid], dtype=np.int64)
    eg_mdot = net.res_ext_grid.mdot_kg_per_s.loc[eg_ids].to_numpy(dtype=float)
    mdot_feed = float(-eg_mdot[eg_mdot < 0].sum())
    tank_elements = {int(m["element"]) for m in idx.producer_meta
                    if m["kind"] == "tank"}
    pos_tank = float(sum(
        m for el, m in zip(eg_ids, eg_mdot)
        if m > 0 and int(el) in tank_elements))
    mdot_exported = float(sum(
        m for el, m in zip(eg_ids, eg_mdot)
        if m > 0 and int(el) not in tank_elements))
    mdot_spill = float(sum(
        getattr(t, "mdot_spill_kg_per_s", 0.0) for t in (tanks or [])))
    mdot_stored = pos_tank - mdot_spill
    # M5 emitter withdrawal (leaks/hydrants/bursts) — a real loss the feed
    # must balance against, distinct from metered consumer delivery
    mdot_emitted = float(sum(
        e.mdot_kg_per_s for e in (emitters.emitters.values()
                                  if emitters is not None else [])))
    demand_sum = float(mdot_demand.sum())
    delivered_sum = float(mdot_delivered.sum())
    # M5 undersupply signal: demand the network could not deliver (PDA)
    mdot_deficit = max(0.0, demand_sum - delivered_sum)

    summary = {
        "p_min_bar": _r(p_min_bar),
        "worst_consumer": worst_consumer,
        "worst_node": worst_node,
        "mdot_feed_kg_per_s": _r(mdot_feed),
        "mdot_demand_kg_per_s": _r(demand_sum),
        "mdot_delivered_kg_per_s": _r(delivered_sum),
        "mdot_deficit_kg_per_s": _r(mdot_deficit),
        "mdot_stored_kg_per_s": _r(mdot_stored),
        "mdot_spill_kg_per_s": _r(mdot_spill),
        "mdot_exported_kg_per_s": _r(mdot_exported),
        "mdot_emitted_kg_per_s": _r(mdot_emitted),
        "balance_err_kg_per_s": _r(mdot_feed - delivered_sum - mdot_stored
                                   - mdot_spill - mdot_exported
                                   - mdot_emitted),
    }
    return {
        "junctions": junctions,
        "pipes": pipes,
        "consumers": consumers,
        "summary": summary,
        "aux": {"worst_pos": worst_pos},
    }


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

class Simulator:
    """Builds net + profiles once; steps the physics; collects results."""

    def __init__(self, inputs: NetInputs, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.inputs = inputs
        #: runtime weather overrides (config, scenario-saved) — the demand
        #: engine rebuilds the profiles from them (set_environment)
        self.environment = EnvironmentState()
        self.net, self.profiles = build_network(
            inputs, steps_per_day=self.settings.steps_per_day)
        self.index = self.profiles.index

        # runtime consumer op log (recipes, scenario save/replay)
        self.consumer_ops: list[dict] = []

        # simulated seconds per engine step (tank level integration)
        self._dt_s = 86400.0 / float(self.settings.steps_per_day)

        # tanks: level-integrating head nodes (ext_grid + controller)
        self.tanks: list[WaterTank] = []
        tank_meta = {m["node"]: m for m in self.index.producer_meta
                     if m["kind"] == "tank"}
        for spec in inputs.supply.tanks:
            meta = tank_meta[spec.node]
            self.tanks.append(WaterTank(
                name=meta["name"], node=spec.node,
                element=int(meta["element"]),
                pid=int(meta["pid"]),
                area_m2=float(spec.area_m2),
                level_min_m=float(spec.level_min_m),
                level_max_m=float(spec.level_max_m),
                fire_reserve_m3=float(spec.fire_reserve_m3),
                kind=spec.kind,
                level_m=float(spec.level_initial_m),
                level_initial_m=float(spec.level_initial_m)))
        self._tanks_by_name = {t.name: t for t in self.tanks}

        # M6 well fields (raw-water side, pure Python): each fills a break
        # tank; its production is capped by the aquifer. Built here, stepped
        # pre-solve in _apply_step.
        self.wellfields: list[WellField] = []
        for wf in inputs.supply.wellfields:
            self.wellfields.append(WellField(
                name=wf.name,
                wells=[Well(
                    name=w.name, static_level_m=float(w.static_level_m),
                    spec_capacity_m3h_per_m=float(w.spec_capacity_m3h_per_m),
                    screen_top_m=float(w.screen_top_m),
                    rated_m3_h=float(w.rated_m3_h),
                    q_s_decay_per_a=float(w.q_s_decay_per_a),
                    protection_margin_m=float(w.protection_margin_m))
                    for w in wf.wells],
                aquifer=Aquifer(
                    storativity_area_m2=float(wf.aquifer.storativity_area_m2),
                    level_initial_m=float(wf.aquifer.level_initial_m),
                    recharge_m3_per_d_mean=float(
                        wf.aquifer.recharge_m3_per_d_mean)),
                break_tank_name=wf.break_tank,
                pump_head_m=float(wf.pump_head_m),
                efficiency=float(wf.efficiency),
                right_m3_per_a=wf.water_right.m3_per_a,
                right_m3_per_d=wf.water_right.m3_per_d,
                interference_fraction=float(wf.interference_fraction)))
        # break-tank hysteresis bands + which stations draw from a break tank
        self._wf_bands = {wf.name: (wf.on_below_m, wf.off_above_m)
                          for wf in inputs.supply.wellfields}
        break_nodes = {t.node for t in inputs.supply.tanks
                       if t.kind == "break"}
        #: station element -> True if its SUCTION is a break tank (low-level
        #: pump protection: trip when the Reinwasserbehälter runs empty)
        self._break_suction_stations = [
            m for m in self.index.producer_meta
            if m["kind"] == "station" and m["from_node"] in break_nodes]

        # pump stations: hysteresis rules + operator mode overrides
        rules: list[HysteresisRule] = []
        self.station_modes: dict[str, str] = {}
        self._station_specs: dict = {}   # station name -> StationSpec
        station_meta = {m["name"]: m for m in self.index.producer_meta
                        if m["kind"] == "station"}
        for st in inputs.supply.stations:
            sname = st.name or f"station_{st.from_node}"
            meta = station_meta[sname]
            self._station_specs[sname] = st
            if st.control.mode == "hysteresis":
                self.station_modes[sname] = "auto"
                rules.append(HysteresisRule(
                    station_name=sname, pump_element=int(meta["element"]),
                    tank_name=st.control.tank,
                    on_below_m=float(st.control.on_below_m),
                    off_above_m=float(st.control.off_above_m),
                    running=bool(st.control.running),
                    initial_running=bool(st.control.running)))
            else:
                self.station_modes[sname] = ("on" if st.control.running
                                             else "off")
        self.rules = RuleEngine(rules)
        self._initial_station_modes = dict(self.station_modes)
        #: station names whose check valve blocked reverse flow THIS tick
        #: (transient, re-decided every solve — see _solve_step)
        self.cv_closed: set[str] = set()

        #: M4 compliance engine — the post-solve rule pass with rolling
        #: sustained/stagnation state
        self.compliance = ComplianceEngine(
            inputs, self.index, self.settings.steps_per_day)

        #: M5 pressure-dependent demand — Wagner delivery scaling. The
        #: per-consumer p_req is looked up live from the compliance engine
        #: (same W 400-1 storey table; kept in sync by consumer CRUD).
        self.pda = PDAController(enabled=bool(self.settings.pda_enabled))

        #: M5 pressure-dependent emitters (leaks / hydrants / bursts)
        self.emitters = EmitterController(self.net, self.index.junction)
        #: last seeded background-leakage coefficient (scenario-saved —
        #: the per-junction leak emitters are derived from it)
        self.leak_coefficient_per_km: float = 0.0
        #: absolute (unwrapped) sim tick of the last run_step — the clock
        #: time-limited emitters expire against
        self._abs_tick: int = 0
        self._cur_day: int = 0            # day of the last run_step (M6)

        self._last_payload: dict | None = None  # last converged _collect()
        #: blind-spot flag: true when the observed layer misses the TRUE
        #: min-pressure worst point — either no usable pressure reading at
        #: all, or the critical consumer carries no meter. Meta-information
        #: about sensor-layout adequacy, not a physics value. None pre-solve.
        self._blind_spot: bool | None = None

        # net ends for the key_points preset: leaf nodes of the pipe graph
        # (static — runtime consumer CRUD reuses existing nodes)
        degree: dict[str, int] = {}
        for pipe in inputs.pipes.pipes:
            degree[pipe.from_node] = degree.get(pipe.from_node, 0) + 1
            degree[pipe.to_node] = degree.get(pipe.to_node, 0) + 1
        self._end_nodes = [n for n, d in degree.items()
                           if d == 1 and n != self.index.ext_grid_node]

        # measurement layer: real placement model; the default preset stays
        # all_consumers + source SCADA, applied through the placement
        # machinery; the 15-min standard window is expressed in engine ticks.
        window_steps = max(1, round(
            WINDOW_MINUTES * self.settings.steps_per_day / 1440.0))
        self.measurements = MeasurementSet(
            window_steps=window_steps, context=self._measurement_context)

        # estimation layer: STUBBED in M0 (estimator.py returns no estimate);
        # the config plumbing survives so the engine re-applies policy across
        # grid swaps and the UI three-view switcher stays wired.
        self.est_config = EstimationConfig()
        self._observer: ForwardObserver | None = None

    # -- estimation layer (stub in M0) ----------------------------------------

    def set_est_config(self, cfg: EstimationConfig) -> None:
        """Install a new estimation policy; the observer is dropped and
        rebuilt lazily."""
        self.est_config = cfg
        self._observer = None

    def _maybe_estimate(self, payload: dict, tick: int,
                        step: int, day: int) -> dict | None:
        if not self.est_config.enabled:
            self._observer = None
            return None
        if self._observer is None:
            self._observer = ForwardObserver(self, self.est_config)
        return self._observer.maybe_estimate(payload, tick, step, day)

    # -- measurement layer ----------------------------------------------------

    def _measurement_context(self) -> dict:
        """Element inventory for the placement presets (sensors.py).

        The key_points worst-point consumer comes from the last converged
        frame — the operator places that meter where the *known* worst point
        is; before the first solve none is known (no meter — honest).
        """
        idx = self.index
        worst_id: int | None = None
        if self._last_payload:
            worst_name = (self._last_payload.get("summary") or {}).get(
                "worst_consumer")
            if worst_name in idx.consumer_names:
                pos = idx.consumer_names.index(worst_name)
                worst_id = int(idx.consumers[pos])
        return {
            "consumer_ids": [int(c) for c in idx.consumers],
            "plant_node": idx.ext_grid_node,
            "end_nodes": list(self._end_nodes),
            "node_names": list(idx.junction),
            "worst_consumer_id": worst_id,
        }

    def measurement_placement(self) -> dict:
        """The GET /measurements payload (placement + coverage)."""
        idx = self.index
        meta = [{"id": int(idx.consumers[i]), "name": idx.consumer_names[i],
                 "node": idx.consumer_nodes[i]}
                for i in range(len(idx.consumers))]
        return self.measurements.placement(
            n_consumers=len(idx.consumers),
            n_nodes=len(idx.junction),
            consumer_meta=meta)

    # -- tick bookkeeping ---------------------------------------------------

    def _tick(self, step: int, day: int) -> int:
        """Global profile tick for (step, day); days wrap modulo the horizon."""
        p = self.profiles
        step = min(max(int(step), 0), p.steps_per_day - 1)
        return (int(day) % p.n_days) * p.steps_per_day + step

    def _time_of_day(self, step: int) -> str:
        minute = int(round(step * 1440.0 / self.settings.steps_per_day)) % 1440
        return f"{minute // 60:02d}:{minute % 60:02d}"

    # -- per-tick input application ------------------------------------------

    def _apply_step(self, tick: int) -> None:
        """demand → tank heads → operating rules (all pre-solve inputs).
        The M5 PDA delivery scaling and the emitter withdrawals are the
        pressure-dependent part — reset here, converged in ``_solve_step``."""
        # consumer demand: the M3 engine profiles carry the full archetype
        # (or legacy demand_factor) modulation — plain column read. Delivery
        # scaling resets to full each tick (PDA re-derives it from the
        # solved pressures); emitters start closed and open from pressure.
        if len(self.index.consumers):
            self.net.sink.loc[self.index.consumers, "mdot_kg_per_s"] = (
                self.profiles.mdot_kg_per_s[:, tick])
            self.net.sink.loc[self.index.consumers, "scaling"] = 1.0
        # emitters expire against the ABSOLUTE tick (not the wrapped
        # profile tick) so a timed hydrant expires correctly across day
        # boundaries (M5 review)
        self.emitters.expire(self._abs_tick)
        self.emitters.zero_withdrawals()
        # tank heads from the integrated levels
        for tank in self.tanks:
            tank.write_p(self.net)
        # operating rules (hysteresis pump switching + operator overrides);
        # tank levels are station SCADA — always observed (TF §7)
        self.rules.evaluate(self.net, self._tanks_by_name, self.station_modes)
        # M6 raw-water side: run the well fields (fill the break tanks,
        # capped by the aquifer) pre-solve, so the break-tank head + the
        # network pump state this tick reflect the raw supply
        empty_break_nodes = self._step_wellfields()
        # manual stations without a rule follow their mode directly; their
        # "auto" means the CONFIGURED state (control.running) — without this
        # write nothing ever touches in_service again and a check-valve
        # closure would latch silently forever (M2 review finding)
        ruled = {r.station_name for r in self.rules.rules}
        for meta in self.index.producer_meta:
            if meta["kind"] != "station":
                continue
            mode = self.station_modes.get(meta["name"], "auto")
            if mode in ("on", "off"):
                self.net.pump.at[meta["element"], "in_service"] = mode == "on"
            elif meta["name"] not in ruled:
                spec = self._station_specs.get(meta["name"])
                if spec is not None:
                    self.net.pump.at[meta["element"], "in_service"] = bool(
                        spec.control.running)
        # low-level (dry-run) pump protection is a HARDWARE INTERLOCK —
        # re-asserted LAST so a manual operator 'on' cannot run a network
        # pump on an empty break tank (which, being an ext_grid, would
        # supply phantom water it does not have — M6 review)
        for meta in self._break_suction_stations:
            if meta["from_node"] in empty_break_nodes:
                self.net.pump.at[meta["element"], "in_service"] = False

    def _step_wellfields(self) -> set[str]:
        """Run each well field pre-solve: the well pumps fill the break tank
        on two-point hysteresis (capped by the aquifer), the aquifer steps.
        Returns the nodes of break tanks that are empty (their network pump
        must be tripped by the caller AFTER the operator-mode writes)."""
        if not self.wellfields:
            return set()
        doy = ((self.inputs.environment.season_day_of_year - 1
                + self._cur_day) % 365) + 1
        # accumulate the inflow — several fields may feed ONE break tank
        # (assigning would drop all but the last, destroying mass — review)
        inflow_by_tank: dict[str, float] = {}
        for wf in self.wellfields:
            tank = self._tanks_by_name.get(wf.break_tank_name)
            if tank is None:
                continue
            on_below, off_above = self._wf_bands[wf.name]
            running = wf.pumps_running
            if tank.level_m < on_below:
                running = True
            elif tank.level_m > off_above:
                running = False
            wf.pumps_running = running
            # when filling, the wells pump flat out (produce() caps at the
            # aquifer availability); when full, they rest
            demand_m3_h = 1e6 if running else 0.0
            inflow = wf.produce(demand_m3_h=demand_m3_h, day=self._cur_day,
                                day_of_year=doy, dt_s=self._dt_s)
            inflow_by_tank[tank.node] = inflow_by_tank.get(tank.node, 0.0) + inflow
        empty_break_nodes: set[str] = set()
        for tank in self.tanks:
            if tank.kind != "break":
                continue
            tank.external_inflow_kg_per_s = inflow_by_tank.get(tank.node, 0.0)
            if tank.level_m <= tank.level_min_m + 1e-6:
                empty_break_nodes.add(tank.node)
        return empty_break_nodes

    # -- the step ------------------------------------------------------------

    def run_step(self, step: int, day: int) -> StepResult:
        """One simulation step. Never raises for non-convergence."""
        tick = self._tick(step, day)
        # absolute (unwrapped) sim tick for time-limited emitters — the
        # profile ``tick`` wraps modulo the horizon, so a hydrant opened on
        # day 1 would never expire against it (M5 review)
        self._abs_tick = int(day) * self.profiles.steps_per_day + int(step)
        self._cur_day = int(day)
        apply_error: str | None = None
        try:
            self._apply_step(tick)
        except Exception as exc:  # racing CRUD may poison one step — self-heal
            apply_error = f"apply_step: {type(exc).__name__}: {exc}"
            log.warning("apply_step failed (frame degrades to failed): %s",
                        apply_error)

        if apply_error is None:
            outcome = self._solve_step()
        else:
            outcome = SolveOutcome(False, "failed", 0, 0.0, error=apply_error)

        if outcome.converged:
            try:
                # tank levels advance from the SOLVED balance before the
                # frame is collected (the frame carries this tick's level)
                for tank in self.tanks:
                    tank.integrate(self.net, self._dt_s)
                payload = self._collect(tick)
                # M4 compliance pass on the collected wire values — in its
                # OWN guard: a poisoned rule check must degrade to an
                # honest system finding, never discard a converged frame
                # (M4 review: the shared except desynced tank state)
                try:
                    payload["findings"] = self.compliance.evaluate(
                        payload, outcome.status, outcome.error)
                except Exception:
                    log.exception("compliance pass failed")
                    payload["findings"] = [{
                        "severity": "info", "rule": "Modellhinweis",
                        "check": "solver", "entity_kind": "system",
                        "entity": "compliance", "value": None,
                        "threshold": None, "since_ticks": 0,
                        "text_de": "Regelwerksprüfung ausgefallen — "
                                   "keine Meldungen für diesen Schritt",
                    }]
                self._last_payload = payload
            except Exception as exc:
                log.exception("result collection failed")
                outcome = SolveOutcome(
                    False, "failed", outcome.tier, outcome.solve_ms,
                    error=f"collect: {type(exc).__name__}: {exc}")
                payload = self._reused_payload()
            else:
                # platform warm start: converged pressures become the next
                # initialization (pn_bar ONLY — no thermal state in water)
                self.net.junction["pn_bar"] = self.net.res_junction.p_bar.values
        else:
            # failed step: reset init to build-time pressures and reuse the
            # last converged state
            self._reset_initialization()
            payload = self._reused_payload()

        # estimation layer (stub in M0): refresh on converged frames; failed
        # frames carry the last estimate stale — consistent with the reused
        # truth/measurement state above.
        if outcome.converged:
            estimated = self._maybe_estimate(payload, tick, step, day)
        else:
            estimated = self._observer.last if self._observer else None

        return StepResult(
            step=int(step),
            day=int(day),
            time_of_day=self._time_of_day(step),
            converged=bool(outcome.converged),
            solver_status=outcome.status,
            solve_ms=_r(outcome.solve_ms, 3) or 0.0,
            timestamp=time.time(),
            error=outcome.error,
            estimated=estimated,
            **payload,
        )

    def _solve_step(self) -> SolveOutcome:
        """The full tick solve: the station/check-valve hydraulic solve
        (``_solve_hydraulic``) wrapped in the M5 pressure-dependent outer
        fixed point (Wagner PDA delivery scaling + emitter withdrawals).

        Each outer pass reads the solved node pressures, updates consumer
        ``sink.scaling`` (delivery backs off below ``p_req``, dry at
        ``p_min``) and emitter ``mdot = C·p^N1``, then re-solves — until the
        scaling factors and emitter flows stop moving (``PDA_TOL``) or
        ``PDA_MAX_ITERS``. A HEALTHY net is a no-op: every consumer sits at
        p ≥ p_req, the first factor pass sees no change, no emitters exist,
        and the loop exits after the single hydraulic solve."""
        outcome = self._solve_hydraulic()
        cons = self.index.consumers
        pda_on = self.pda.enabled and len(cons)
        has_em = bool(self.emitters.emitters)
        if (not pda_on and not has_em) or not outcome.converged:
            return outcome

        cons_jj = [self.index.junction[n] for n in self.index.consumer_nodes]
        p_req = [self.compliance.p_req.get(n, P_MIN_EG_BAR)
                 for n in self.index.consumer_names]
        for _ in range(PDA_MAX_ITERS):
            rj = self.net.res_junction
            # measure the Wagner consistency GAP at the current solved state
            gap = 0.0
            target = old = None
            if pda_on:
                p_cons = rj.p_bar.to_numpy()[cons_jj]
                old = self.net.sink.loc[cons, "scaling"].to_numpy(dtype=float)
                target = np.array([
                    self.pda.factor(float(p_cons[i]), float(p_req[i]))
                    for i in range(len(cons))], dtype=float)
                gap = max(gap, float(np.max(np.abs(target - old)))
                          if len(target) else 0.0)
            if has_em:
                gap = max(gap, self.emitters.consistency_gap(
                    rj, self.index.junction))
            if gap < PDA_TOL:
                break            # scaling/emitter state is self-consistent
            # not consistent: damped step toward the target, then re-solve
            if pda_on:
                self.net.sink.loc[cons, "scaling"] = old + PDA_DAMP * (
                    target - old)
            if has_em:
                self.emitters.damped_update(
                    rj, self.index.junction, PDA_DAMP)
            outcome = self._solve_hydraulic()
            if not outcome.converged:
                return outcome
        else:
            # cap exhausted without a self-consistent state (very stiff
            # undersupply) — honest degradation. The frame MAY carry
            # unphysical pressures (an unsettled iterate); it is flagged so.
            if outcome.converged:
                log.warning("PDA/emitter fixed point not settled after %d "
                            "iterations (gap %.3f)", PDA_MAX_ITERS, gap)
                outcome = SolveOutcome(
                    True, "degraded", outcome.tier, outcome.solve_ms,
                    error="pressure-demand fixed point not settled after "
                          f"{PDA_MAX_ITERS} iterations")
        # PHYSICAL-VALIDITY guard (M5 review): PDA throttles only consumer
        # demand, so an emitter (burst/hydrant) can crater OTHER junctions
        # below zero while the fixed point is self-consistent. Negative
        # gauge pressure is unphysical (real mains cavitate / draw air —
        # pandapipes does not model that), so an "ok" frame must never
        # report it. Downgrade to "degraded" — the honest signal that the
        # model is outside its validity; the M4 p_min findings + the
        # negative summary.p_min_bar still surface the crisis truthfully.
        if outcome.converged and outcome.status == "ok":
            p_min = float(self.net.res_junction.p_bar.min())
            if p_min < NEG_PRESSURE_FLOOR_BAR:
                return SolveOutcome(
                    True, "degraded", outcome.tier, outcome.solve_ms,
                    error=(f"physically invalid: {p_min:.2f} bar (negative "
                           "gauge pressure — demand/emitter draw exceeds "
                           "the network's capacity)"))
        return outcome

    def _solve_hydraulic(self) -> SolveOutcome:
        """Retry-ladder solve + station operating points + check valves.

        pandapipes applies pump curves EXPLICITLY per Newton iteration (no
        dPL/dQ in the Jacobian) — against dominant static head that
        fixed-point diverges into the reverse-bypass sink (see
        ``StationLiftStdType``). The solver therefore sees a CONSTANT
        per-station lift, and this outer loop finds the honest curve
        operating point ``lift = curve(Q(lift))`` by a bracketed secant on
        ``[0, shutoff]`` — ``g(lift) = curve(Q(lift)) − lift`` is strictly
        decreasing (StationSpec REJECTS curves whose degree-2 fit is not:
        models._curve_fit), so the root is unique; reverse iterates only
        narrow the bracket from below (max-effort shutoff is tried ONCE per
        tick — the M2 review found re-firing it turned the bisection into
        one halving per TWO solves on marginally sized pumps). The lift is
        warm-started tick-to-tick: quasi-steady ticks settle in one solve.

        Check valves (Rückschlagklappen): a station still reversing at
        shutoff lift closes for the tick (EPANET-style link status; upstream
        pumps assume zero-lift zero-resistance bypass on reverse flow, which
        would drain the Hochbehälter backwards through the works). The rules
        re-enable it next tick, so it retries as soon as heads allow.
        """
        self.cv_closed.clear()
        stations = [m for m in self.index.producer_meta
                    if m["kind"] == "station"]
        outcome = solve_with_retry(self.net, self.settings.solver_iter)
        if not stations:
            return outcome
        pump_stds = self.net["std_types"]["pump"]
        lo = {m["name"]: 0.0 for m in stations}
        hi = {m["name"]: pump_stds[m["name"]].shutoff_bar() for m in stations}
        last: dict[str, tuple[float, float]] = {}   # secant memory (lift, g)
        # one max-effort (shutoff) try per station per tick — gating on the
        # BRACKET instead (M2 review finding) re-fired shutoff after every
        # reverse iterate (a forward point AT shutoff never shrinks hi), so
        # marginally sized pumps burnt half the solve budget re-computing
        # the identical reverse state and cold starts exhausted the cap
        max_effort_tried = {m["name"]: False for m in stations}
        for _ in range(MAX_STATION_SOLVES):
            if not outcome.converged:
                return outcome
            settled = True
            for meta in stations:
                name, el = meta["name"], int(meta["element"])
                if not bool(self.net.pump.at[el, "in_service"]):
                    continue
                if el not in self.net.res_pump.index:
                    continue
                std = pump_stds[name]
                mdot = float(self.net.res_pump.at[el, "mdot_from_kg_per_s"])
                log.debug("station %s: lift=%.4f mdot=%.4f", name,
                          std.lift_bar, mdot)
                if mdot < -CV_EPS_KG_PER_S:
                    settled = False
                    # reverse flow = the lift is BELOW the reversal cliff:
                    # the root (if any) lies above — bracket accordingly
                    if std.shutoff_bar() - std.lift_bar < CV_CLOSE_MARGIN_BAR:
                        # reversing at (essentially) shutoff head: no
                        # forward operating point exists — the clapper shuts
                        self.net.pump.at[el, "in_service"] = False
                        self.cv_closed.add(name)
                    else:
                        lo[name] = max(lo[name], std.lift_bar)
                        if not max_effort_tried[name]:
                            # decisive single try: forward at shutoff
                            # brackets the root, reverse there closes the
                            # valve next round
                            max_effort_tried[name] = True
                            std.lift_bar = std.shutoff_bar()
                        else:
                            std.lift_bar = 0.5 * (lo[name] + hi[name])
                        last.pop(name, None)
                    continue
                max_effort_tried[name] = True   # forward point exists
                q_m3h = max(0.0, mdot) / RHO_KG_M3 * 3600.0
                g = std.curve_lift_bar(q_m3h) - std.lift_bar
                if abs(g) <= LIFT_TOL_BAR:
                    continue
                settled = False
                if g > 0:                       # root lies above this lift
                    lo[name] = max(lo[name], std.lift_bar)
                else:
                    hi[name] = min(hi[name], std.lift_bar)
                # secant only from FORWARD-flow points: the reverse/deadhead
                # state (q = 0, curve plateau at shutoff) carries no local
                # gradient — a secant across that cliff leaps wildly
                prev = last.get(name)
                nxt = None
                if (q_m3h > 0.0 and prev is not None
                        and abs(std.lift_bar - prev[0]) > 1e-9
                        and abs(g - prev[1]) > 1e-12):
                    nxt = (std.lift_bar
                           - g * (std.lift_bar - prev[0]) / (g - prev[1]))
                if nxt is None or not lo[name] < nxt < hi[name]:
                    # local damped step (≤ 1 bar), never a leap across the
                    # bracket — the warm-started lift is trusted to be near
                    # the root; the bracket midpoint is the last resort
                    nxt = std.lift_bar + max(-1.0, min(1.0, g))
                    if not lo[name] < nxt < hi[name]:
                        nxt = 0.5 * (lo[name] + hi[name])
                if q_m3h > 0.0:
                    last[name] = (std.lift_bar, g)
                std.lift_bar = nxt
            if settled:
                return outcome
            outcome = solve_with_retry(self.net, self.settings.solver_iter)
        if outcome.converged:
            # honest degradation: frames carry curve-inconsistent lift
            log.warning("station operating point not settled after %d solves",
                        MAX_STATION_SOLVES)
            return SolveOutcome(
                True, "degraded", outcome.tier, outcome.solve_ms,
                error="station operating point not settled after "
                      f"{MAX_STATION_SOLVES} solves")
        return outcome

    def _reset_initialization(self) -> None:
        """After swaps/failures: build-time pressures (pn_bar only)."""
        self.net.junction["pn_bar"] = self.index.init_pn_bar

    def _reused_payload(self) -> dict:
        """Last converged physics (or empty shells) + live controls."""
        payload = dict(self._last_payload) if self._last_payload else {
            "junctions": [], "pipes": [], "consumers": [],
            "producers": [], "tanks": [], "emitters": [], "wellfields": [],
            "summary": {}, "findings": [],
        }
        payload["controls"] = self._controls_dict()
        return payload

    # -- environment overrides (M3 weather knob) --------------------------------

    def set_environment(self, t_offset_c: float | None = None,
                        dryness_override: object = ...) -> dict:
        """Apply runtime weather overrides and rebuild the demand profiles.

        Configuration, not physics state (scenario recipes save it): a
        temperature offset on the bundle's series and an optional dryness
        override (None clears back to the bundle's values). Runtime-added
        consumers keep their constant demand — only bundle consumers carry
        archetype profiles."""
        if t_offset_c is not None:
            self.environment.t_offset_c = float(t_offset_c)
        if dryness_override is not ...:
            self.environment.dryness_override = (
                None if dryness_override is None else float(dryness_override))
        self._rebuild_demand_profiles()
        return self.environment.as_dict()

    def _rebuild_demand_profiles(self) -> None:
        """Rebuild engine profiles BY CONSUMER IDENTITY, never by position:
        runtime CRUD deletes/appends profile rows positionally while
        inputs.consumers stays immutable — a positional write after a
        removal crashed (broadcast ValueError → 500) or silently shifted
        every later consumer onto its neighbour's archetype profile (M3
        review, critical). Bundle consumers are matched by their build
        name; removed ones are skipped; runtime-added rows keep their
        constant demand untouched."""
        fresh = build_demand_profiles(
            self.inputs, self.settings.steps_per_day, env=self.environment)
        bundle_row = {
            (c.name or f"consumer_{c.node}"): bi
            for bi, c in enumerate(self.inputs.consumers.consumers)}
        for pos, name in enumerate(self.index.consumer_names):
            bi = bundle_row.get(name)
            if bi is not None:
                self.profiles.mdot_kg_per_s[pos, :] = fresh[bi]
        self.profiles.t_air_c = (
            _resample_staircase(self.inputs.environment.t_air_c,
                                self.profiles.steps)
            + self.environment.t_offset_c)

    # -- M5 pressure-dependent hydraulics (PDA + emitters) --------------------

    def set_pda(self, enabled: bool) -> dict:
        """Toggle pressure-driven demand. OFF reverts to fixed demand (the
        M0 behavior — undersupply shows as negative pressure, the teaching
        contrast). Resets any live delivery scaling to full."""
        self.pda.enabled = bool(enabled)
        if len(self.index.consumers):
            self.net.sink.loc[self.index.consumers, "scaling"] = 1.0
        return {"pda_enabled": self.pda.enabled}

    def _node_pressure(self, node: str) -> float:
        """Best current pressure estimate at *node* for sizing an emitter:
        the last converged frame, else the built-in pn_bar."""
        if self._last_payload:
            for j in self._last_payload.get("junctions", []):
                if j["name"] == node and j["p_bar"] is not None:
                    return float(j["p_bar"])
        jj = self.index.junction[node]
        return float(self.net.junction.at[jj, "pn_bar"])

    def open_hydrant(self, node: str, target_m3_h: float,
                     duration_ticks: int | None = None,
                     name: str | None = None) -> dict:
        """Open a fire hydrant sized to draw *target_m3_h* at the node's
        current pressure (W 405 fire flow). As the zone responds the
        emitter follows the pressure, so a starved node delivers LESS than
        target (the emitter is capped at the target — a hydrant of a given
        nozzle cannot pull MORE than rated even at high pressure). The
        honest fire-flow-vs-1.5-bar teaching signal."""
        name = name or f"Hydrant {node}"
        C = EmitterController.hydrant_coefficient(
            float(target_m3_h), self._node_pressure(node))
        max_mdot = float(target_m3_h) / 3600.0 * RHO_KG_M3
        em = self.emitters.add(
            name, node, "hydrant", C, exponent=0.5, start_tick=self._abs_tick,
            duration_ticks=duration_ticks, target_m3_h=float(target_m3_h),
            max_mdot_kg_per_s=max_mdot)
        return em.payload()

    def place_burst(self, node: str, area_m2: float,
                    name: str | None = None) -> dict:
        """Place a pipe burst as a large orifice (``C = Cd·A·√(2ρ)``) at a
        junction — local pressure crater + upstream flow spike."""
        name = name or f"Rohrbruch {node}"
        C = EmitterController.burst_coefficient(float(area_m2))
        em = self.emitters.add(
            name, node, "burst", C, exponent=0.5, start_tick=self._abs_tick,
            duration_ticks=None)
        return em.payload()

    def set_leakage(self, coefficient_per_km: float) -> dict:
        """Seed distributed background leakage: a FAVAD emitter
        (``mdot = C·p^1.15``) at every NETWORK junction, ``C`` proportional
        to the incident pipe length. Rising ``coefficient_per_km`` raises
        the night minimum flow (MNF); lowering pressure then measurably
        cuts the loss (pressure management). Clears any previous leak set.
        Head-source nodes (tank/ext_grid) are excluded — a leak on the
        fixed-pressure boundary is meaningless (as with consumers)."""
        for nm in [n for n, e in self.emitters.emitters.items()
                   if e.kind == "leak"]:
            self.emitters.remove(nm)
        self.leak_coefficient_per_km = float(coefficient_per_km)
        head_nodes = {m["node"] for m in self.index.producer_meta
                      if m["kind"] in ("slack", "tank")}
        # half the incident pipe length attaches to each end node
        incident_km: dict[str, float] = {}
        for p in self.inputs.pipes.pipes:
            for nd in (p.from_node, p.to_node):
                incident_km[nd] = incident_km.get(nd, 0.0) + 0.5 * p.length_km
        n = 0
        for node, km in incident_km.items():
            if (km <= 0 or node not in self.index.junction
                    or node in head_nodes):
                continue
            C = float(coefficient_per_km) * km
            if C <= 0:
                continue
            self.emitters.add(f"Leckage {node}", node, "leak", C,
                              exponent=1.15, start_tick=self._abs_tick,
                              duration_ticks=None)
            n += 1
        return {"leaks": n, "coefficient_per_km": float(coefficient_per_km)}

    def remove_emitter(self, name: str) -> bool:
        return self.emitters.remove(name)

    # -- M6 raw-water side (wells / aquifer) ---------------------------------

    def set_drought(self, factor: float) -> dict:
        """Set the recharge drought factor on every aquifer (1.0 = normal,
        0 = no recharge). Config (scenario-saved); the aquifer level then
        declines under abstraction, capping well production (Lauenau)."""
        f = max(0.0, float(factor))
        for wf in self.wellfields:
            wf.aquifer.drought_factor = f
        return {"drought_factor": f,
                "wellfields": [wf.name for wf in self.wellfields]}

    def regenerate_well(self, wellfield: str, well: str) -> dict:
        """Well regeneration (W 130): restore ~90 % of the nameplate Q/s."""
        wf = next((w for w in self.wellfields if w.name == wellfield), None)
        if wf is None:
            raise KeyError(f"unknown well field {wellfield!r}")
        w = next((x for x in wf.wells if x.name == well), None)
        if w is None:
            raise KeyError(f"unknown well {well!r} in {wellfield!r}")
        w.regenerate()
        return {"wellfield": wellfield, "well": well,
                "spec_capacity_now": round(w.spec_capacity_now, 3)}

    def clear_leakage(self) -> int:
        names = [n for n, e in self.emitters.emitters.items()
                 if e.kind == "leak"]
        for nm in names:
            self.emitters.remove(nm)
        return len(names)

    # -- operations reset (scenario load / bulk-export replay) -----------------

    def reset_operations(self) -> None:
        """Run-state back to the bundle's initial operating point: tank
        levels to level_initial, station modes to their configured state
        (recipes keep configuration; this is the deterministic-replay
        normalization the exporter and scenario loads share)."""
        for tank in self.tanks:
            tank.reset()
            tank.write_p(self.net)
        self.station_modes = dict(self._initial_station_modes)
        self.rules.reset()
        self.cv_closed.clear()
        # station operating points back to the cold-start seed — a replay
        # must not inherit the live warm lift (deterministic exports)
        for meta in self.index.producer_meta:
            if meta["kind"] != "station":
                continue
            std = self.net["std_types"]["pump"].get(meta["name"])
            if std is not None and hasattr(std, "lift_seed_bar"):
                std.lift_bar = float(std.lift_seed_bar)
        # weather overrides normalize like station modes (ONE doctrine —
        # M3 review): the replay starts from the bundle's environment; a
        # scenario recipe restores its own overrides afterwards
        if (self.environment.t_offset_c
                or self.environment.dryness_override is not None):
            self.environment = EnvironmentState()
            self._rebuild_demand_profiles()
        # compliance rolling state fresh (sustained/stagnation windows)
        self.compliance.reset()
        # M5 emitters are run-state (a live burst / open hydrant) — a
        # deterministic replay starts clean; the scenario recipe re-adds
        # its own emitter actions afterwards
        self.emitters.clear()
        self.leak_coefficient_per_km = 0.0
        # M6 raw side back to its initial point (aquifer level, well ageing,
        # abstraction/energy counters) — the drought override is config,
        # restored by the scenario recipe
        for wf in self.wellfields:
            wf.reset()
            tank = self._tanks_by_name.get(wf.break_tank_name)
            if tank is not None:
                tank.external_inflow_kg_per_s = 0.0
        if len(self.index.consumers):
            self.net.sink.loc[self.index.consumers, "scaling"] = 1.0

    # -- derived quantities & wire payload -------------------------------------

    def _controls_dict(self) -> dict:
        # station operator modes are config (scenario-saved); blind_spot is
        # meta-information about the sensor layout.
        return {"blind_spot": self._blind_spot,
                "stations": dict(self.station_modes)}

    def _collect(self, tick: int) -> dict:
        net, idx = self.net, self.index

        # the four ground-truth wire keys + aux — shared with the forward
        # observer (estimator.py) so twin and truth use the same formulas
        physics = collect_physics(net, idx, tanks=self.tanks,
                                  emitters=self.emitters)
        worst_pos = physics["aux"]["worst_pos"]

        producers = []
        for meta in idx.producer_meta:
            # wire id = platform-unique pid, never the per-kind element index
            entry = {"id": int(meta["pid"]), "kind": meta["kind"],
                     "name": meta["name"], "node": meta["node"]}
            if meta["kind"] == "slack":
                # SIGNED feed since M2 (multi-source nets): positive =
                # supplying the net, negative = absorbing (exporting) —
                # abs() made an absorbing slack indistinguishable from a
                # supplying one (M2 review finding)
                entry.update({
                    "p_bar": _r(net.ext_grid.at[meta["element"], "p_bar"]),
                    "mdot_kg_per_s": _r(
                        -net.res_ext_grid.mdot_kg_per_s.loc[meta["element"]]),
                })
            elif meta["kind"] == "tank":
                tank = self._tanks_by_name.get(meta["name"])
                if tank is not None:
                    entry.update({
                        "p_bar": _r(tank.p_bar()),
                        "mdot_kg_per_s": _r(tank.mdot_kg_per_s),
                        "level_m": _r(tank.level_m, 4),
                    })
            elif meta["kind"] == "prv":
                # PRV entries are STATION SCADA (real Druckminderer stations
                # carry in/out gauges + a flowmeter — TF §7): like the source
                # they stay on the wire in strict mode. mdot is SIGNED
                # (negative = reverse flow through the valve) and `reducing`
                # honestly flags the press_control failure modes the M1
                # static PRV cannot prevent (boosting when the upstream head
                # collapses, back-feeding) — M2+ supervision acts on them.
                r = net.res_press_control.loc[meta["element"]]
                entry.update({
                    "p_set_bar": _r(net.press_control.at[
                        meta["element"], "controlled_p_bar"]),
                    "p_out_bar": _r(r.p_to_bar),
                    "p_in_bar": _r(r.p_from_bar),
                    "mdot_kg_per_s": _r(r.mdot_from_kg_per_s),
                    "reducing": bool(r.deltap_bar < 0),
                })
            elif meta["kind"] == "station":
                running = bool(net.pump.at[meta["element"], "in_service"])
                entry["running"] = running
                entry["mode"] = self.station_modes.get(meta["name"], "auto")
                # honest station SCADA: the check valve closed against
                # reverse flow this tick (pump commanded on, delivering 0)
                entry["cv_closed"] = meta["name"] in self.cv_closed
                if running and meta["element"] in net.res_pump.index:
                    r = net.res_pump.loc[meta["element"]]
                    entry.update({
                        "p_in_bar": _r(r.p_from_bar),
                        "p_out_bar": _r(r.p_to_bar),
                        "mdot_kg_per_s": _r(r.mdot_from_kg_per_s),
                    })
                else:
                    entry.update({"p_in_bar": None, "p_out_bar": None,
                                  "mdot_kg_per_s": _r(0.0)})
            producers.append(entry)

        payload = {
            "junctions": physics["junctions"],
            "pipes": physics["pipes"],
            "consumers": physics["consumers"],
            "producers": producers,
            "tanks": [t.payload() for t in self.tanks],
            # M5 emitters (leaks/hydrants/bursts) — station SCADA-like
            # equipment, kept on the wire in strict mode (an operator sees
            # an open hydrant / a reported burst)
            "emitters": [e.payload() for e in self.emitters.emitters.values()],
            # M6 well fields — raw-side SCADA (aquifer level, production,
            # water right, energy); station equipment, visible in strict mode
            "wellfields": [wf.payload() for wf in self.wellfields],
            "summary": physics["summary"],
            "controls": self._controls_dict(),
        }
        # observed layer: projection of the truth payload onto the sensored
        # elements — every frame carries measurements/observed_summary
        payload["measurements"], payload["observed_summary"] = \
            self.measurements.observe(payload, tick)

        # blind-spot flag: true when the operator's pressure view misses the
        # TRUE min-pressure worst point — either no usable reading at all,
        # or the truth's critical consumer carries no meter.
        obs = payload["observed_summary"] or {}
        if len(idx.consumers):
            worst_el = int(idx.consumers[worst_pos])
            self._blind_spot = (
                obs.get("p_min_bar") is None
                or worst_el not in self.measurements.consumer_meters)
        else:
            self._blind_spot = None
        payload["controls"]["blind_spot"] = self._blind_spot
        return payload

    # -- runtime consumer CRUD -------------------------------------------------
    #
    # Blueprint pattern throughout: mutate the live net directly (no rebuild),
    # extend the dense profile arrays + index records (row order = element
    # order), reset the initialization after topology CRUD, tolerate racing
    # solves (a poisoned step self-heals next tick). Dropped pandapipes
    # element indices are REUSED by the next create — bookkeeping keys on
    # current tables, never creation history.

    def add_consumer(
        self,
        node: str,
        mdot_kg_per_s: float,
        name: str | None = None,
        recipe: dict | None = None,
    ) -> dict:
        """Place a fixed-demand sink at an existing node.

        *recipe* goes into the consumer op log so scenario save/load replays
        the placement deterministically. Raises ``KeyError`` for an unknown
        node (API: 400).
        """
        idx, p = self.index, self.profiles
        jj = idx.junction[node]  # KeyError -> unknown node (API: 400)
        head_nodes = {m["node"] for m in idx.producer_meta
                      if m["kind"] in ("slack", "tank")}
        if node in head_nodes:
            # a sink on the fixed-pressure junction is served straight from
            # the boundary and pins the Schlechtpunkt to the source's low
            # gauge pressure (M2 review finding; loader rejects it too)
            raise KeyError(
                f"node {node!r} is a head source (tank/ext_grid) — "
                "consumers must attach to network nodes")
        name = name or f"consumer_{node}_{len(idx.consumers)}"
        # duplicate names are load-bearing keys (compliance counters,
        # profile identity rebuild, scenario meter replay) — reject
        # BEFORE the sink exists (no orphaned element on error)
        if name in idx.consumer_names:
            raise KeyError(
                f"consumer name {name!r} already exists — names must be "
                "unique")
        mdot = float(mdot_kg_per_s)
        sk = pp.create_sink(self.net, junction=jj, mdot_kg_per_s=mdot,
                            name=name)
        idx.consumers = np.append(idx.consumers, sk)
        idx.consumer_names.append(name)
        idx.consumer_nodes.append(node)
        idx.consumer_kinds.append("consumer")
        # M4: runtime consumers get the EG minimum-pressure requirement —
        # never silently exempt from the W 400-1 check (review)
        self.compliance.register_consumer(name, node, storeys=1)
        p.mdot_kg_per_s = np.vstack(
            [p.mdot_kg_per_s, np.full((1, p.steps), mdot)])
        self.consumer_ops.append({
            "op": "add_consumer", "node": node, "name": name,
            "mdot_kg_per_s": mdot, **(recipe or {})})
        self._reset_initialization()  # topology CRUD → cold init
        return {"id": int(sk), "name": name, "node": node,
                "kind": "consumer", "mdot_demand_kg_per_s": mdot}

    def remove_consumer(self, element: int) -> dict:
        """Remove a consumer by sink element index.

        Raises ``KeyError`` for an unknown element and ``ValueError`` when it
        is the last consumer (a net without any demand teaches nothing — the
        API maps that to a 409 conflict, keeping the fork parent's surface
        semantics).
        """
        idx, p = self.index, self.profiles
        pos_arr = np.nonzero(idx.consumers == int(element))[0]
        if len(pos_arr) == 0:
            raise KeyError(f"no consumer with element index {element}")
        if len(idx.consumers) <= 1:
            raise ValueError(
                "cannot remove the last consumer — a net without any demand "
                "has nothing to simulate")
        pos = int(pos_arr[0])
        name = idx.consumer_names[pos]
        kind = idx.consumer_kinds[pos] if idx.consumer_kinds else "consumer"
        node = idx.consumer_nodes[pos]
        self.net.sink.drop(index=int(element), inplace=True)
        if "res_sink" in self.net and len(self.net.res_sink):
            self.net.res_sink.drop(
                index=int(element), inplace=True, errors="ignore")
        idx.consumers = np.delete(idx.consumers, pos)
        del idx.consumer_names[pos]
        del idx.consumer_nodes[pos]
        if idx.consumer_kinds:
            del idx.consumer_kinds[pos]
        p.mdot_kg_per_s = np.delete(p.mdot_kg_per_s, pos, axis=0)
        # compliance requirement + sustained counter go with the consumer
        self.compliance.unregister_consumer(name)
        # a removed consumer takes its meter with it
        self.measurements.prune({int(c) for c in idx.consumers})
        self.consumer_ops.append({"op": "remove_consumer", "name": name})
        self._reset_initialization()
        return {"id": int(element), "name": name, "node": node, "kind": kind}
