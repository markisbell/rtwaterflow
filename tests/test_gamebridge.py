"""Puppet mode (gamebridge): the external clock must be a drop-in replacement
for the internal accelerated tick (mirrors rtheatflow's test_gamebridge.py),
and the ``/gb/*`` surface must implement the simgames co-simulation contract
v1 (``simgames/docs/contract/v1.md`` — the authoritative spec).

Core guarantee: N externally clocked steps produce the IDENTICAL result
sequence as N internally clocked steps on the same inputs — the game can own
time without changing the physics.
"""
from __future__ import annotations

import asyncio
import json

from conftest import HILLSIDE_DIR, make_api_client, make_settings

N_STEPS = 100

# Deterministic physics subset. Excluded on purpose: timestamp/solve_ms
# (wall-clock), estimated (wall-clock self-throttled observer cadence),
# controls/measurements/producers/tanks (deterministic but redundant —
# they derive from the same solve the compared keys pin).
_COMPARE_KEYS = ("step", "day", "converged", "solver_status",
                 "junctions", "pipes", "consumers", "summary")


def _normalize(frames: list[dict]) -> list[dict]:
    return [{k: f[k] for k in _COMPARE_KEYS} for f in frames]


def _build_engine():
    from rtwaterflow.data_loader import load_network
    from rtwaterflow.engine import RealtimeEngine
    from rtwaterflow.simulator import Simulator
    from rtwaterflow.state import StateStore

    settings = make_settings(autostart=False, step_interval_seconds=0.01)
    sim = Simulator(load_network(HILLSIDE_DIR), settings)
    store = StateStore(settings)
    return RealtimeEngine(sim, store, settings), store


def _run_internal_clock(n: int) -> list[dict]:
    """Drive the REAL internal loop (start + accelerated tick)."""

    async def go() -> list[dict]:
        engine, store = _build_engine()
        await engine.start()
        while len(store.history) < n:
            await asyncio.sleep(0.01)
        await engine.stop()
        return [store.frame(r) for r in list(store.history)[:n]]

    return asyncio.run(go())


def _run_external_clock(n: int) -> list[dict]:
    """Drive the SAME engine via external_step only (puppet mode)."""

    async def go() -> list[dict]:
        engine, store = _build_engine()
        for _ in range(n):
            await engine.external_step()
        return [store.frame(r) for r in list(store.history)[:n]]

    return asyncio.run(go())


def test_external_clock_equivalence():
    internal = _normalize(_run_internal_clock(N_STEPS))
    external = _normalize(_run_external_clock(N_STEPS))
    assert len(internal) == len(external) == N_STEPS
    for i, (a, b) in enumerate(zip(internal, external)):
        assert a == b, f"step {i}: internal and external results differ"


def test_external_step_refused_while_internal_clock_runs():
    import pytest

    async def go() -> None:
        engine, _store = _build_engine()
        await engine.start()
        with pytest.raises(RuntimeError):
            await engine.external_step()
        engine.pause()  # paused internal clock => external stepping is allowed
        await engine.external_step()
        await engine.stop()

    asyncio.run(go())


# ---------------------------------------------------------------------------
# Contract v1 surface (simgames docs/contract/v1.md, §3.1 water rows)
# ---------------------------------------------------------------------------

STEPS = 96


def _native_docs() -> dict:
    """A minimal game-built bundle (the WaterTopology builder convention):
    tower junction (elevation already includes the 25 m tower height), three
    zone consumers at hillside elevations, ONE ext_grid head supply at the
    tower node (p_bar 0.5 — the tank surface seed), no tanks/stations in
    native (device-synthesized)."""
    junctions = [
        {"name": "twr", "kind": "source", "geo": [48.0, 8.0],
         "elevation_m": 325.0, "pn_bar": 4.0},
        {"name": "j0", "kind": "node", "geo": [48.0004, 8.0],
         "elevation_m": 300.0, "pn_bar": 4.0},
        {"name": "wz0", "kind": "consumer", "geo": [48.0008, 8.0],
         "elevation_m": 300.0, "pn_bar": 4.0},
        {"name": "wz1", "kind": "consumer", "geo": [48.0004, 8.0004],
         "elevation_m": 302.5, "pn_bar": 4.0},
        {"name": "wz2", "kind": "consumer", "geo": [48.0004, 8.0008],
         "elevation_m": 305.0, "pn_bar": 4.0},
    ]
    pipes = [
        {"from_node": "twr", "to_node": "j0", "length_km": 0.3,
         "inner_diameter_mm": 150.0, "k_mm": 0.1},
        {"from_node": "j0", "to_node": "wz0", "length_km": 0.25,
         "inner_diameter_mm": 150.0, "k_mm": 0.1},
        {"from_node": "j0", "to_node": "wz1", "length_km": 0.25,
         "inner_diameter_mm": 150.0, "k_mm": 0.1},
        {"from_node": "wz1", "to_node": "wz2", "length_km": 0.25,
         "inner_diameter_mm": 150.0, "k_mm": 0.1},
    ]
    consumers = [
        {"node": name, "name": name, "mdot_kg_per_s": 0.01,
         "kind": "residential"}
        for name in ("wz0", "wz1", "wz2")
    ]
    return {
        "network_structure": {"name": "gb_test_net", "junctions": junctions},
        "pipes": {"pipes": pipes},
        "consumers": {"consumers": consumers},
        "supply": {"supplies": [{"node": "twr", "name": "head",
                                 "kind": "ext_grid", "p_bar": 0.5}]},
        "environment": {"resolution_minutes": 1440 // STEPS, "steps": STEPS,
                        "t_air_c": [10.0] * STEPS},
    }


def _topology(native: dict | None = None, devices: list | None = None) -> dict:
    return {
        "contract": "1.0",
        "network_kind": "water",
        "name": "gb_test_net",
        "steps_per_day": STEPS,
        "native": native or _native_docs(),
        "zones": [
            {"id": "wz0", "consumer": "wz0"},
            {"id": "wz1", "consumer": "wz1"},
            {"id": "wz2", "consumer": "wz2"},
        ],
        "devices": devices if devices is not None else [
            # HEAD source first (slack-first convention): the tower
            {"id": "tower", "kind": "water_tower", "node": "twr",
             "params": {"volume_m3": 40.0, "tower_height_m": 25.0}},
            {"id": "well", "kind": "well", "node": "j0",
             "params": {"rated_m3_h": 2.0}},
            {"id": "pump", "kind": "water_pump", "node": "wz1",
             "params": {"rated_m3_h": 2.0, "head_m": 30.0, "eta": 0.6}},
        ],
    }


def _minus_solve_ms(result: dict) -> dict:
    stripped = dict(result)
    stripped.pop("solve_ms", None)
    return stripped


def test_gb_version_contract():
    with make_api_client(external_clock=True) as client:
        v = client.get("/gb/version").json()
        assert v["contract"] == "1.1"  # 1.1: water device rows (§3.1)
        assert v["backend"] == "rtwaterflow"
        assert "pandapipes" in v["solver"]
        assert v["external_clock"] is True


def test_gb_step_requires_reset_and_latest_404():
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/step",
                           json={"t": 0, "dt_s": 900}).status_code == 400
        assert client.get("/gb/result/latest").status_code == 404
        assert client.post("/gb/net/patch", json=[]).status_code == 400


def test_gb_reset_rejects_bad_documents():
    """Contract §3.1: a bad document is a 400 and leaves the running network
    untouched (everything is validated before the swap)."""
    with make_api_client(external_clock=True) as client:
        doc = _topology()
        del doc["zones"]
        assert client.post("/gb/net/reset", json=doc).status_code == 400

        doc = _topology()
        doc["network_kind"] = "heat"
        assert client.post("/gb/net/reset", json=doc).status_code == 400

        doc = _topology()
        doc["steps_per_day"] = 48  # native carries a 96-step environment
        assert client.post("/gb/net/reset", json=doc).status_code == 400

        doc = _topology()
        doc["zones"][0]["consumer"] = "no such consumer"
        assert client.post("/gb/net/reset", json=doc).status_code == 400

        doc = _topology(devices=[{"id": "chp1", "kind": "chp", "node": "j0",
                                  "params": {"pq_ratio": 0.5}}])
        assert client.post("/gb/net/reset", json=doc).status_code == 400

        doc = _topology()
        del doc["native"]["environment"]
        assert client.post("/gb/net/reset", json=doc).status_code == 400

        doc = _topology(devices=[{"id": "w", "kind": "well",
                                  "node": "nowhere"}])
        assert client.post("/gb/net/reset", json=doc).status_code == 400

        # the default network is still the active one — no swap happened
        assert client.get("/config/active").json()["network_id"] \
            == "tutorial_hillside"


def test_gb_reset_step_result_contract():
    """The §4 step path end-to-end: reset → step → idempotent re-send →
    out-of-order 409 → /gb/result/latest, with the §3.1 water device mapping
    (tower head → tank soc; well/pump injections → q_m3h + positive pump
    coupling; Wagner supplied; zone detail p_bar)."""
    with make_api_client(external_clock=True) as client:
        r = client.post("/gb/net/reset", json=_topology())
        assert r.status_code == 200, r.text
        reset = r.json()
        assert reset["ok"] is True
        assert reset["network_kind"] == "water"
        assert reset["n_zones"] == 3
        assert reset["n_devices"] == 3
        assert isinstance(reset["warmup_solve_ms"], (int, float))

        req = {"t": 7, "dt_s": 900,
               "weather": {"temp_c": 18.0},
               "zone_demand": {"wz0": {"value": 1.5}, "wz1": {"value": 1.0},
                               "wz2": {"value": 0.8}},
               "device_setpoints": {"well": {"yield_factor": 1.0},
                                    "pump": {"enabled": True}}}
        r = client.post("/gb/step", json=req)
        assert r.status_code == 200, r.text
        res = r.json()
        assert res["t"] == 7
        assert res["status"] == "converged"
        assert res["solve_ms"] >= 0
        for zid in ("wz0", "wz1", "wz2"):
            zone = res["zones"][zid]
            assert zone["supplied"] >= 0.99          # Wagner PDD, healthy
            assert isinstance(zone["detail"]["p_bar"], float)
            assert 1.5 <= zone["detail"]["p_bar"] <= 8.0
        # no pressure_low on a healthy net
        assert not [v for v in res["violations"]
                    if v["kind"] == "pressure_low"]
        tower = res["devices"]["tower"]
        assert 0.0 <= tower["soc"] <= 1.0
        assert isinstance(tower["detail"]["level_m"], float)
        well = res["devices"]["well"]
        assert abs(well["detail"]["q_m3h"] - 2.0) < 1e-6
        pump = res["devices"]["pump"]
        assert abs(pump["detail"]["q_m3h"] - 2.0) < 1e-6
        # pump coupling: POSITIVE p_el (draws while pumping, §3.1);
        # rho*g*Q*H/eta = 1000*9.81*(2/3600)*30/0.6 = 272.5 W
        p_el = res["coupling_out"]["pump"]["p_el_kw"]
        assert abs(p_el - 0.2725) < 1e-3
        assert "well" not in res["coupling_out"]  # wells couple nothing

        # idempotent re-send: cached result, no re-solve (§0.3)
        r2 = client.post("/gb/step", json=req)
        assert r2.status_code == 200
        assert _minus_solve_ms(r2.json()) == _minus_solve_ms(res)

        # out-of-order: the one 4xx in the step path (§0.3/§4)
        bad = dict(req)
        bad["t"] = 12
        r3 = client.post("/gb/step", json=bad)
        assert r3.status_code == 409
        body = r3.json()
        assert body["status"] == "error"
        assert body["error"] == "out_of_order"
        assert body["expected"] == [7, 8]

        # last_t + 1 advances
        nxt = dict(req)
        nxt["t"] = 8
        assert client.post("/gb/step", json=nxt).status_code == 200

        # /gb/result/latest equals the last result (§4)
        r4 = client.get("/gb/result/latest")
        assert r4.status_code == 200
        assert r4.json()["t"] == 8


def test_gb_ws_step_channel():
    """WS /gb/ws behaves identically to POST /gb/step (contract §1)."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_topology()).status_code == 200
        with client.websocket_connect("/gb/ws") as ws:
            for t in range(2):
                ws.send_text(json.dumps({
                    "t": t, "dt_s": 900,
                    "zone_demand": {"wz0": {"value": 1.2}}}))
                frame = json.loads(ws.receive_text())
                assert frame["t"] == t
                assert frame["status"] == "converged"
                assert frame["zones"]["wz0"]["supplied"] >= 0.99
            # out-of-order -> error frame, socket stays open (§4)
            ws.send_text(json.dumps({"t": 40, "dt_s": 900}))
            err = json.loads(ws.receive_text())
            assert err["status"] == "error"
            assert err["error"] == "out_of_order"
            assert err["expected"] == [1, 2]
            # malformed frame -> bad_request error frame, socket stays open
            ws.send_text("{not json")
            err = json.loads(ws.receive_text())
            assert err["status"] == "error"
            assert err["error"] == "bad_request"
            # and the channel still steps
            ws.send_text(json.dumps({"t": 2, "dt_s": 900}))
            assert json.loads(ws.receive_text())["status"] == "converged"


def test_gb_zone_sample_and_hold():
    """Contract §4: a missing zone holds its previous value; zones never
    seen default to 0 (zero-demand sinks are legal water hydraulics)."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_topology()).status_code == 200
        client.post("/gb/step", json={
            "t": 0, "dt_s": 900, "zone_demand": {"wz0": {"value": 1.8}}})
        # native /state keeps working and shows the game demand in physics
        state = client.get("/state").json()
        q = {c["name"]: c["mdot_demand_kg_per_s"]
             for c in state["consumers"]}
        assert abs(q["wz0"] - 1.8 / 3.6) < 1e-6
        assert q["wz1"] == 0.0   # never sent -> 0
        assert q["wz2"] == 0.0

        # wz0 missing on the next step -> held at 1.8 m3/h
        client.post("/gb/step", json={"t": 1, "dt_s": 900})
        state = client.get("/state").json()
        q = {c["name"]: c["mdot_demand_kg_per_s"]
             for c in state["consumers"]}
        assert abs(q["wz0"] - 1.8 / 3.6) < 1e-6


def test_gb_pump_off_tank_drains_pressure_falls():
    """The §3.1 water gameplay loop: pump enabled=false + well yield 0 stop
    every injection → the tower supplies the zones alone → its level (soc)
    falls monotonically and the zone pressure falls with the head."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_topology()).status_code == 200
        demand = {"wz0": {"value": 2.5}, "wz1": {"value": 2.5},
                  "wz2": {"value": 2.5}}
        r = client.post("/gb/step", json={
            "t": 0, "dt_s": 900, "zone_demand": demand,
            "device_setpoints": {"well": {"yield_factor": 1.0},
                                 "pump": {"enabled": True}}}).json()
        p0 = r["zones"]["wz2"]["detail"]["p_bar"]
        soc0 = r["devices"]["tower"]["soc"]
        assert r["devices"]["pump"]["detail"]["q_m3h"] > 0

        socs, p_last = [soc0], p0
        for t in range(1, 9):
            r = client.post("/gb/step", json={
                "t": t, "dt_s": 900, "zone_demand": demand,
                "device_setpoints": {"well": {"yield_factor": 0.0},
                                     "pump": {"enabled": False}}}).json()
            assert r["status"] in ("converged", "degraded")
            assert r["devices"]["pump"]["detail"]["q_m3h"] == 0.0
            assert r["coupling_out"]["pump"]["p_el_kw"] == 0.0
            assert r["devices"]["well"]["detail"]["q_m3h"] == 0.0
            socs.append(r["devices"]["tower"]["soc"])
            p_last = r["zones"]["wz2"]["detail"]["p_bar"]
        # 7.5 m3/h from a 10 m2 tank: the level falls every tick
        assert all(b < a for a, b in zip(socs, socs[1:])), socs
        assert p_last < p0 - 0.05, (p0, p_last)


def test_gb_empty_tower_is_a_dead_head_and_recovers():
    """Game pin: an EMPTY tower is a DEAD head — dry zones (supplied << 1),
    not a merely weaker gravity feed (the ext_grid would otherwise keep
    supplying phantom water at its elevation head). Net inflow recovers it."""
    devices = [
        {"id": "tower", "kind": "water_tower", "node": "twr",
         "params": {"volume_m3": 2.0, "tower_height_m": 25.0}},
        {"id": "well", "kind": "well", "node": "j0",
         "params": {"rated_m3_h": 4.0}},
    ]
    with make_api_client(external_clock=True) as client:
        assert client.post(
            "/gb/net/reset",
            json=_topology(devices=devices)).status_code == 200
        demand = {"wz0": {"value": 2.5}, "wz1": {"value": 2.5},
                  "wz2": {"value": 2.5}}
        # drain: well off, 7.5 m3/h from a 0.5 m2 basin -> empty in ~1 tick
        r = None
        for t in range(4):
            r = client.post("/gb/step", json={
                "t": t, "dt_s": 900, "zone_demand": demand,
                "device_setpoints": {"well": {"yield_factor": 0.0}}}).json()
        assert r["devices"]["tower"]["soc"] == 0.0
        # dead head: dry zones, honest pressure_low; the tower junction
        # carries a below-zero boundary pressure, so the frame may be
        # honestly "degraded" (M5 validity guard) — never an HTTP error
        assert r["status"] in ("converged", "degraded")
        assert r["zones"]["wz0"]["supplied"] < 0.1
        assert r["zones"]["wz0"]["detail"]["p_bar"] < 0.5
        assert [v for v in r["violations"] if v["kind"] == "pressure_low"]
        # recovery: the well refills the basin -> head + supply return
        for t in range(4, 7):
            r = client.post("/gb/step", json={
                "t": t, "dt_s": 900,
                "zone_demand": {z: {"value": 0.2} for z in demand},
                "device_setpoints": {"well": {"yield_factor": 1.0}}}).json()
        assert r["devices"]["tower"]["soc"] > 0.1
        assert r["zones"]["wz0"]["supplied"] >= 0.99
        assert r["zones"]["wz0"]["detail"]["p_bar"] > 2.0


def test_gb_towerless_head_collapses_when_dead():
    """A towerless net: the head-bound pump keeps its supply p_bar while
    enabled; disabling it collapses the boundary (~0.05 bar) and the Wagner
    PDD dries the zone (supplied ≈ 0, pressure_low critical)."""
    native = _native_docs()
    native["supply"] = {"supplies": [{"node": "twr", "name": "head",
                                      "kind": "ext_grid", "p_bar": 4.0}]}
    # flatten: head junction at zone elevation so the collapse is decisive
    for j in native["network_structure"]["junctions"]:
        j["elevation_m"] = 300.0
    devices = [
        {"id": "feed", "kind": "water_pump", "node": "twr",
         "params": {"rated_m3_h": 10.0, "head_m": 40.0, "eta": 0.6}},
    ]
    with make_api_client(external_clock=True) as client:
        assert client.post(
            "/gb/net/reset",
            json=_topology(native=native, devices=devices)).status_code == 200
        r = client.post("/gb/step", json={
            "t": 0, "dt_s": 900,
            "zone_demand": {"wz0": {"value": 2.0}},
            "device_setpoints": {"feed": {"enabled": True}}}).json()
        assert r["status"] == "converged"
        assert r["zones"]["wz0"]["supplied"] >= 0.99
        assert r["zones"]["wz0"]["detail"]["p_bar"] > 3.0
        assert r["coupling_out"]["feed"]["p_el_kw"] > 0

        r = client.post("/gb/step", json={
            "t": 1, "dt_s": 900,
            "device_setpoints": {"feed": {"enabled": False}}}).json()
        assert r["status"] in ("converged", "degraded")
        assert r["zones"]["wz0"]["supplied"] < 0.1
        assert r["zones"]["wz0"]["detail"]["p_bar"] < 0.5
        low = [v for v in r["violations"] if v["kind"] == "pressure_low"]
        assert low and low[0]["severity"] == "critical"
        assert r["coupling_out"]["feed"]["p_el_kw"] == 0.0


def test_gb_patch_roundtrip_and_tolerance():
    """Contract §3.2: device ops, tolerant per entry. Patchable water kinds
    are the source injections (wells/pumps); pressure boundaries need a
    full reset."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_topology()).status_code == 200
        probe = {"id": "probe_well2", "kind": "well", "node": "j0",
                 "params": {"rated_m3_h": 5.0}}
        r = client.post("/gb/net/patch",
                        json=[{"op": "add_device", "device": probe}])
        assert r.status_code == 200
        assert r.json() == {"applied": ["probe_well2"], "errors": []}

        # the new well injects: q = yield_factor * rated
        res = client.post("/gb/step", json={
            "t": 0, "dt_s": 900,
            "zone_demand": {"wz0": {"value": 1.0}},
            "device_setpoints": {"probe_well2": {"yield_factor": 0.5}}}).json()
        assert abs(res["devices"]["probe_well2"]["detail"]["q_m3h"]
                   - 2.5) < 1e-6

        # set_device updates params (the injection reacts)
        r = client.post("/gb/net/patch", json=[
            {"op": "set_device", "id": "probe_well2",
             "params": {"rated_m3_h": 8.0}}])
        assert r.json()["applied"] == ["probe_well2"]
        res = client.post("/gb/step", json={"t": 1, "dt_s": 900}).json()
        assert abs(res["devices"]["probe_well2"]["detail"]["q_m3h"]
                   - 4.0) < 1e-6

        # tolerant per entry: applied ops stay applied even if later ops fail
        r = client.post("/gb/net/patch", json=[
            {"op": "remove_device", "id": "probe_well2"},
            {"op": "remove_device", "id": "__no_such_device__"},
            {"op": "add_device", "device": {"id": "well", "kind": "well",
                                            "node": "j0"}},   # duplicate id
            {"op": "remove_device", "id": "tower"},           # head-bound
        ])
        assert r.status_code == 200
        body = r.json()
        assert body["applied"] == ["probe_well2"]
        assert [e["index"] for e in body["errors"]] == [1, 2, 3]
        # the removed device is gone from the results
        res = client.post("/gb/step", json={"t": 2, "dt_s": 900}).json()
        assert "probe_well2" not in res["devices"]


def test_gb_session_ends_on_native_network_swap():
    """A native /config/apply after a gb session must invalidate the gb
    zone/device maps (they point at the old net) and restore the configured
    tick raster — stepping then requires a fresh /gb/net/reset."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_topology()).status_code == 200
        assert client.get("/status").json()["steps_per_day"] == STEPS
        assert client.post("/gb/step",
                           json={"t": 0, "dt_s": 900}).status_code == 200
        r = client.post("/config/apply",
                        json={"network_id": "tutorial_hillside"})
        assert r.status_code == 200
        assert client.post("/gb/step",
                           json={"t": 1, "dt_s": 900}).status_code == 400
        assert client.get("/gb/result/latest").status_code == 404
        # back on the configured raster (make_settings default: 1440/day)
        assert client.get("/status").json()["steps_per_day"] == 1440


def test_gb_reset_clears_last_t():
    """Contract §3.1: a reset clears last_t — the next step may carry ANY t
    (the game clock continues across resets)."""
    with make_api_client(external_clock=True) as client:
        assert client.post("/gb/net/reset", json=_topology()).status_code == 200
        assert client.post("/gb/step",
                           json={"t": 5, "dt_s": 900}).status_code == 200
        assert client.post("/gb/net/reset", json=_topology()).status_code == 200
        assert client.post("/gb/step",
                           json={"t": 4711, "dt_s": 900}).status_code == 200
        assert client.get("/gb/result/latest").json()["t"] == 4711
