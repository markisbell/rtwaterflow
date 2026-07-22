"""M8 stage 2 — NetzStudio water editor backend (roadmap §5, TF §2).

The W 400-1 three-load-case check (the "commission a *passing* network" gate)
and the editor endpoints. The live OSM/DEM proxies (Overpass/Nominatim/
OpenTopoData) are NOT hit in CI — only their offline guards (bbox bounds,
bundle validation) are exercised; the load-case engine is tested offline
against the committed bundles.
"""
from __future__ import annotations

import json

from conftest import REPO_ROOT, make_api_client, make_settings

from rtwaterflow.data_loader import load_network
from rtwaterflow.loadcases import _fire_flow_m3_h, run_load_cases

NETS = REPO_ROOT / "data" / "networks"


def _bundle(net: str) -> dict:
    d = NETS / net
    return {f: json.loads((d / f"{f}.json").read_text("utf-8"))
            for f in ("network_structure", "pipes", "consumers",
                      "supply", "environment")}


# --- the load-case engine ----------------------------------------------------

def test_load_cases_shape_and_pass_on_a_healthy_net():
    """tutorial_hillside is a small, well-pressured net → all three W 400-1
    load cases hold; the result shape is the three named cases."""
    r = run_load_cases(load_network(NETS / "tutorial_hillside"),
                       make_settings(steps_per_day=96))
    assert [c["id"] for c in r["cases"]] == ["lf1", "lf2", "lf3"]
    assert r["passed"] is True
    assert all(c["passed"] for c in r["cases"])
    lf3 = r["cases"][2]
    assert lf3["p_fire_bar"] >= 1.5 and lf3["fire_flow_m3_h"] in (48, 96, 192)


def test_fire_case_binds_on_a_stressed_worst_point():
    """Musterdorf has an industry zone (→ 192 m³/h fire) and its worst point
    cannot hold 1.5 bar under that draw — the honest design insight that fire
    flow, not consumption, sizes small networks (TF §3)."""
    r = run_load_cases(load_network(NETS / "musterdorf"),
                       make_settings(steps_per_day=96))
    lf1, lf2, lf3 = r["cases"]
    assert lf1["passed"] and lf2["passed"]        # pressure/velocity fine
    assert lf3["fire_flow_m3_h"] == 192           # industry land use
    assert not lf3["passed"]                      # fire case binds
    assert lf3["p_fire_bar"] < 1.5
    assert r["passed"] is False


def test_fire_flow_by_land_use():
    """W 405 Grundschutz value from land use, not headcount (TF §3)."""
    assert _fire_flow_m3_h(load_network(NETS / "musterdorf")) == 192  # industry
    assert _fire_flow_m3_h(load_network(NETS / "alpen")) == 96        # residential
    assert _fire_flow_m3_h(load_network(NETS / "tutorial_hillside")) == 48  # tiny


# --- the /editor/loadcheck endpoint ------------------------------------------

def test_loadcheck_endpoint_runs_the_three_cases():
    with make_api_client() as client:
        r = client.post("/editor/loadcheck", json=_bundle("tutorial_hillside"))
        assert r.status_code == 200
        body = r.json()
        assert body["passed"] is True
        assert len(body["cases"]) == 3
        assert body["cases"][0]["name"] == "Maximale Förderung"


def test_loadcheck_rejects_an_invalid_bundle():
    """A structurally broken bundle (a pipe to a nonexistent node) must 422 —
    the editor has to fix the structure before the load cases mean anything."""
    bundle = _bundle("tutorial_hillside")
    bundle["pipes"]["pipes"].append(
        {"from_node": "j1", "to_node": "ghost", "length_km": 0.1,
         "inner_diameter_mm": 100.0, "k_mm": 0.1})
    with make_api_client() as client:
        r = client.post("/editor/loadcheck", json=bundle)
        assert r.status_code == 422
        assert "problems" in r.json()["detail"]


def test_loadcheck_reports_a_failing_fire_case():
    with make_api_client() as client:
        body = client.post("/editor/loadcheck", json=_bundle("musterdorf")).json()
        assert body["passed"] is False
        assert body["cases"][2]["id"] == "lf3"
        assert not body["cases"][2]["passed"]


# --- editor proxy guards (no network) ----------------------------------------

def test_streets_rejects_an_oversized_bbox():
    with make_api_client() as client:
        # ~ a whole city: over the village-sized limit → 422, no Overpass call
        r = client.get("/editor/streets",
                       params={"s": 51.0, "w": 6.0, "n": 51.2, "e": 6.2})
        assert r.status_code == 422
        r2 = client.get("/editor/streets",
                        params={"s": 51.2, "w": 6.0, "n": 51.0, "e": 6.2})
        assert r2.status_code == 422        # inverted bbox


def test_elevation_empty_points_is_a_noop():
    with make_api_client() as client:
        r = client.post("/editor/elevation", json={"points": []})
        assert r.status_code == 200
        assert r.json() == {"elevations": [], "attribution": None}


def test_elevation_rejects_over_100_points():
    """Explicit cap, never a silent truncation that misaligns results (review)."""
    with make_api_client() as client:
        r = client.post("/editor/elevation",
                        json={"points": [[52.0, 9.0]] * 101})
        assert r.status_code == 422


# --- review regression pins --------------------------------------------------

def test_lf2_enforces_the_per_storey_requirement():
    """Regression (review): the W 400-1 storey requirement (2.0 + 0.35·
    (storeys−1)) is keyed by consumer NAME — keying by node silently applied
    a flat 2.0 bar to every multi-storey tap, defeating the LF2 gate."""
    from rtwaterflow.loadcases import (_consumer_pressures, _new_sim,
                                       _peak_tick, _steady_frame)
    sim = _new_sim(load_network(NETS / "lauenau"), make_settings())
    cons = _consumer_pressures(sim, _steady_frame(sim, _peak_tick(sim)))
    reqs = {req for _, _, req in cons}
    assert reqs != {2.0}                          # the per-storey table applies
    assert any(req > 2.0 for _, _, req in cons)   # multi-storey is enforced


def test_lf3_fails_on_a_physically_invalid_fire_frame():
    """Regression (review): a fire draw that leaves the solve degraded /
    craters a node below zero must FAIL — the verdict is not read off an
    unphysical frame, and not only the hydrant node is inspected."""
    lf3 = run_load_cases(load_network(NETS / "musterdorf"),
                         make_settings())["cases"][2]
    assert not lf3["passed"]
    # musterdorf's 192 m³/h fire is unservable → degraded / sub-1.5-bar frame
    assert lf3["solver_status"] != "ok" or lf3["p_fire_bar"] < 1.5


def test_loadcheck_422_on_schema_invalid_bundle():
    """Regression (review): a per-document schema error (a field out of its
    bounds) → 422 with a problems list, never a bare 500."""
    bundle = _bundle("tutorial_hillside")
    bundle["consumers"]["consumers"][0]["mdot_kg_per_s"] = -5.0   # gt=0
    with make_api_client() as client:
        r = client.post("/editor/loadcheck", json=bundle)
        assert r.status_code == 422
        assert "problems" in r.json()["detail"]
