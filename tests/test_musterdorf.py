"""M1 acceptance: the Musterdorf demo bundle (two pressure zones via PRV,
ring core + branched fringe, mixed consumers, catalog-sized pipes).

Roadmap §6 M1 bars: solves warm < 50 ms, mass balance < 0.1 %, zone
pressures inside the 4-6 bar band mid-zone, builder round-trip byte-stable.
"""
from __future__ import annotations

import importlib.util
import json
import statistics
import time
from pathlib import Path

import pytest

from conftest import REPO_ROOT, make_settings

from rtwaterflow.data_loader import load_network
from rtwaterflow.simulator import Simulator

MUSTERDORF_DIR = REPO_ROOT / "data" / "networks" / "musterdorf"
FILES = ("network_structure", "pipes", "consumers", "supply", "environment")


@pytest.fixture(scope="module")
def inputs():
    return load_network(MUSTERDORF_DIR)


@pytest.fixture(scope="module")
def sim_and_frame(inputs):
    sim = Simulator(inputs, make_settings(autostart=False))
    frame = sim.run_step(0, 0)
    return sim, frame


def test_contract_and_catalog_resolution(inputs):
    assert inputs.name == "Musterdorf"
    assert len(inputs.structure.junctions) == 35   # M2: + ww, ws
    assert len(inputs.pipes.pipes) == 33           # M2: + Steigleitung ws-hb
    assert len(inputs.consumers.consumers) == 26
    assert len(inputs.supply.prvs) == 1
    # M2 water assets: Hochbehälter (Durchlauf) + Pumpwerk with hysteresis
    assert len(inputs.supply.tanks) == 1
    assert inputs.supply.tanks[0].kind == "durchlauf"
    assert len(inputs.supply.stations) == 1
    assert inputs.supply.stations[0].control.mode == "hysteresis"
    assert inputs.environment.demand_factor is not None
    # catalog resolution: every pipe ends up with concrete hydraulic values
    for p in inputs.pipes.pipes:
        assert p.inner_diameter_mm and p.inner_diameter_mm > 0
        assert p.k_mm is not None and p.length_km and p.length_km > 0
    # material defaults per GW 303-1: PE 0.1, GGG 0.4, legacy GG 1.0
    by_mat = {p.material: p.k_mm for p in inputs.pipes.pipes if p.material}
    assert by_mat["PE"] == 0.1
    assert by_mat["GGG"] == 0.4
    assert by_mat["GG"] == 1.0
    # PE d-series: d110 SDR 17 bore is 96.8 mm, not 110
    d110 = next(p for p in inputs.pipes.pipes
                if p.material == "PE" and p.dn == 110)
    assert d110.inner_diameter_mm == pytest.approx(96.8)
    # geometry-derived lengths: village + Steigleitung span a plausible 3-6 km
    assert 3.0 < sum(p.length_km for p in inputs.pipes.pipes) < 6.0


def test_solves_tier1_with_closed_balance(inputs, sim_and_frame):
    _, frame = sim_and_frame
    assert frame.converged and frame.solver_status == "ok"
    s = frame.summary
    # M2: demand is base x the diurnal factor of tick 0 (from the contract,
    # never hardcoded — the generator owns the profile)
    f0 = float(inputs.environment.demand_factor[0])
    assert s["mdot_demand_kg_per_s"] == pytest.approx(3.09 * f0, abs=1e-6)
    assert s["mdot_delivered_kg_per_s"] == pytest.approx(3.09 * f0, abs=1e-6)
    # M2 balance: feed = delivered + stored (the Hochbehälter charges while
    # the Pumpwerk runs; closure < 0.1 % of feed — M1 acceptance kept)
    assert abs(s["balance_err_kg_per_s"]) / s["mdot_feed_kg_per_s"] < 1e-3


def test_zone_pressures_in_dvgw_bands(inputs, sim_and_frame):
    """High zone (gravity from the tank) and low zone (behind the PRV) both
    sit in sane DVGW bands: every consumer >= 2 bar, nothing above 8 bar,
    the mid-zone bulk inside 4-6 bar."""
    _, frame = sim_and_frame
    ps = {j["name"]: j["p_bar"] for j in frame.junctions}
    consumer_nodes = {c.node for c in inputs.consumers.consumers}

    for node in consumer_nodes:
        assert ps[node] >= 2.0, f"{node} below minimum service pressure"
        assert ps[node] <= 8.0, f"{node} above max rest pressure"

    # mid-zone bulk (roadmap: 4-6 bar in the middle of each zone)
    high = [ps[n] for n in consumer_nodes if n.startswith("h")]
    low = [ps[n] for n in consumer_nodes if n[0] in "rb"]
    assert 4.0 <= statistics.median(high) <= 6.0
    assert 4.0 <= statistics.median(low) <= 6.0


def test_prv_holds_the_low_zone(inputs, sim_and_frame):
    """The Druckminderer is the zone boundary: 2.8 bar held at its outlet
    while the inlet arrives ~5 bar higher (the 75 m drop from the tank).
    The wire is honest telemetry: SOLVED p_out (not the setpoint echo),
    SIGNED through-flow, and the `reducing` abnormality flag."""
    _, frame = sim_and_frame
    prv = next(p for p in frame.producers if p["kind"] == "prv")
    assert prv["name"] == "Druckminderer Talstraße"
    assert prv["p_set_bar"] == pytest.approx(2.8)
    assert prv["p_out_bar"] == pytest.approx(2.8, abs=1e-3)  # solved value
    assert prv["p_in_bar"] > prv["p_out_bar"] + 3.0
    # the whole low zone flows through it, forward (signed on the wire);
    # 2.44 kg/s is the low-zone base demand, scaled by the tick-0 factor
    f0 = float(inputs.environment.demand_factor[0])
    assert prv["mdot_kg_per_s"] == pytest.approx(2.44 * f0, abs=0.01)
    assert prv["mdot_kg_per_s"] > 0
    assert prv["reducing"] is True
    ps = {j["name"]: j["p_bar"] for j in frame.junctions}
    assert ps["dm_o"] == pytest.approx(2.8, abs=1e-3)


def test_design_velocities(sim_and_frame):
    """Musterdorf is sized fire-capable (DN >= 80/90): normal-load
    velocities stay well under the 2.0 m/s W 400-1 limit."""
    _, frame = sim_and_frame
    vmax = max(abs(p["v_m_per_s"]) for p in frame.pipes)
    assert vmax < 1.0


def test_ring_supplies_two_sided(inputs, sim_and_frame):
    """The Ringnetz teaching point: both ring halves carry flow (the core
    is supplied from two sides), so a single break would not island it.

    Pipe ids are resolved from the CONTRACT (wire id == creation order ==
    pipes.json row — the platform invariant), never hardcoded: a generator
    edit reordering the list must not silently retarget this test."""
    _, frame = sim_and_frame
    flows = {p["id"]: p["mdot_kg_per_s"] for p in frame.pipes}
    pos = {(p.from_node, p.to_node): i
           for i, p in enumerate(inputs.pipes.pipes)}
    # ring entry splits at r1: both senses around the ring carry flow
    assert abs(flows[pos[("r1", "r2")]]) > 0.3
    assert abs(flows[pos[("r10", "r1")]]) > 0.3


def test_warm_solve_under_50ms(sim_and_frame):
    """M1 acceptance: < 50 ms warm (median over 10 solves; single solves
    jitter with the OS scheduler)."""
    sim, _ = sim_and_frame
    times = []
    for i in range(10):
        t0 = time.perf_counter()
        r = sim.run_step(i % 1440, 0)
        times.append((time.perf_counter() - t0) * 1000)
        assert r.converged
    assert statistics.median(times) < 50.0, f"warm medians: {times}"


def test_generator_round_trip_byte_stable(tmp_path, monkeypatch):
    """The committed bundle IS the generator output (deterministic, no RNG):
    regenerating into a scratch dir reproduces every file byte-for-byte."""
    spec = importlib.util.spec_from_file_location(
        "generate_musterdorf",
        REPO_ROOT / "scripts" / "generate_musterdorf.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    monkeypatch.setattr(gen, "OUT", Path(tmp_path))
    gen.main()
    for fname in FILES:
        fresh = (tmp_path / f"{fname}.json").read_bytes()
        committed = (MUSTERDORF_DIR / f"{fname}.json").read_bytes()
        assert fresh.replace(b"\r\n", b"\n") == committed.replace(
            b"\r\n", b"\n"), f"{fname}.json drifted from the generator"


def test_consumer_metadata_for_later_milestones(inputs):
    """kind/storeys ship now so M3 (demand archetypes) and M4 (per-storey
    minimum pressure) need no bundle rewrite."""
    kinds = {c.kind for c in inputs.consumers.consumers}
    assert {"residential", "industry", "farm", "school", "pool"} <= kinds
    mfh = next(c for c in inputs.consumers.consumers
               if c.name == "Marktplatz (MFH)")
    assert mfh.storeys == 4


def test_topology_carries_prvs_and_catalog_sizing(sim_and_frame):
    """GET /network payload: the prvs list (UI markers + Drucklinie path
    edges) and the catalog sizing per trench are pinned — the UI guards
    with `?? []`, so a dropped key would die silently otherwise."""
    from rtwaterflow.api.runtime import build_topology

    sim, _ = sim_and_frame
    topo = build_topology("musterdorf", sim)
    # pid order M2: slack (ww) 0, tank (hb) 1, prv 2, station 3
    assert topo["prvs"] == [{
        "id": 2, "name": "Druckminderer Talstraße",
        "from_node": "dm_i", "to_node": "dm_o"}]
    assert topo["stations"] == [{
        "id": 3, "name": "Pumpwerk Mustertal",
        "from_node": "ww", "to_node": "ws"}]
    d110 = next(tr for tr in topo["trenches"]
                if tr["material"] == "PE" and tr["dn"] == 110)
    assert d110["inner_diameter_mm"] == pytest.approx(96.8)
    ggg = next(tr for tr in topo["trenches"] if tr["material"] == "GGG")
    assert ggg["k_mm"] == 0.4


def test_recorder_round_trips_prv_telemetry(tmp_path, sim_and_frame):
    """producers.csv must mirror the wire 1:1 — a Musterdorf recording has
    to show that the PRV held its setpoint (silent column loss found by the
    M1 review)."""
    import csv

    from rtwaterflow.recorder import Recorder
    from rtwaterflow.state import StateStore

    sim, _ = sim_and_frame
    store = StateStore(make_settings(recordings_dir=tmp_path))
    rec = Recorder(tmp_path)
    rid = rec.start({"network": {"name": "musterdorf"}})["id"]
    rec.record(store.frame(sim.run_step(0, 0)))
    rec.stop()
    rows = list(csv.DictReader(
        (tmp_path / rid / "producers.csv").open(encoding="utf-8")))
    prv = next(r for r in rows if r["kind"] == "prv")
    assert float(prv["p_set_bar"]) == pytest.approx(2.8)
    assert float(prv["p_out_bar"]) == pytest.approx(2.8, abs=1e-3)
    assert float(prv["p_in_bar"]) > 5.0
    assert float(prv["mdot_kg_per_s"]) > 0
    assert prv["reducing"] == "1"


def test_strict_mode_keeps_station_scada(sim_and_frame):
    """Doctrine pin: producer entries (source + PRV stations) are station
    SCADA — always measured in reality, so they stay on the wire in strict
    mode (like the plant block; documented in simulator._collect)."""
    from rtwaterflow.state import StateStore

    sim, _ = sim_and_frame
    store = StateStore(make_settings(expose_ground_truth=False))
    frame = store.frame(sim.run_step(1, 0))
    assert "junctions" not in frame          # truth stripped
    kinds = {p["kind"] for p in frame["producers"]}
    # M2: tanks + pump stations are station SCADA too (levels/switchgear
    # are always telemetered in a real waterworks)
    assert kinds == {"slack", "tank", "prv", "station"}
