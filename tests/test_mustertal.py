"""M2 acceptance: the Mustertal demo bundle — the Gegenbehälter reference.

The pump feeds at the WEST end, the Wasserturm sits on the EAST hill: with
the pump running the surplus charges the tank THROUGH the village; with the
pump off the tank feeds back and the segment next to it REVERSES flow (the
canonical Gegenbehälter teaching point, TF §4).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from conftest import REPO_ROOT, make_settings

from rtwaterflow.data_loader import load_network
from rtwaterflow.network_builder import RHO_KG_M3
from rtwaterflow.simulator import Simulator

MUSTERTAL_DIR = REPO_ROOT / "data" / "networks" / "mustertal"
FILES = ("network_structure", "pipes", "consumers", "supply", "environment")
SPD = 96  # the bundle's native resolution (15-min ticks) — one day, fast


@pytest.fixture(scope="module")
def inputs():
    return load_network(MUSTERTAL_DIR)


@pytest.fixture(scope="module")
def day_run(inputs):
    """One simulated day: (sim, frames[96])."""
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=SPD))
    frames = [sim.run_step(t, 0) for t in range(SPD)]
    return sim, frames


def test_contract(inputs):
    assert inputs.name == "Mustertal (Gegenbehälter)"
    assert len(inputs.structure.junctions) == 8
    assert len(inputs.pipes.pipes) == 6
    assert len(inputs.consumers.consumers) == 5
    assert len(inputs.supply.prvs) == 0
    assert len(inputs.supply.tanks) == 1
    assert inputs.supply.tanks[0].kind == "gegen"
    assert len(inputs.supply.stations) == 1
    st = inputs.supply.stations[0]
    assert st.control.mode == "hysteresis"
    assert st.control.tank == "Wasserturm Mustertal"


def test_day_converges_cleanly(day_run):
    _, frames = day_run
    assert all(f.converged for f in frames)
    # no degraded frames: the station operating point settles every tick
    assert {f.solver_status for f in frames} == {"ok"}


def test_gegen_reversal(inputs, day_run):
    """THE Gegenbehälter bar: the segment next to the Wasserturm carries flow
    in BOTH directions over one day — toward the tank while the pump surplus
    charges it, away from it when the tank supplies the village."""
    _, frames = day_run
    pos = {(p.from_node, p.to_node): i
           for i, p in enumerate(inputs.pipes.pipes)}
    seg = pos[("wt5", "twr")]
    flows = [next(p["mdot_kg_per_s"] for p in f.pipes if p["id"] == seg)
             for f in frames]
    assert max(flows) > 0.5, "tank never charged through the village"
    assert min(flows) < -0.5, "tank never fed the village back"


def test_hysteresis_cycles_within_band(inputs, day_run):
    _, frames = day_run
    ctl = inputs.supply.stations[0].control
    levels = [f.tanks[0]["level_m"] for f in frames]
    running = [next(p for p in f.producers if p["kind"] == "station")["running"]
               for f in frames]
    switches = sum(1 for a, b in zip(running, running[1:]) if a != b)
    assert switches >= 2, "pump never cycled"
    # band envelope (± one tick's integration step). NB the published level
    # is CLAMPED to [level_min, level_max] by construction — envelope
    # assertions against those limits would be tautological (M2 review);
    # the physics guard is the band check + the alarm flags below.
    assert min(levels) >= ctl.on_below_m - 0.2
    assert max(levels) <= ctl.off_above_m + 0.2
    assert not any(f.tanks[0]["empty"] or f.tanks[0]["overflow"]
                   for f in frames)


def test_tank_mass_balance(inputs, day_run):
    """∫ tank mdot dt == ΔV·rho: the level integrator and the solved ext_grid
    flows agree to < 0.5 % of the day's tank throughput (no clamp events in
    this run — verified by the flags above)."""
    _, frames = day_run
    tk = inputs.supply.tanks[0]
    dt_s = 86400.0 / SPD
    mdots = [f.tanks[0]["mdot_kg_per_s"] for f in frames]
    stored_kg = sum(mdots) * dt_s
    dlevel = frames[-1].tanks[0]["level_m"] - tk.level_initial_m
    expected_kg = dlevel * tk.area_m2 * RHO_KG_M3
    throughput_kg = sum(abs(m) for m in mdots) * dt_s
    assert abs(stored_kg - expected_kg) < 0.005 * throughput_kg


def test_service_pressure_all_day(inputs, day_run):
    """Every consumer ≥ 2.0 bar in every frame (W 400-1 minimum) — even at
    the lowest tank level of the day."""
    _, frames = day_run
    consumer_nodes = {c.node for c in inputs.consumers.consumers}
    for f in frames:
        ps = {j["name"]: j["p_bar"] for j in f.junctions}
        for node in consumer_nodes:
            assert ps[node] >= 2.0, f"{node} at {ps[node]} bar in {f.step}"


def test_generator_round_trip_byte_stable(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "generate_mustertal", REPO_ROOT / "scripts" / "generate_mustertal.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    monkeypatch.setattr(gen, "OUT", Path(tmp_path))
    gen.main()
    for fname in FILES:
        fresh = (tmp_path / f"{fname}.json").read_bytes()
        committed = (MUSTERTAL_DIR / f"{fname}.json").read_bytes()
        assert fresh.replace(b"\r\n", b"\n") == committed.replace(
            b"\r\n", b"\n"), f"{fname}.json drifted from the generator"
