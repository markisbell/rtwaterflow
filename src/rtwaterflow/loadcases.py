"""DVGW W 400-1 network-sizing load cases (M8 stage 2 — the editor's
"commission a *passing* network" check, roadmap §5, TF §2).

A network is dimensioned to hold THREE design load cases (TF §2 — "network
passes if all hold"):

1. **Maximale Förderung** — maximum delivery of the pump station(s): the peak
   throughput a network must carry → the velocity check (≤ 2.0 m/s) binds.
2. **Spitzenstunde Maximaltag** — peak-hour demand on the maximum day: every
   tap at its design peak → the minimum-pressure check (2.0 + 0.35 bar/storey,
   W 400-1) and the rest-pressure ceiling bind.
3. **Löschfall** — fire-fighting withdrawal superimposed on the (mean-day)
   peak hour: a W 405 fire draw (48/96/192 m³/h by density) at the
   hydraulically worst node must keep ≥ 1.5 bar flow pressure, velocity ≤ 2.5.

Each case is evaluated from a warm, steady solved frame's RAW values (not the
time-dependent compliance findings) so the verdict is an instantaneous design
check, deterministic and history-free. The editor runs this before
"commission network" and shows pass/fail per case + the binding quantity.
"""
from __future__ import annotations

import numpy as np

from .compliance.engine import P_MIN_EG_BAR
from .config import Settings, get_settings
from .net_inputs import NetInputs
from .simulator import NEG_PRESSURE_FLOOR_BAR, Simulator

#: normal-operation velocity ceiling (W 400-1) and the fire-case relaxation
V_MAX_M_S = 2.0
V_MAX_FIRE_M_S = 2.5
#: rest-pressure ceiling at consumer nodes (PN-10 practice, W 400-1)
P_REST_MAX_BAR = 8.0
#: fire-flow minimum flow pressure in built-up areas (W 405)
P_FIRE_MIN_BAR = 1.5
#: steady-state warm-up steps at a fixed tick (a hydraulic solve is
#: memoryless + warm-started, so it settles in a couple of steps)
_WARM_STEPS = 3
#: the check runs at a FIXED coarse resolution — the verdict must not depend
#: on the app's runtime steps_per_day, and 96 keeps it fast (review)
_LOADCHECK_SPD = 96


def _peak_tick(sim: Simulator) -> int:
    """The global horizon tick of maximum total consumer demand."""
    prof = sim.profiles.mdot_kg_per_s
    if not prof.size:
        return 0
    return int(np.argmax(prof.sum(axis=0)))


def _fire_flow_m3_h(inputs: NetInputs) -> float:
    """W 405 Grundschutz guide value by land use (TF §3): 48/96/192 m³/h.
    Land use, not headcount, drives it — an industry/high-risk zone takes
    192; typical residential/mixed 96; a low-density hamlet (< 300
    residents) the reduced 48."""
    kinds = {c.kind for c in inputs.consumers.consumers}
    pop = sum(int(c.size.population) for c in inputs.consumers.consumers
              if c.size and c.size.population)
    if kinds & {"industry"}:
        return 192.0
    if pop >= 300:
        return 96.0
    return 48.0


def _steady_frame(sim: Simulator, tick: int):
    """Warm the solve to steady state at the global *tick*, return the last
    frame. The global tick is decomposed into (step, day) so a multi-day
    horizon's peak on a later day is not clamped into day 0 (review)."""
    spd = sim.profiles.steps_per_day
    day, step = divmod(int(tick), spd)
    frame = None
    for _ in range(_WARM_STEPS):
        frame = sim.run_step(step, day)
    return frame


def _max_velocity(frame) -> float:
    vs = [abs(p.get("v_m_per_s") or 0.0) for p in frame.pipes]
    return max(vs) if vs else 0.0


def _network_p_min(frame) -> float | None:
    """Minimum gauge pressure over ALL junctions — the physical-validity
    signal (a design that craters ANY node below ~0 bar is inadequate, even
    if the drawing hydrant itself holds; review)."""
    ps = [j["p_bar"] for j in frame.junctions if j.get("p_bar") is not None]
    return min(ps) if ps else None


def _physically_valid(frame) -> bool:
    """The frame converged, is not a failed solve, and holds non-negative
    pressure everywhere (a 'degraded' friction-fallback frame is fine; a
    negative-pressure / not-settled 'degraded' frame is NOT — review)."""
    if not frame.converged or frame.solver_status == "failed":
        return False
    p_min = _network_p_min(frame)
    return p_min is None or p_min >= NEG_PRESSURE_FLOOR_BAR


def _consumer_pressures(sim: Simulator, frame) -> list[tuple[str, float, float]]:
    """(node, p_bar, p_req_bar) for every consumer in *frame*. The W 400-1
    storey requirement table is keyed by consumer NAME (review — keying by
    node silently fell back to a flat 2.0 bar for every multi-storey tap)."""
    out = []
    for c in frame.consumers:
        p = c.get("p_bar")
        if p is None:
            continue
        p_req = sim.compliance.p_req.get(c["name"], P_MIN_EG_BAR)
        out.append((c["node"], float(p), float(p_req)))
    return out


def _new_sim(inputs: NetInputs, settings: Settings) -> Simulator:
    sim = Simulator(inputs, settings.model_copy(
        update={"steps_per_day": _LOADCHECK_SPD}))
    sim.set_est_config(sim.est_config.__class__(enabled=False))  # no observer
    return sim


def run_load_cases(inputs: NetInputs,
                   settings: Settings | None = None) -> dict:
    """Run the three W 400-1 load cases; return per-case pass/fail + the
    binding quantities. The network `passes` iff all three hold."""
    settings = settings or get_settings()
    cases: list[dict] = []

    # -- LF1: Maximale Förderung — peak throughput, stations forced on -------
    sim = _new_sim(inputs, settings)
    peak = _peak_tick(sim)
    for name in sim.station_modes:
        sim.station_modes[name] = "on"
    frame = _steady_frame(sim, peak)
    v = _max_velocity(frame)
    p_min = frame.summary.get("p_min_bar")
    ok1 = _physically_valid(frame) and v <= V_MAX_M_S
    cases.append({
        "id": "lf1", "name": "Maximale Förderung",
        "passed": bool(ok1), "solver_status": frame.solver_status,
        "v_max_m_per_s": round(v, 3), "v_limit_m_per_s": V_MAX_M_S,
        "p_min_bar": None if p_min is None else round(p_min, 3),
        "detail": ("Spitzendurchfluss mit laufenden Pumpwerken — "
                   "Geschwindigkeit ≤ 2,0 m/s (bei bedarfsgeführten/"
                   "Gefälle-Netzen der Spitzendurchfluss aus Lastfall 2)"),
    })

    # -- LF2: Spitzenstunde Maximaltag — min pressure + velocity + rest ------
    sim = _new_sim(inputs, settings)
    frame = _steady_frame(sim, _peak_tick(sim))
    v = _max_velocity(frame)
    cons = _consumer_pressures(sim, frame)
    under = [(n, p, req) for n, p, req in cons if p < req]
    over_rest = [(n, p) for n, p, _ in cons if p > P_REST_MAX_BAR]
    p_worst = min((p for _, p, _ in cons), default=None)
    ok2 = (_physically_valid(frame) and not under and not over_rest
           and v <= V_MAX_M_S)
    cases.append({
        "id": "lf2", "name": "Spitzenstunde am Maximaltag",
        "passed": bool(ok2), "solver_status": frame.solver_status,
        "p_min_bar": None if p_worst is None else round(p_worst, 3),
        "v_max_m_per_s": round(v, 3),
        "n_underpressure": len(under), "n_over_rest": len(over_rest),
        "underpressure_nodes": [n for n, _, _ in under][:8],
        "detail": ("Spitzenstunde: jeder Hausanschluss ≥ Mindestdruck "
                   "(2,0 + 0,35 bar/Geschoss) und ≤ 8 bar Ruhedruck"),
    })

    # -- LF3: Löschfall — fire draw at the worst node, ≥ 1.5 bar -------------
    sim = _new_sim(inputs, settings)
    fire = _fire_flow_m3_h(inputs)
    frame = _steady_frame(sim, _peak_tick(sim))
    cons = _consumer_pressures(sim, frame)
    # the hydraulically worst consumer node bears the fire draw (worst case)
    hydrant_node = (min(cons, key=lambda t: t[1])[0] if cons
                    else inputs.consumers.consumers[0].node)
    sim.open_hydrant(node=hydrant_node, target_m3_h=fire, name="Löschfall")
    frame = _steady_frame(sim, _peak_tick(sim))
    v = _max_velocity(frame)
    p_fire = next((j["p_bar"] for j in frame.junctions
                   if j["name"] == hydrant_node), None)
    # a fire draw that craters ANY node (not just the hydrant) below zero, or
    # leaves the PDA/emitter loop unsettled, means the network cannot serve it
    ok3 = (_physically_valid(frame) and p_fire is not None
           and p_fire >= P_FIRE_MIN_BAR and v <= V_MAX_FIRE_M_S)
    cases.append({
        "id": "lf3", "name": "Löschfall",
        "passed": bool(ok3), "solver_status": frame.solver_status,
        "fire_flow_m3_h": fire, "hydrant_node": hydrant_node,
        "p_fire_bar": None if p_fire is None else round(p_fire, 3),
        "p_fire_min_bar": P_FIRE_MIN_BAR,
        "v_max_m_per_s": round(v, 3), "v_limit_m_per_s": V_MAX_FIRE_M_S,
        "detail": (f"Löschentnahme {fire:.0f} m³/h am Schlechtpunkt "
                   f"{hydrant_node} — Fließdruck ≥ 1,5 bar"),
    })

    return {"passed": all(c["passed"] for c in cases), "cases": cases}
