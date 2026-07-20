"""M0 acceptance: the tutorial_hillside bundle (pandapipes height_difference
tutorial as a five-file water bundle) + default-network wiring.

The elevation regression is the load-bearing physics pin: ext_grid 0.5 bar at
400 m must yield ~5.78 bar at the 346 m consumer (54 m of static head at
~0.098 bar/m minus a tiny friction residual). Reference values were baselined
LOCALLY on the pinned pandapipes 0.14.0 with friction_model="colebrook"
(project convention: pins are re-derived at runtime, never copied from docs);
the upstream tutorial's stored nikuradse outputs agree within ±0.02 bar.
"""
from __future__ import annotations

import pytest

from conftest import HILLSIDE_DIR, make_api_client, make_settings, wait_for

from rtwaterflow.config import Settings
from rtwaterflow.simulator import Simulator

#: Local colebrook baseline (2026-07-20, pandapipes 0.14.0): junction -> p_bar
EXPECTED_P_BAR = {
    "j1": 5.194293,
    "j2": 4.607446,
    "j3": 4.314019,
    "j4": 5.781010,
    "j5": 0.5,
}
#: Upstream tutorial (nikuradse, master notebook outputs) — sanity band only
TUTORIAL_P_BAR = {
    "j1": 5.194289,
    "j2": 4.607435,
    "j3": 4.314005,
    "j4": 5.781005,
    "j5": 0.500000,
}


@pytest.fixture(scope="module")
def sim(hillside_inputs) -> Simulator:
    return Simulator(hillside_inputs,
                     make_settings(autostart=False))


@pytest.fixture(scope="module")
def frame(sim):
    return sim.run_step(0, 0)


def test_contract_loads_clean(hillside_inputs):
    assert hillside_inputs.name == "Tutorial Hanglage"
    assert len(hillside_inputs.structure.junctions) == 5
    assert len(hillside_inputs.pipes.pipes) == 4
    assert len(hillside_inputs.consumers.consumers) == 2
    assert hillside_inputs.n_days == 1


def test_geometry_on_land(hillside_inputs):
    """WGS84 bbox: the synthetic hillside sits in the Odenwald (Germany)."""
    for j in hillside_inputs.structure.junctions:
        lat, lon = j.geo
        assert 49.4 < lat < 49.5 and 8.9 < lon < 9.1


def test_elevation_regression(frame):
    """THE M0 acceptance pin: 0.5 bar @ 400 m -> ~5.78 bar @ 346 m (±0.01)."""
    assert frame.converged and frame.solver_status == "ok"
    p = {j["name"]: j["p_bar"] for j in frame.junctions}
    for name, expected in EXPECTED_P_BAR.items():
        assert p[name] == pytest.approx(expected, abs=0.01), name
    # sanity band vs the upstream tutorial's stored outputs (nikuradse)
    for name, expected in TUTORIAL_P_BAR.items():
        assert p[name] == pytest.approx(expected, abs=0.02), name
    # the headline number: the 346 m node reads ~5.78 bar
    assert p["j4"] == pytest.approx(5.781, abs=0.01)


def test_hydrostatic_gradient(frame):
    """The 1-bar-per-10-m teaching point: elevation dominates, friction is
    sub-centibar at these tiny flows (~0.098 bar/m)."""
    p = {j["name"]: j["p_bar"] for j in frame.junctions}
    for name, height in (("j1", 352.0), ("j2", 358.0), ("j3", 361.0),
                         ("j4", 346.0)):
        static = 0.5 + 0.0981 * (400.0 - height)
        assert p[name] == pytest.approx(static, abs=0.05), name


def test_summary_worst_point(frame):
    """Min-pressure worst point = the HIGHEST consumer (j3 at 361 m), never
    the source junction (0.5 bar at the tank surface is not a violation)."""
    s = frame.summary
    assert s["worst_node"] == "j3"
    assert s["worst_consumer"] == "Abnehmer Hangfuss"
    assert s["p_min_bar"] == pytest.approx(4.314, abs=0.01)
    assert s["mdot_feed_kg_per_s"] == pytest.approx(0.416, abs=1e-6)
    assert s["mdot_demand_kg_per_s"] == pytest.approx(0.416, abs=1e-6)
    assert s["mdot_delivered_kg_per_s"] == pytest.approx(0.416, abs=1e-6)
    assert abs(s["balance_err_kg_per_s"]) < 1e-6


def test_consumers_demand_equals_delivery(frame):
    """Fixed sinks: demanded == delivered while converged (PDA arrives M5)."""
    for c in frame.consumers:
        assert c["mdot_kg_per_s"] == pytest.approx(
            c["mdot_demand_kg_per_s"], abs=1e-9)


def test_default_network_setting():
    assert make_settings().default_network == "tutorial_hillside"


def test_app_serves_hillside_by_default(tmp_path):
    """create_app() without network_dir resolves <data_dir>/networks/<default>."""
    from rtwaterflow.api import create_app
    from starlette.testclient import TestClient

    settings = Settings(
        _env_file=None, autostart=False, step_interval_seconds=0.02,
        data_dir=HILLSIDE_DIR.parents[1])
    with TestClient(create_app(settings)) as client:
        status = client.get("/status").json()
        assert status["network"]["id"] == "tutorial_hillside"
        topo = client.get("/network").json()
        assert {n["name"] for n in topo["nodes"]} == {"j1", "j2", "j3", "j4",
                                                      "j5"}
        assert all("elevation_m" in n for n in topo["nodes"])
        assert all("pipe" in t for t in topo["trenches"])


def test_streams_frames_over_ws():
    """M0 end-to-end: the engine ticks and frames stream over the WS."""
    with make_api_client(autostart=True) as client:
        wait_for(lambda: client.get("/state").status_code == 200)
        with client.websocket_connect("/ws") as ws:
            frame = ws.receive_json()
            assert frame["converged"] is True
            assert {j["name"] for j in frame["junctions"]} == {
                "j1", "j2", "j3", "j4", "j5"}
