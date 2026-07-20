"""Network catalog + runtime swap + scenario save/load round-trip."""
from __future__ import annotations

import json

import pytest

from conftest import HILLSIDE_DIR, make_api_client, wait_for

FILES = ("network_structure", "pipes", "consumers", "supply", "environment")


def _hillside_bundle(name: str) -> dict:
    bundle = {n: json.loads(
        (HILLSIDE_DIR / f"{n}.json").read_text(encoding="utf-8"))
        for n in FILES}
    return {"name": name, **bundle}


# -- catalog ---------------------------------------------------------------------

def test_networks_list_and_preview():
    with make_api_client() as client:
        r = client.get("/networks").json()
        assert r["available"] is True
        ids = {n["id"] for n in r["networks"]}
        assert {"tutorial_hillside", "musterdorf"} <= ids
        entry = next(n for n in r["networks"]
                     if n["id"] == "tutorial_hillside")
        assert entry["nodes"] == 5
        assert entry["pipe_km"] == pytest.approx(1.355, abs=0.01)

        # musterdorf library stats + catalog-path load (M1 review: the
        # hand-maintained entry and the preview() path were unpinned)
        md = next(n for n in r["networks"] if n["id"] == "musterdorf")
        assert md["nodes"] == 33
        assert md["pipe_km"] == pytest.approx(3.885, abs=0.01)
        prev = client.get("/networks/musterdorf").json()
        assert prev["n_pipes"] == 32
        assert prev["n_consumers"] == 26
        assert prev["demand_kg_per_s"] == pytest.approx(3.09, abs=0.001)
        assert prev["elevation_min_m"] == 303.0
        assert prev["elevation_max_m"] == 420.0
        assert prev["supply"]["node"] == "hb"

        p = client.get("/networks/tutorial_hillside").json()
        assert p["n_consumers"] == 2
        assert p["n_pipes"] == 4
        assert p["demand_kg_per_s"] == pytest.approx(0.416, abs=1e-4)
        assert p["elevation_min_m"] == 346.0
        assert p["elevation_max_m"] == 400.0
        assert p["supply"]["node"] == "j5"
        assert p["supply"]["p_bar"] == 0.5

        assert client.get("/networks/nope").status_code == 404


# -- runtime network swap ----------------------------------------------------------

def test_config_apply_swaps_the_network(tmp_path):
    """Swap needs a second loadable network — import one on the fly."""
    with make_api_client(autostart=True, user_networks_dir=tmp_path,
                         recordings_dir=tmp_path / "rec") as client:
        wait_for(lambda: client.get("/state").status_code == 200)
        assert client.get("/config/active").json()[
            "network_id"] == "tutorial_hillside"

        imp = client.post("/networks/import",
                          json=_hillside_bundle("Zweitnetz"))
        assert imp.status_code == 200

        r = client.post("/config/apply",
                        json={"network_id": "user_zweitnetz"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"]["network"]["id"] == "user_zweitnetz"
        assert body["active"]["source"] == "catalog"
        assert len(body["network"]["nodes"]) == 5

        # the store was reset; the engine keeps running on the new net
        assert client.get("/status").json()[
            "network"]["id"] == "user_zweitnetz"
        frame = wait_for(lambda: (
            (rr := client.get("/state")).status_code == 200
            and rr.json().get("converged") and rr.json()))
        assert len(frame["consumers"]) == 2

        assert client.post("/config/apply", json={
            "network_id": "nope"}).status_code == 404


# -- scenarios ---------------------------------------------------------------------

def test_scenario_round_trip(tmp_path):
    """configure (consumer + sensors + clock) → save → mutate heavily
    (incl. a full network swap) → load → restored."""
    with make_api_client(scenarios_dir=tmp_path,
                         user_networks_dir=tmp_path / "user",
                         recordings_dir=tmp_path / "rec") as client:
        # --- configure the live setup ---
        client.post("/consumer", json={
            "node": "j1", "mdot_kg_per_s": 0.05, "name": "Neubau"})
        client.post("/measurements/preset", json={"preset": "clear"})
        client.post("/measurements/node/j4", json={})
        client.post("/measurements/mode", json={"mode": "standard"})
        client.post("/control/seek", json={"step": 300})
        client.post("/control/interval", json={"seconds": 5.0})

        r = client.post("/scenarios", json={
            "name": "RT Test", "description": "round trip"})
        assert r.status_code == 200
        sid = r.json()["id"]
        assert sid == "rt-test"
        assert r.json()["network_id"] == "tutorial_hillside"
        listed = client.get("/scenarios").json()["scenarios"]
        assert any(s["id"] == sid for s in listed)

        # --- mutate everything, including a full network swap ---
        topo = client.get("/network").json()
        neubau = next(c for c in topo["consumers"] if c["name"] == "Neubau")
        client.delete(f"/consumer/{neubau['id']}")
        client.post("/measurements/preset", json={"preset": "all_consumers"})
        imp = client.post("/networks/import",
                          json=_hillside_bundle("Anderes Netz"))
        assert imp.status_code == 200
        client.post("/config/apply", json={"network_id": "user_anderes-netz"})
        assert client.get("/status").json()[
            "network"]["id"] == "user_anderes-netz"

        # --- load the recipe back ---
        r = client.post(f"/scenarios/{sid}/load")
        assert r.status_code == 200
        assert r.json()["status"]["network"]["id"] == "tutorial_hillside"
        assert r.json()["active"]["scenario"] == "RT Test"

        # consumer op restored
        topo = client.get("/network").json()
        names = {c["name"] for c in topo["consumers"]}
        assert "Neubau" in names

        # sensor placement restored (explicit lists win)
        m = client.get("/measurements").json()
        assert m["mode"] == "standard"
        assert m["node_sensors"] == ["j4"]
        assert m["consumer_meters"] == []

        # clock restored (±2 ticks: the loaded scenario starts running)
        st = client.get("/status").json()
        assert st["running"] is True
        assert 300 <= st["step"] <= 302
        assert st["interval_seconds"] == 5.0

        # delete
        assert client.delete(f"/scenarios/{sid}").status_code == 200
        assert client.delete(f"/scenarios/{sid}").status_code == 404
        assert client.post(f"/scenarios/{sid}/load").status_code == 404
