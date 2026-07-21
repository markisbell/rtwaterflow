"""EPANET oracle (WNTR) for the M2 tank dynamics — Mustertal, 24 h.

The same network — reservoir (Wasserwerk head), pump with the SAME fitted
Q-H characteristic (sampled from the pandapipes regression polynomial, so
both engines march the same curve), D-W pipes, cylindrical tank, level
hysteresis controls, 15-min diurnal demand pattern — is built in WNTR 1.5
and run through EPANET's extended-period simulation. EPANET is the
industry-standard reference for exactly this: explicit-Euler tank
integration + rule-based pump switching + implicit pump check valves.

Compared: the tank level trajectory and the pump switching count. Both
engines use forward-Euler tank integration over 900 s steps, so remaining
differences are friction-model details and rule-evaluation timing (EPANET
may switch on intermediate hydraulic events) — a fraction of one 15-min
integration step of level motion.
"""
from __future__ import annotations

import numpy as np
import pytest

wntr = pytest.importorskip("wntr")

from conftest import REPO_ROOT, make_settings  # noqa: E402

from rtwaterflow.data_loader import load_network  # noqa: E402
from rtwaterflow.network_builder import BAR_PER_M, RHO_KG_M3  # noqa: E402
from rtwaterflow.simulator import Simulator  # noqa: E402

MUSTERTAL_DIR = REPO_ROOT / "data" / "networks" / "mustertal"
SPD = 96
DT_S = 900


def _build_wntr_twin(inputs, reg_par) -> "wntr.network.WaterNetworkModel":
    wn = wntr.network.WaterNetworkModel()
    wn.options.hydraulic.headloss = "D-W"
    wn.options.time.duration = 86400
    wn.options.time.hydraulic_timestep = DT_S
    wn.options.time.pattern_timestep = DT_S
    wn.options.time.report_timestep = DT_S

    wn.add_pattern("diurnal", [float(f) for f in
                               inputs.environment.demand_factor])

    elev = {j.name: float(j.elevation_m) for j in inputs.structure.junctions}
    demand = {c.node: float(c.mdot_kg_per_s) / RHO_KG_M3
              for c in inputs.consumers.consumers}

    supply = inputs.supply.supplies[0]
    tank = inputs.supply.tanks[0]
    station = inputs.supply.stations[0]

    for j in inputs.structure.junctions:
        if j.name == supply.node:
            wn.add_reservoir(
                j.name, base_head=elev[j.name] + supply.p_bar / BAR_PER_M)
        elif j.name == tank.node:
            wn.add_tank(
                j.name, elevation=elev[j.name],
                init_level=float(tank.level_initial_m),
                min_level=float(tank.level_min_m),
                max_level=float(tank.level_max_m),
                diameter=float(np.sqrt(4.0 * tank.area_m2 / np.pi)))
        else:
            wn.add_junction(
                j.name, base_demand=demand.get(j.name, 0.0),
                demand_pattern="diurnal", elevation=elev[j.name])

    for i, p in enumerate(inputs.pipes.pipes):
        # WNTR-internal D-W roughness is in METRES (written as mm to the
        # metric INP file by HydParam.RoughnessCoeff)
        wn.add_pipe(f"p{i}", p.from_node, p.to_node,
                    length=p.length_km * 1000.0,
                    diameter=p.inner_diameter_mm / 1000.0,
                    roughness=p.k_mm / 1000.0, minor_loss=0.0)

    # the pump: EPANET multipoint curve sampled from the SAME regression
    # polynomial pandapipes' std_type carries — both engines see one curve
    q_m3h = np.arange(0.0, 32.0, 2.0)
    head_m = np.polyval(reg_par, q_m3h) / BAR_PER_M
    wn.add_curve("qh", "HEAD",
                 [(q / 3600.0, float(h)) for q, h in zip(q_m3h, head_m)])
    wn.add_pump("pw", station.from_node, station.to_node,
                pump_type="HEAD", pump_parameter="qh")

    # hysteresis as EPANET conditional controls
    from wntr.network.controls import Control, ControlAction, ValueCondition
    pump = wn.get_link("pw")
    tk = wn.get_node(tank.node)
    wn.add_control("pump_on", Control(
        ValueCondition(tk, "level", "<", float(station.control.on_below_m)),
        ControlAction(pump, "status", 1)))
    wn.add_control("pump_off", Control(
        ValueCondition(tk, "level", ">", float(station.control.off_above_m)),
        ControlAction(pump, "status", 0)))
    return wn


def test_tank_trajectory_matches_epanet(tmp_path):
    inputs = load_network(MUSTERTAL_DIR)
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=SPD))
    station = inputs.supply.stations[0]
    sname = station.name
    reg_par = sim.net["std_types"]["pump"][sname].reg_par

    ours_levels, ours_running = [], []
    for t in range(SPD):
        f = sim.run_step(t, 0)
        assert f.converged
        ours_levels.append(f.tanks[0]["level_m"])
        st = next(p for p in f.producers if p["kind"] == "station")
        ours_running.append(bool(st["running"]))

    wn = _build_wntr_twin(inputs, reg_par)
    res = wntr.sim.EpanetSimulator(wn).run_sim(
        file_prefix=str(tmp_path / "oracle"))
    # tank "pressure" is the level above the tank bottom [m]; row at time
    # (t+1)*900 corresponds to OUR end-of-tick-t level (same forward Euler)
    ep = res.node["pressure"][inputs.supply.tanks[0].node]
    ep_levels = [float(ep.loc[(t + 1) * DT_S]) for t in range(SPD)]
    ep_status = res.link["status"]["pw"]
    ep_running = [float(ep_status.loc[t * DT_S]) > 0.5 for t in range(SPD)]

    ours = np.asarray(ours_levels)
    ep = np.asarray(ep_levels)

    # 1) THE physics bar: the level RATE (pump inflow minus village demand,
    # integrated over 900 s) matches EPANET wherever both engines agree on
    # the pump state. EPANET cuts hydraulic timesteps to switch exactly at
    # threshold crossings while our engine switches on tick boundaries, so
    # the sawtooths phase-shift over the day — the rates, not the absolute
    # phase, carry the hydraulic truth. Runtime-observed agreement is
    # ~1 mm/tick; pinned at 1 cm median / 3 cm p75 (rate ~0.26 m/tick).
    d_ours, d_ep = np.diff(ours), np.diff(ep)
    same_state = np.asarray(
        [ours_running[t] == ep_running[t]
         and ours_running[t + 1] == ep_running[t + 1]
         for t in range(SPD - 1)])
    rate_err = np.abs(d_ours - d_ep)[same_state]
    assert rate_err.size > 40, "trajectories barely overlap in pump state"
    assert float(np.median(rate_err)) < 0.01, f"median {np.median(rate_err):.4f} m/tick"
    assert float(np.percentile(rate_err, 75)) < 0.03

    # 2) both sawtooths live in the same hysteresis envelope (EPANET lands
    # exactly on thresholds; we overshoot at most one tick of level motion)
    ctl = station.control
    for name, arr in (("ours", ours), ("epanet", ep)):
        assert arr.min() > ctl.on_below_m - 0.30, name
        assert arr.max() < ctl.off_above_m + 0.30, name

    # 3) same cycling behavior + a coarse absolute-envelope gross-error trap
    ours_sw = sum(1 for a, b in zip(ours_running, ours_running[1:]) if a != b)
    ep_sw = sum(1 for a, b in zip(ep_running, ep_running[1:]) if a != b)
    assert abs(ours_sw - ep_sw) <= 1, (ours_sw, ep_sw)
    assert float(np.abs(ours - ep).max()) < 1.0
