"""Estimation layer — the forward-simulation observer (roadmap §4.10, M7).

pandapipes has **no state estimator** (nothing like pandapower's WLS), so the
``estimated`` layer is a *forward observer* ("digital-twin estimator"): a
second pandapipes net driven **only** by what a waterworks operator can
actually know —

* **source & equipment SCADA** (always measured): the head-source pressures,
  the tank levels (→ their ext_grid heads), the pump on/off dispatch and the
  PRV setpoints — real waterworks meter all of these, so the twin copies the
  live dispatch;
* **metered consumers**: their measured delivered flow, respecting fidelity
  (standard-mode meters deliver 15-min-window means; a cold-start ``None``
  falls back to the prior);
* **unmetered consumers**: *demand priors* — the expected archetype profile
  from planning data, **never** the live per-tick truth;
* **weather**: the operator's own temperature/dryness knob (the demand priors
  are computed with it, exactly like the truth).

The twin re-derives its own pump operating points (``solve_hydraulic``, the
same station secant the truth uses) — never inheriting the truth's operating
point, which responds to the true demand. Its deviation from the
**measurements** at sensored points quantifies estimate quality (the
``error`` field) — computed against measurements, not truth, so it stays valid
in strict mode. Twin non-convergence is data: the estimate simply stops
refreshing (stale attachment), never a crash.

Honesty rules (roadmap §4.10 tripwires; pinned in
``tests/test_estimation_m7.py``):

* the estimate must NOT contain information no sensor could deliver — an
  anomaly injected on an *unmetered* consumer stays invisible (its estimated
  values remain the prior), and a **burst at an unmetered node** does not
  appear (every emitter withdrawal is zeroed in the twin — a hidden leak or
  burst is not operator knowledge), while the same anomaly on a *metered*
  consumer propagates;
* with the ``clear`` preset (source SCADA only) the estimated per-consumer
  flows equal the priors exactly (the twin is *driven* by them);
* the ``error`` metric rises visibly as sensor coverage shrinks.

Priors read ``sim.inputs`` (the immutable five-file planning contract) plus
the runtime placement *recipes* — never the mutable runtime
``sim.profiles``/net tables, so a runtime anomaly injection cannot leak into
the prior basis.

Throttling: two gates ANDed — a metering raster (estimates only at 15-min
window boundaries when the placed devices run in standard mode; no new
readings exist in between) and a wall-clock self-throttle (``throttle_factor
×`` the last estimation's own runtime must have elapsed). The last estimate is
attached to every subsequent frame until refreshed; ``step``/``day``/``seq``
tell the UI how stale it is.
"""
from __future__ import annotations

import copy
import logging
import time
from dataclasses import asdict, dataclass

import numpy as np

from .demand import build_demand_profiles
from .sensors import _r

log = logging.getLogger(__name__)

#: What unmetered consumers are assumed to do.
PRIOR_BASES = ("archetype", "design")


@dataclass
class EstimationConfig:
    """Operator-facing estimation policy (GET/POST /estimation/config).

    ``enabled`` — the observer runs at all (default **on** in M7).
    ``prior_basis`` — the expectation for unmetered consumers:
      * ``archetype``: the demand engine's *expected* profile (archetype ×
        day × weather, the stochastic per-tick noise dropped), i.e. the
        planning-basis load forecast;
      * ``design``: the crude teaching prior — the flat contracted base
        demand, no diurnal/seasonal shape.
    ``throttle_factor`` — wall-clock self-throttle: a new estimate only after
    ``throttle_factor × solve_ms`` of the previous one has elapsed (~2×).
    """

    enabled: bool = True
    prior_basis: str = "archetype"
    throttle_factor: float = 2.0

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Priors — the operator's expectation for unmetered consumers
# ---------------------------------------------------------------------------

@dataclass
class PriorBook:
    """Dense expected-demand array [n_cons, T] aligned with the CURRENT
    consumer row order. Everything here derives from planning data
    (``sim.inputs`` + placement recipes) — deliberately never from the
    runtime profile arrays, which carry the stochastic per-tick truth."""

    mdot_kg_per_s: np.ndarray   # [n_cons, T] expected consumer demand

    def demand_at(self, tick: int) -> np.ndarray:
        return self.mdot_kg_per_s[:, int(tick)].copy()


def build_prior_book(sim, basis: str) -> PriorBook:
    """Assemble the demand priors for the CURRENT consumer inventory of *sim*.

    Sources, matched by NAME (the convention scenario replay uses):

    1. planning contract (``sim.inputs.consumers``) — ``archetype`` basis:
       the demand engine's expected (noise-free) profile with the operator's
       current weather knob; ``design`` basis: the flat contracted base;
    2. runtime placement recipe (``sim.consumer_ops``) — a consumer added at
       runtime takes its configured base demand (flat plan);
    3. fallback — zero (no plan for it; honest).
    """
    idx, inputs = sim.index, sim.inputs
    spd = sim.profiles.steps_per_day
    steps = sim.profiles.steps
    n = len(idx.consumers)
    out = np.zeros((n, steps))

    # the expected (noise-free) demand for the FILE consumers, computed with
    # the operator's current weather override — the load forecast, not the
    # realized truth (archetype basis only; design uses the flat base)
    expected_file = (
        build_demand_profiles(inputs, spd, env=sim.environment, noise=False)
        if basis != "design" else None)
    file_row = {(c.name or f"consumer_{c.node}"): bi
                for bi, c in enumerate(inputs.consumers.consumers)}
    base_file = {(c.name or f"consumer_{c.node}"): float(c.mdot_kg_per_s)
                 for c in inputs.consumers.consumers}
    op_by_name = {str(op["name"]): op for op in sim.consumer_ops
                  if op.get("op") in ("add_consumer", "add_bypass")
                  and op.get("name")}

    for i in range(n):
        name = idx.consumer_names[i]
        if basis != "design" and name in file_row:
            out[i] = expected_file[file_row[name]]
        elif name in base_file:            # design basis, planned consumer
            out[i] = base_file[name]
        elif name in op_by_name:           # runtime-added: the configured base
            out[i] = float(op_by_name[name].get("mdot_kg_per_s", 0.0))
        # else: unknown consumer, no plan → zero prior
    return PriorBook(mdot_kg_per_s=out)


# ---------------------------------------------------------------------------
# The forward observer
# ---------------------------------------------------------------------------

class ForwardObserver:
    """Maintains the twin net + priors; produces the ``estimated`` payload."""

    def __init__(self, sim, config: EstimationConfig):
        self.sim = sim
        self.config = config
        self.twin = None
        self.book: PriorBook | None = None
        self._signature: tuple | None = None
        self._emitter_els: list[int] = []
        self._twin_cv: set[str] = set()
        self.last: dict | None = None
        self.seq = 0
        self._wall = 0.0        # monotonic time of the last estimation run
        self._ms = 0.0          # its duration (drives the adaptive spacing)

    # -- twin lifecycle -------------------------------------------------------

    def _current_signature(self) -> tuple:
        sim, idx = self.sim, self.sim.index
        env = sim.environment
        return (
            tuple(int(c) for c in idx.consumers),
            tuple(idx.consumer_names),
            len(sim.net.junction), len(sim.net.pipe), len(sim.net.sink),
            self.config.prior_basis,
            round(float(env.t_offset_c), 6),
            (None if env.dryness_override is None
             else round(float(env.dryness_override), 6)),
        )

    def _ensure_twin(self) -> None:
        """(Re)build the twin + priors when topology or policy changed."""
        sig = self._current_signature()
        if self.twin is not None and sig == self._signature:
            return
        sim = self.sim
        self.twin = copy.deepcopy(sim.net)
        # honest init: the twin never inherits the truth's warm pressures —
        # build-time pn_bar like a fresh net; after each converged estimate
        # the twin warm-starts from ITSELF.
        self.twin.junction["pn_bar"] = sim.index.init_pn_bar
        self.book = build_prior_book(sim, self.config.prior_basis)
        # emitter sinks = every sink that is not a consumer (hidden reality,
        # zeroed each tick in _apply)
        cons = {int(c) for c in sim.index.consumers}
        self._emitter_els = [int(el) for el in self.twin.sink.index
                             if int(el) not in cons]
        self._signature = sig
        log.info("forward observer: twin rebuilt (%d consumers, basis=%s)",
                 len(sim.index.consumers), self.config.prior_basis)

    # -- boundary application -------------------------------------------------

    def _apply(self, tick: int, measurements: dict) -> None:
        """Write the operator's knowledge onto the twin for this tick."""
        sim, idx, tw = self.sim, self.sim.index, self.twin

        # 1. consumer demand: priors ...
        q = self.book.demand_at(tick)
        # ... overlaid with the measured delivered flow where a meter delivers
        # (fidelity-respecting: a standard-mode cold start is None → prior)
        pos_of = {int(c): i for i, c in enumerate(idx.consumers)}
        for m in measurements.get("consumers") or []:
            i = pos_of.get(int(m["id"]))
            if i is not None and m.get("mdot_kg_per_s") is not None:
                q[i] = abs(float(m["mdot_kg_per_s"]))
        if len(idx.consumers):
            tw.sink.loc[idx.consumers, "mdot_kg_per_s"] = q
            tw.sink.loc[idx.consumers, "scaling"] = 1.0

        # 2. hidden reality stays hidden: every emitter withdrawal (leak /
        # burst / hydrant) is zeroed — an unmetered draw is NOT operator
        # knowledge, so a burst at an unmetered node cannot enter the estimate
        for el in self._emitter_els:
            tw.sink.at[el, "mdot_kg_per_s"] = 0.0
            tw.sink.at[el, "scaling"] = 1.0

        # 3. operator-known boundaries: copy the live dispatch onto the twin —
        # head-source pressures + tank ext_grid heads (level SCADA), the pump
        # on/off command (tank-level dispatch), the PRV setpoints (config)
        live = sim.net
        tw.ext_grid["p_bar"] = live.ext_grid["p_bar"].values
        if len(live.pump):
            tw.pump["in_service"] = live.pump["in_service"].values
            # a check valve that shut in the TRUTH this tick is a physical
            # response to the true demand, NOT an operator command — re-open
            # those pumps so the twin re-derives its own check-valve state
            # from the prior demands (else a truth-only CV closure, e.g. from
            # a hidden burst reversing a pump, would leak into the estimate)
            for meta in sim.index.producer_meta:
                if meta["kind"] == "station" and meta["name"] in sim.cv_closed:
                    tw.pump.at[int(meta["element"]), "in_service"] = True
        if len(live.press_control):
            tw.press_control["controlled_p_bar"] = \
                live.press_control["controlled_p_bar"].values

    # -- error metric: deviation at SENSORED points ---------------------------

    def _error(self, est: dict, measurements: dict) -> dict:
        """|twin − measurement| at every sensored point, bucketed by channel
        (mass flow, pressure). Computed against MEASUREMENTS (not truth) — the
        innovation of the observer, valid in strict mode by construction."""
        dmdot: list[float] = []
        dp: list[float] = []

        est_cons = {int(c["id"]): c for c in est["consumers"]}
        for m in measurements.get("consumers") or []:
            c = est_cons.get(int(m["id"]))
            if c is None:
                continue
            if (m.get("mdot_kg_per_s") is not None
                    and c["mdot_kg_per_s"] is not None):
                dmdot.append(abs(abs(float(m["mdot_kg_per_s"]))
                                 - abs(c["mdot_kg_per_s"])))
            if m.get("p_bar") is not None and c["p_bar"] is not None:
                dp.append(abs(float(m["p_bar"]) - c["p_bar"]))

        est_junc = {j["name"]: j for j in est["junctions"]}
        for nm in measurements.get("nodes") or []:
            j = est_junc.get(nm["node"])
            if (j is not None and nm.get("p_bar") is not None
                    and j["p_bar"] is not None):
                dp.append(abs(float(nm["p_bar"]) - j["p_bar"]))

        # source SCADA (always measured): the feed flow + the source pressure
        plant = measurements.get("plant") or {}
        s = est["summary"]
        if (plant.get("mdot_kg_per_s") is not None
                and s.get("mdot_feed_kg_per_s") is not None):
            dmdot.append(abs(float(plant["mdot_kg_per_s"])
                             - s["mdot_feed_kg_per_s"]))
        src = est_junc.get(self.sim.index.ext_grid_node)
        if (plant.get("p_bar") is not None and src is not None
                and src["p_bar"] is not None):
            dp.append(abs(float(plant["p_bar"]) - src["p_bar"]))

        def agg(vals: list[float]) -> tuple:
            return ((_r(max(vals)), _r(sum(vals) / len(vals)))
                    if vals else (None, None))

        max_dm, mean_dm = agg(dmdot)
        max_dp, mean_dp = agg(dp)
        return {
            "max_dmdot_kg_per_s": max_dm, "mean_dmdot_kg_per_s": mean_dm,
            "max_dp_bar": max_dp, "mean_dp_bar": mean_dp,
            "n_points": len(dmdot) + len(dp),
        }

    # -- the estimate ---------------------------------------------------------

    def maybe_estimate(self, payload: dict, tick: int,
                       step: int, day: int) -> dict | None:
        """Refresh the estimate if the gates allow; return the current one.

        Called from ``Simulator.run_step`` after a converged truth solve.
        Two gates ANDed: the metering raster (standard-mode devices publish
        only at window boundaries — no new information in between) and the
        wall-clock self-throttle. Estimation failure keeps the last estimate
        attached (stale) — failure is data.
        """
        if not self.config.enabled:
            return None
        ms = self.sim.measurements
        raster = (ms.window_steps
                  if ms.mode == "standard"
                  and (ms.consumer_meters or ms.node_sensors) else 1)
        now = time.monotonic()
        throttled = (now - self._wall
                     < self.config.throttle_factor * self._ms / 1000.0)
        if int(tick) % raster != 0 or throttled:
            return self.last

        try:
            est = self._estimate(payload, tick)
        except Exception:  # estimation failure is data, never a crash
            log.exception("forward observer failed — estimate stays stale")
            est = None
        self._wall = time.monotonic()   # stamped even on failure (no retry
        if est is not None:             # storm against a broken twin)
            est["step"], est["day"] = int(step), int(day)
            self.seq += 1
            est["seq"] = self.seq
            self._ms = float(est["solve_ms"] or 0.0)
            self.last = est
        return self.last

    def _estimate(self, payload: dict, tick: int) -> dict | None:
        from .simulator import collect_physics, solve_hydraulic

        self._ensure_twin()
        self._apply(tick, payload.get("measurements") or {})
        outcome = solve_hydraulic(
            self.twin, self.sim.settings.solver_iter,
            self.sim.index.producer_meta, self._twin_cv)
        if not outcome.converged:
            return None
        # twin warm start — from its OWN converged pressures
        self.twin.junction["pn_bar"] = self.twin.res_junction.p_bar.values

        physics = collect_physics(self.twin, self.sim.index,
                                  tanks=None, emitters=None)
        est = {
            "junctions": physics["junctions"],
            "pipes": physics["pipes"],
            "consumers": physics["consumers"],
            "summary": physics["summary"],
            "solver_status": outcome.status,
            "solve_ms": _r(outcome.solve_ms, 3) or 0.0,
        }
        est["error"] = self._error(est, payload.get("measurements") or {})
        return est
