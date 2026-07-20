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

from .config import Settings, get_settings
from .estimator import EstimationConfig, ForwardObserver
from .net_inputs import NetInputs
from .network_builder import ProfileArrays, build_network
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
    # -- supply/equipment (always visible) --
    producers: list = field(default_factory=list)
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
    of tier 1; tiers 2 and 3 use 3x (single-knob semantics)."""
    n = int(iter_base)
    return [
        dict(mode="hydraulics", iter=n, friction_model="colebrook"),
        dict(mode="hydraulics", iter=3 * n, friction_model="colebrook"),
        dict(mode="hydraulics", iter=3 * n, friction_model="nikuradse"),
    ]


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

def collect_physics(net, idx) -> dict:
    """The four ground-truth wire keys + aux values from a SOLVED *net*.

    Returns ``{junctions, pipes, consumers, summary, aux}`` where ``aux``
    carries ``worst_pos`` (consumer row of the min-pressure worst point) for
    the caller's blind-spot flag and the key_points measurement preset.
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

    # feed: pandapipes reports ext_grid withdrawal as negative mdot — the
    # magnitude is what the wire carries (pinned by the hillside regression)
    mdot_feed = float(abs(net.res_ext_grid.mdot_kg_per_s.loc[idx.ext_grid]))
    demand_sum = float(mdot_demand.sum())
    delivered_sum = float(mdot_delivered.sum())

    summary = {
        "p_min_bar": _r(p_min_bar),
        "worst_consumer": worst_consumer,
        "worst_node": worst_node,
        "mdot_feed_kg_per_s": _r(mdot_feed),
        "mdot_demand_kg_per_s": _r(demand_sum),
        "mdot_delivered_kg_per_s": _r(delivered_sum),
        "balance_err_kg_per_s": _r(mdot_feed - delivered_sum),
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
        self.net, self.profiles = build_network(
            inputs, steps_per_day=self.settings.steps_per_day)
        self.index = self.profiles.index

        # runtime consumer op log (recipes, scenario save/replay)
        self.consumer_ops: list[dict] = []

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
        """Write the tick's demand onto the sink table (single write in M0;
        the M3 demand engine and M5 PDA/emitters extend this seam)."""
        if len(self.index.consumers):
            self.net.sink.loc[self.index.consumers, "mdot_kg_per_s"] = \
                self.profiles.mdot_kg_per_s[:, tick]

    # -- the step ------------------------------------------------------------

    def run_step(self, step: int, day: int) -> StepResult:
        """One simulation step. Never raises for non-convergence."""
        tick = self._tick(step, day)
        apply_error: str | None = None
        try:
            self._apply_step(tick)
        except Exception as exc:  # racing CRUD may poison one step — self-heal
            apply_error = f"apply_step: {type(exc).__name__}: {exc}"
            log.warning("apply_step failed (frame degrades to failed): %s",
                        apply_error)

        if apply_error is None:
            outcome = solve_with_retry(self.net, self.settings.solver_iter)
        else:
            outcome = SolveOutcome(False, "failed", 0, 0.0, error=apply_error)

        if outcome.converged:
            try:
                payload = self._collect(tick)
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

    def _reset_initialization(self) -> None:
        """After swaps/failures: build-time pressures (pn_bar only)."""
        self.net.junction["pn_bar"] = self.index.init_pn_bar

    def _reused_payload(self) -> dict:
        """Last converged physics (or empty shells) + live controls."""
        payload = dict(self._last_payload) if self._last_payload else {
            "junctions": [], "pipes": [], "consumers": [],
            "producers": [], "summary": {},
        }
        payload["controls"] = self._controls_dict()
        return payload

    # -- derived quantities & wire payload -------------------------------------

    def _controls_dict(self) -> dict:
        # M0: no controllers exist yet (tank/pump/rule controllers arrive in
        # M2). The blind-spot flag survives as meta-information about the
        # sensor layout.
        return {"blind_spot": self._blind_spot}

    def _collect(self, tick: int) -> dict:
        net, idx = self.net, self.index

        # the four ground-truth wire keys + aux — shared with the forward
        # observer (estimator.py) so twin and truth use the same formulas
        physics = collect_physics(net, idx)
        worst_pos = physics["aux"]["worst_pos"]

        producers = []
        for meta in idx.producer_meta:
            # wire id = platform-unique pid, never the per-kind element index
            entry = {"id": int(meta["pid"]), "kind": meta["kind"],
                     "name": meta["name"], "node": meta["node"]}
            if meta["kind"] == "slack":
                entry.update({
                    "p_bar": _r(net.ext_grid.at[meta["element"], "p_bar"]),
                    "mdot_kg_per_s": _r(abs(
                        net.res_ext_grid.mdot_kg_per_s.loc[meta["element"]])),
                })
            elif meta["kind"] == "prv":
                # PRV entries are STATION SCADA (real Druckminderer stations
                # carry in/out gauges + a flowmeter — TF §7): like the source
                # they stay on the wire in strict mode. mdot is SIGNED
                # (negative = reverse flow through the valve) and `reducing`
                # honestly flags the press_control failure modes the M1
                # static PRV cannot prevent (boosting when the upstream head
                # collapses, back-feeding) — M2 supervision acts on them.
                r = net.res_press_control.loc[meta["element"]]
                entry.update({
                    "p_set_bar": _r(net.press_control.at[
                        meta["element"], "controlled_p_bar"]),
                    "p_out_bar": _r(r.p_to_bar),
                    "p_in_bar": _r(r.p_from_bar),
                    "mdot_kg_per_s": _r(r.mdot_from_kg_per_s),
                    "reducing": bool(r.deltap_bar < 0),
                })
            producers.append(entry)

        payload = {
            "junctions": physics["junctions"],
            "pipes": physics["pipes"],
            "consumers": physics["consumers"],
            "producers": producers,
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
        name = name or f"consumer_{node}_{len(idx.consumers)}"
        mdot = float(mdot_kg_per_s)
        sk = pp.create_sink(self.net, junction=jj, mdot_kg_per_s=mdot,
                            name=name)
        idx.consumers = np.append(idx.consumers, sk)
        idx.consumer_names.append(name)
        idx.consumer_nodes.append(node)
        idx.consumer_kinds.append("consumer")
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
        # a removed consumer takes its meter with it
        self.measurements.prune({int(c) for c in idx.consumers})
        self.consumer_ops.append({"op": "remove_consumer", "name": name})
        self._reset_initialization()
        return {"id": int(element), "name": name, "node": node, "kind": kind}
