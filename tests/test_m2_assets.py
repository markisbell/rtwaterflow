"""M2 acceptance: tanks, pump stations, hysteresis rules, check valves.

Runs the Musterdorf day (Durchlaufbehälter: Wasserwerk → Pumpwerk →
Steigleitung → Hochbehälter → zones) and pins the M2 physics bars:

* 24 h tank sawtooth with hysteresis pump cycling,
* the reverse-flow runaway CLOSED (pandapipes pumps assume zero-lift
  zero-resistance bypass for Q < 0 — without the check-valve layer the
  Hochbehälter drains backwards through the works at −51 kg/s),
* station telemetry honest and ON the solved curve,
* operator overrides (auto|on|off), reset, and the API surface.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import REPO_ROOT, make_api_client, make_settings

from rtwaterflow.data_loader import load_network
from rtwaterflow.network_builder import RHO_KG_M3, StationLiftStdType
from rtwaterflow.simulator import Simulator

MUSTERDORF_DIR = REPO_ROOT / "data" / "networks" / "musterdorf"
SPD = 96


@pytest.fixture(scope="module")
def inputs():
    return load_network(MUSTERDORF_DIR)


@pytest.fixture(scope="module")
def day_run(inputs):
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=SPD))
    frames = [sim.run_step(t, 0) for t in range(SPD)]
    return sim, frames


def _stations(frame):
    return [p for p in frame.producers if p["kind"] == "station"]


# --- the M2 physics bars ----------------------------------------------------

def test_day_converges_without_degradation(day_run):
    """Every frame converges. Since the M3 noisy demand profiles, a small
    share of ticks hit transitional-Reynolds states (several pipes at
    Re ≈ 1700–3800) the implicit Colebrook Newton cannot solve — those land
    honestly on the swamee-jain tier (explicit approximation), NEVER on the
    biased nikuradse last resort, and stay ≤ 5 % of the day."""
    _, frames = day_run
    assert all(f.converged for f in frames)
    degraded = [f for f in frames if f.solver_status != "ok"]
    assert len(degraded) <= len(frames) * 0.05, [f.step for f in degraded]
    for f in degraded:
        assert "swamee-jain" in (f.error or ""), (f.step, f.error)


def test_tank_sawtooth_with_hysteresis(inputs, day_run):
    """The teaching picture: pump ON below 2.4 m, OFF above 4.2 m — the
    level saws between the band edges (±1 tick of integration overshoot),
    with a sane duty cycle for the designed pump-vs-demand sizing."""
    _, frames = day_run
    ctl = inputs.supply.stations[0].control
    levels = [f.tanks[0]["level_m"] for f in frames]
    running = [_stations(f)[0]["running"] for f in frames]
    switches = sum(1 for a, b in zip(running, running[1:]) if a != b)
    assert switches >= 2, "pump never cycled through the band"
    assert min(levels) >= ctl.on_below_m - 0.2
    assert max(levels) <= ctl.off_above_m + 0.2
    duty = sum(running) / len(running)
    assert 0.15 < duty < 0.65, f"implausible pump duty {duty:.2f}"
    assert not any(f.tanks[0]["empty"] or f.tanks[0]["overflow"]
                   for f in frames)


def test_no_reverse_pump_flow_ever(day_run):
    """THE M2 regression pin: pandapipes pump std_types bypass reverse flow
    (zero lift, zero resistance) — before the check-valve layer the solver
    reliably converged onto a −51 kg/s backwards drain of the Hochbehälter
    through the running pump. No frame may ever show reverse station flow."""
    _, frames = day_run
    for f in frames:
        st = _stations(f)[0]
        mdot = st["mdot_kg_per_s"]
        assert mdot is None or mdot >= 0.0, (
            f"reverse pump flow {mdot} on the wire at step {f.step}")


def test_station_telemetry_on_the_curve(inputs, day_run):
    """Honest station SCADA: while running, solved p_out − p_in must sit ON
    the bundle's Q-H curve at the solved flow (within the operating-point
    tolerance + curve regression scatter) — the outer iteration may not
    park the pump at an off-curve lift."""
    _, frames = day_run
    st_spec = inputs.supply.stations[0]
    x = [p[0] for p in st_spec.curve]
    y = [p[1] for p in st_spec.curve]
    reg = np.polyfit(np.asarray(x, float), np.asarray(y, float), 2)
    checked = 0
    for f in frames:
        st = _stations(f)[0]
        if not st["running"] or not st["mdot_kg_per_s"]:
            continue
        q_m3h = st["mdot_kg_per_s"] / RHO_KG_M3 * 3600.0
        lift = st["p_out_bar"] - st["p_in_bar"]
        assert lift == pytest.approx(
            float(np.polyval(reg, q_m3h)), abs=0.05), f"off-curve at {f.step}"
        checked += 1
    assert checked > 5


def test_tank_mass_balance(inputs, day_run):
    _, frames = day_run
    tk = inputs.supply.tanks[0]
    dt_s = 86400.0 / SPD
    mdots = [f.tanks[0]["mdot_kg_per_s"] for f in frames]
    stored_kg = sum(mdots) * dt_s
    dlevel = frames[-1].tanks[0]["level_m"] - tk.level_initial_m
    expected_kg = dlevel * tk.area_m2 * RHO_KG_M3
    throughput_kg = sum(abs(m) for m in mdots) * dt_s
    assert abs(stored_kg - expected_kg) < 0.005 * throughput_kg


def test_buffer_time_kpi(day_run):
    """buffer_time_h = usable volume / current draw while the tank supplies;
    None/0 handling stays honest when it charges."""
    _, frames = day_run
    f = next(f for f in frames if f.tanks[0]["mdot_kg_per_s"] < -0.1)
    t = f.tanks[0]
    draw_m3_h = -t["mdot_kg_per_s"] / RHO_KG_M3 * 3600.0
    assert t["buffer_time_h"] == pytest.approx(
        t["volume_m3"] / draw_m3_h, rel=0.02)
    charging = next(f for f in frames if f.tanks[0]["mdot_kg_per_s"] > 0.1)
    assert charging.tanks[0]["buffer_time_h"] is None   # no countdown


def test_summary_carries_stored_flow(day_run):
    """While the pump charges the tank: feed = delivered + stored, balance
    closed — the M0 feed==delivered invariant generalized to storage."""
    _, frames = day_run
    f = next(f for f in frames
             if (f.summary.get("mdot_stored_kg_per_s") or 0) > 0.5)
    s = f.summary
    assert s["mdot_feed_kg_per_s"] == pytest.approx(
        s["mdot_delivered_kg_per_s"] + s["mdot_stored_kg_per_s"], abs=1e-3)
    assert abs(s["balance_err_kg_per_s"]) < 1e-3


# --- operator overrides and reset -------------------------------------------

def test_station_mode_overrides(inputs):
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=SPD))
    sname = inputs.supply.stations[0].name
    el = next(m["element"] for m in sim.index.producer_meta
              if m["kind"] == "station")

    # forced OFF: tank alone carries the zones
    sim.station_modes[sname] = "off"
    f = sim.run_step(0, 0)
    assert f.converged
    assert not bool(sim.net.pump.at[el, "in_service"])
    assert f.tanks[0]["mdot_kg_per_s"] < 0          # tank supplying

    # forced ON at a full tank (rule would stop): pump must run
    sim.tanks[0].level_m = 4.5                       # above off_above 4.2
    sim.station_modes[sname] = "on"
    f = sim.run_step(1, 0)
    assert f.converged
    st = next(p for p in f.producers if p["kind"] == "station")
    assert st["running"] is True and st["mode"] == "on"
    assert st["mdot_kg_per_s"] > 0

    # back to auto: the hysteresis takes over (full tank -> off)
    sim.station_modes[sname] = "auto"
    f = sim.run_step(2, 0)
    st = next(p for p in f.producers if p["kind"] == "station")
    assert st["running"] is False and st["mode"] == "auto"


def test_reset_operations_restores_initial_state(inputs):
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=SPD))
    sname = inputs.supply.stations[0].name
    for t in range(5):
        sim.run_step(t, 0)
    sim.station_modes[sname] = "off"
    sim.tanks[0].level_m = 1.7
    sim.reset_operations()
    assert sim.tanks[0].level_m == inputs.supply.tanks[0].level_initial_m
    assert sim.station_modes[sname] == "auto"
    assert all(r.running == r.initial_running for r in sim.rules.rules)
    # deterministic replay: the station operating point is back at the
    # cold-start seed, not the live warm lift
    std = sim.net["std_types"]["pump"][sname]
    assert std.lift_bar == pytest.approx(std.lift_seed_bar)


# --- the API surface ---------------------------------------------------------

def test_tanks_stations_api_and_override():
    with make_api_client() as client:
        r = client.post("/config/apply", json={"network_id": "musterdorf"})
        assert r.status_code == 200

        tanks = client.get("/tanks").json()
        assert len(tanks) == 1
        tk = tanks[0]
        assert tk["name"] == "Hochbehälter Musterberg"
        assert tk["kind"] == "durchlauf"
        assert tk["level_m"] == pytest.approx(3.2)
        assert {"volume_m3", "capacity_m3", "buffer_time_h", "overflow",
                "empty", "fire_reserve_breached"} <= set(tk)

        stations = client.get("/stations").json()
        assert len(stations) == 1
        st = stations[0]
        assert st["name"] == "Pumpwerk Mustertal"
        assert st["mode"] == "auto"
        assert st["control"]["mode"] == "hysteresis"
        assert st["curve"][0] == [0.0, 10.0]

        r = client.post("/station/Pumpwerk Mustertal", json={"mode": "off"})
        assert r.status_code == 200 and r.json()["mode"] == "off"
        assert client.get("/stations").json()[0]["mode"] == "off"
        assert client.post(
            "/station/nope", json={"mode": "on"}).status_code == 404
        assert client.post(
            "/station/Pumpwerk Mustertal",
            json={"mode": "sideways"}).status_code == 422


def test_scenario_keeps_station_mode():
    with make_api_client() as client:
        client.post("/config/apply", json={"network_id": "musterdorf"})
        client.post("/station/Pumpwerk Mustertal", json={"mode": "off"})
        sid = client.post("/scenarios", json={"name": "pumpe-aus"}).json()["id"]
        client.post("/station/Pumpwerk Mustertal", json={"mode": "on"})
        r = client.post(f"/scenarios/{sid}/load")
        assert r.status_code == 200
        assert client.get("/stations").json()[0]["mode"] == "off"
        client.delete(f"/scenarios/{sid}")


def test_recorder_round_trips_tank_and_station(tmp_path, inputs):
    """producers.csv carries the M2 station SCADA columns and tanks.csv the
    full tank state — the recording mirrors the wire 1:1 (M1 discipline)."""
    import csv

    from rtwaterflow.recorder import Recorder
    from rtwaterflow.state import StateStore

    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=SPD))
    store = StateStore(make_settings(recordings_dir=tmp_path))
    rec = Recorder(tmp_path)
    rid = rec.start({"network": {"name": "musterdorf"}})["id"]
    rec.record(store.frame(sim.run_step(0, 0)))
    rec.stop()

    prows = list(csv.DictReader(
        (tmp_path / rid / "producers.csv").open(encoding="utf-8")))
    st = next(r for r in prows if r["kind"] == "station")
    assert st["name"] == "Pumpwerk Mustertal"
    assert st["running"] in ("0", "1")
    assert st["mode"] == "auto"
    assert st["cv_closed"] in ("0", "1")
    tank_row = next(r for r in prows if r["kind"] == "tank")
    # end-of-tick level: initial 3.2 + one tick of pump charge (~0.1 m)
    assert 3.2 <= float(tank_row["level_m"]) <= 3.5

    trows = list(csv.DictReader(
        (tmp_path / rid / "tanks.csv").open(encoding="utf-8")))
    assert len(trows) == 1
    assert trows[0]["name"] == "Hochbehälter Musterberg"
    assert float(trows[0]["capacity_m3"]) == pytest.approx(216.0, abs=0.1)


# --- adversarial-review regression pins (M2) --------------------------------

def test_tank_wire_id_is_platform_pid(day_run):
    """tanks[].id must be the producer pid, never the per-kind list index
    (which collides with the slack's pid — review finding)."""
    _, frames = day_run
    f = frames[0]
    tank_prod = next(p for p in f.producers if p["kind"] == "tank")
    assert f.tanks[0]["id"] == tank_prod["id"] == 1


def test_absorbing_slack_signed_and_exported(hillside_docs):
    """Multi-source honesty: an absorbing slack publishes NEGATIVE mdot and
    its uptake books as mdot_exported, never as tank storage (review found
    abs() + the stored sum fabricating storage on tankless nets)."""
    from test_data_contract import _rebuild

    docs = hillside_docs
    docs["supply"]["supplies"].append(
        {"node": "j1", "name": "Tiefbehälter", "kind": "ext_grid",
         "p_bar": 3.0})
    sim = Simulator(_rebuild(docs), make_settings(autostart=False))
    f = sim.run_step(0, 0)
    assert f.converged
    slacks = {p["name"]: p for p in f.producers if p["kind"] == "slack"}
    assert slacks["Tiefbehälter"]["mdot_kg_per_s"] < 0      # absorbing
    assert slacks["Hochbehälter"]["mdot_kg_per_s"] > 0      # supplying
    s = f.summary
    assert s["mdot_exported_kg_per_s"] > 0.1
    assert s["mdot_stored_kg_per_s"] == 0.0                 # no tanks here
    assert abs(s["balance_err_kg_per_s"]) < 1e-3


def test_overflow_spill_split_from_stored(inputs):
    """At the level_max clamp the inflow the level cannot keep is SPILL —
    summary 'stored' stays level-effective and the balance still closes
    (review: spill was booked as stored, fabricating ~23 m³/h storage)."""
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=SPD))
    sname = inputs.supply.stations[0].name
    sim.station_modes[sname] = "on"
    sim.tanks[0].level_m = sim.tanks[0].level_max_m - 0.01
    f1 = sim.run_step(0, 0)          # fills the last centimetre + spills
    f2 = sim.run_step(1, 0)          # fully clamped tick
    assert f2.converged and f2.tanks[0]["overflow"]
    assert f2.tanks[0]["level_m"] == inputs.supply.tanks[0].level_max_m
    assert f2.tanks[0]["mdot_spill_kg_per_s"] > 1.0
    s = f2.summary
    assert s["mdot_spill_kg_per_s"] == pytest.approx(
        f2.tanks[0]["mdot_spill_kg_per_s"], abs=1e-6)
    # fully clamped: nothing is level-effective
    assert s["mdot_stored_kg_per_s"] == pytest.approx(0.0, abs=1e-6)
    assert abs(s["balance_err_kg_per_s"]) < 1e-3
    assert f1.converged


def test_marginal_pump_cold_start_settles():
    """A legal bundle whose static head sits ~0.4 bar under pump shutoff:
    the review found the old bracket logic re-firing shutoff after every
    reverse iterate — cold starts exhausted the solve cap and published
    degraded, off-curve frames. Pinned: cold start, warm ticks AND the
    deterministic replay reset all settle 'ok'."""
    import json

    docs = {}
    for name in ("network_structure", "pipes", "consumers", "supply",
                 "environment"):
        with open(REPO_ROOT / "data" / "networks" / "mustertal"
                  / f"{name}.json", encoding="utf-8") as fh:
            docs[name] = json.load(fh)
    for j in docs["network_structure"]["junctions"]:
        if j["name"] == "twr":
            j["elevation_m"] = 342.0     # static ≈ 4.0–4.2 bar vs 4.5 shutoff
    from test_data_contract import _rebuild

    sim = Simulator(_rebuild(docs),
                    make_settings(autostart=False, steps_per_day=SPD))
    frames = [sim.run_step(t, 0) for t in range(4)]
    assert all(f.converged for f in frames)
    assert {f.solver_status for f in frames} == {"ok"}, \
        [f.error for f in frames]
    for f in frames:
        st = _stations(f)[0]
        assert st["mdot_kg_per_s"] is None or st["mdot_kg_per_s"] >= 0
    sim.reset_operations()
    f = sim.run_step(0, 0)               # the bulk-export replay first frame
    assert f.converged and f.solver_status == "ok"


def test_manual_station_auto_means_configured_state():
    """mode 'auto' on a manual-control station returns it to its CONFIGURED
    running state each tick (review: nothing wrote in_service again — a
    check-valve closure latched silently forever)."""
    import json

    docs = {}
    for name in ("network_structure", "pipes", "consumers", "supply",
                 "environment"):
        with open(REPO_ROOT / "data" / "networks" / "mustertal"
                  / f"{name}.json", encoding="utf-8") as fh:
            docs[name] = json.load(fh)
    docs["supply"]["stations"][0]["control"] = {
        "mode": "manual", "running": True}
    from test_data_contract import _rebuild

    sim = Simulator(_rebuild(docs),
                    make_settings(autostart=False, steps_per_day=SPD))
    sname = docs["supply"]["stations"][0]["name"]
    el = next(m["element"] for m in sim.index.producer_meta
              if m["kind"] == "station")
    sim.station_modes[sname] = "auto"
    sim.net.pump.at[el, "in_service"] = False   # simulate a latched closure
    f = sim.run_step(0, 0)
    assert f.converged
    st = next(p for p in f.producers if p["kind"] == "station")
    assert st["running"] is True                # configured state restored


def test_tank_only_bundle_imports_and_previews(tmp_path):
    """Tank-only supply is contract-legal since M2 — preview/import used to
    500 on the first-ext_grid assumption (review finding)."""
    bundle = {
        "name": "Turmnetz",
        "network_structure": {"name": "Turmnetz", "junctions": [
            {"name": "twr", "kind": "source", "geo": [49.47, 9.03],
             "elevation_m": 330.0, "pn_bar": 0.2},
            {"name": "c1", "kind": "consumer", "geo": [49.4701, 9.031],
             "elevation_m": 300.0, "pn_bar": 3.0},
        ]},
        "pipes": {"pipes": [
            {"from_node": "twr", "to_node": "c1", "length_km": 0.15,
             "dn": 100, "material": "GGG"},
        ]},
        "consumers": {"consumers": [
            {"node": "c1", "name": "Dorf", "mdot_kg_per_s": 0.5},
        ]},
        "supply": {"supplies": [], "tanks": [
            {"node": "twr", "name": "Wasserturm", "area_m2": 20.0,
             "level_min_m": 0.5, "level_max_m": 4.0, "level_initial_m": 2.0},
        ]},
        "environment": {"resolution_minutes": 15, "steps": 96,
                        "t_air_c": [10.0] * 96},
    }
    with make_api_client(user_networks_dir=tmp_path,
                         recordings_dir=tmp_path / "rec") as client:
        r = client.post("/networks/import", json=bundle)
        assert r.status_code == 200, r.text
        prev = r.json()
        assert prev["supply"]["node"] == "twr"
        assert prev["supply"]["p_bar"] == pytest.approx(0.1958, abs=1e-3)
        r2 = client.get(f"/networks/{prev['id']}")
        assert r2.status_code == 200


# --- the numerical foundation -----------------------------------------------

def test_station_lift_std_type_semantics():
    """The constant-lift std_type the M2 solver strategy rests on: solver
    sees lift_bar flat for Q ≥ 0 and upstream's bypass 0 for Q < 0; the
    honest curve lives in curve_lift_bar (regression polynomial, ≥ 0)."""
    std = StationLiftStdType.from_list(
        "t", [0.0, 15.0, 30.0, 45.0], [10.0, 9.4, 8.4, 6.8], 2)
    assert std.shutoff_bar() == pytest.approx(
        std.curve_lift_bar(0.0), abs=1e-9)
    assert 9.9 < std.shutoff_bar() < 10.1          # regression intercept
    std.lift_bar = 7.5
    assert std.get_pressure(0.005) == 7.5           # constant for the solver
    assert std.get_pressure(-0.005) == 0.0          # upstream bypass kept
    arr = std.get_pressure(np.array([-0.01, 0.0, 0.02]))
    assert list(arr) == [0.0, 7.5, 7.5]
    # the curve itself decreases and clamps at 0 beyond range
    assert std.curve_lift_bar(30.0) < std.curve_lift_bar(15.0)
    assert std.curve_lift_bar(500.0) == 0.0
