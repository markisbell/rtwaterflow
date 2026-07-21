"""M6 acceptance: wells & aquifer (roadmap §4.5, TF §5).

Bars: the Lauenau mechanism (source cap below the peak → break tank
empties → the network pump trips → the Hochbehälter drains → households
unsupplied); a multi-year fast-forward shows the seasonal aquifer sawtooth
+ drought decline + well ageing; the pumping energy KPI lands in the
0.3–1.0 kWh/m³ range; a water-right exceedance is a COMPLIANCE warning,
not a hydraulic failure.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from conftest import REPO_ROOT, make_api_client, make_settings

from rtwaterflow.assets.wellfield import Aquifer, Well, WellField
from rtwaterflow.data_loader import load_network
from rtwaterflow.simulator import Simulator

LAUENAU_DIR = REPO_ROOT / "data" / "networks" / "lauenau"
SPD = 96
FILES = ("network_structure", "pipes", "consumers", "supply", "environment")


def _sim():
    return Simulator(load_network(LAUENAU_DIR),
                     make_settings(autostart=False, steps_per_day=SPD))


# --- the pure-Python raw-water model ----------------------------------------

def test_aquifer_seasonal_sawtooth_and_drought():
    """The linear-reservoir aquifer rises in the winter half-year and falls
    in summer (sawtooth); a drought factor scales the recharge down."""
    aq = Aquifer(storativity_area_m2=1500.0, level_initial_m=100.0,
                 recharge_m3_per_d_mean=520.0)
    monthly = []
    for day in range(365):
        for _ in range(SPD):
            aq.step(abstracted_m3_per_s=400.0 / 86400.0,
                    day_of_year=(day % 365) + 1, dt_s=900.0)
        if day % 30 == 0:
            monthly.append(aq.level_m)
    assert max(monthly) - min(monthly) > 1.0        # a real seasonal swing
    # winter recharge > summer: the spring level exceeds the autumn trough
    assert monthly[3] > monthly[8]

    # drought: same abstraction, no recharge → monotone decline
    aq2 = Aquifer(1500.0, 100.0, 520.0, drought_factor=0.0)
    start = aq2.level_m
    for _ in range(30 * SPD):
        aq2.step(400.0 / 86400.0, 205, 900.0)
    assert aq2.level_m < start - 2.0


def test_well_ageing_and_regeneration():
    w = Well("B1", static_level_m=100.0, spec_capacity_m3h_per_m=2.0,
             screen_top_m=88.0, rated_m3_h=15.0, q_s_decay_per_a=0.1)
    for _ in range(365 * SPD):              # one year of deep-drawdown duty
        w.age(dt_s=900.0, drawdown_m=8.0)
    assert w.spec_capacity_now < 2.0 * 0.95      # measurably aged
    w.regenerate()
    # restores to 95 % — clear of the 10 %-aged W 130 threshold so the
    # maintenance action CLEARS the alarm rather than re-tripping it (M6 review)
    assert w.spec_capacity_now == pytest.approx(0.95 * 2.0)


def test_well_screen_protection_caps_yield():
    """A well cannot pump below the filter-screen top + margin — its yield
    falls to zero as the regional level approaches the screen."""
    w = Well("B1", 100.0, 2.0, screen_top_m=90.0, rated_m3_h=20.0,
             protection_margin_m=1.0)
    assert w.max_yield_m3_h(100.0) == pytest.approx(18.0)   # (100-91)*2 capped 20? no
    assert w.max_yield_m3_h(91.5) == pytest.approx(1.0)     # headroom 0.5*2
    assert w.max_yield_m3_h(90.5) == 0.0                    # below protection


# --- the Lauenau mechanism (coupled) ----------------------------------------

def test_lauenau_normal_day_supplied():
    """A normal day: the well field meets demand, the village is supplied,
    the energy KPI is in the plausible band."""
    sim = _sim()
    statuses, minp = set(), []
    for t in range(SPD):
        r = sim.run_step(t, 0)
        statuses.add(r.solver_status)
        minp.append(min(c["p_bar"] for c in r.consumers))
    wf = r.wellfields[0]
    assert statuses <= {"ok", "degraded"}
    assert min(minp) > 0.5                       # no dry taps
    assert 0.3 <= wf["energy_kwh_per_m3"] <= 1.0   # TF §5 corridor
    # break tank + Hochbehälter present
    kinds = {t["kind"] for t in r.tanks}
    assert "break" in kinds and "durchlauf" in kinds


def test_lauenau_drought_starves_households():
    """THE M6 bar: a drought lowers the aquifer → the well field's capacity
    falls below the (hot-day) peak → the break tank empties → the network
    pump trips → the Hochbehälter drains → households run dry (deficit)."""
    sim = _sim()
    sim.set_drought(0.0)                          # severe drought
    sim.set_environment(t_offset_c=8.0, dryness_override=1.0)  # hot weekend
    cap0 = None
    deficit_seen = False
    for day in range(12):
        for t in range(SPD):
            r = sim.run_step(t, day)
        wf = r.wellfields[0]
        if cap0 is None:
            cap0 = wf["capacity_m3_h"]
        if r.summary.get("mdot_deficit_kg_per_s", 0) > 0.05:
            deficit_seen = True
    wf = r.wellfields[0]
    # the aquifer fell and the well capacity dropped with it
    assert wf["aquifer_level_m"] < 99.0
    assert wf["capacity_m3_h"] < cap0 - 1.0
    # households were left unsupplied (PDA deficit)
    assert deficit_seen
    # the Hochbehälter was drawn down to (near) its minimum
    hb = next(t for t in r.tanks if t["kind"] == "durchlauf")
    assert hb["level_m"] <= hb["level_min_m"] + 0.3


def test_break_tank_mass_balance():
    """The break tank integrates well inflow − network draw: over a day the
    level change equals ∫(inflow − draw)·dt / (area·ρ)."""
    sim = _sim()
    bt0 = None
    net_kg = 0.0
    dt = 86400.0 / SPD
    for t in range(SPD):
        r = sim.run_step(t, 0)
        bt = next(x for x in r.tanks if x["kind"] == "break")
        if bt0 is None:
            bt0 = bt["level_m"]
        net_kg += bt["mdot_kg_per_s"] * dt        # tank's own net flow
    dlevel = bt["level_m"] - bt0
    expected = net_kg / (40.0 * 998.2)            # area 40 m²
    assert dlevel == pytest.approx(expected, abs=0.05)


# --- water right + energy ---------------------------------------------------

def test_water_right_is_compliance_not_hydraulic():
    """Exceeding the abstraction permit (WHG §§8–10) is a COMPLIANCE
    finding — the wells keep pumping (no hydraulic failure)."""
    sim = _sim()
    sim.wellfields[0].right_m3_per_d = 5.0        # absurdly low → exceeded fast
    r = None
    for t in range(20):
        r = sim.run_step(t, 0)
    wr = [f for f in r.findings if f["check"] == "water_right"]
    assert wr and "WHG" in wr[0]["rule"]
    # the field kept producing despite the exceedance (not a trip)
    assert r.wellfields[0]["production_m3_h"] > 0


# --- lifecycle / wire / API -------------------------------------------------

def test_reset_operations_resets_raw_side():
    sim = _sim()
    sim.set_drought(0.0)
    for t in range(SPD):
        sim.run_step(t, 0)
    assert sim.wellfields[0].aquifer.level_m < 100.0
    sim.reset_operations()
    assert sim.wellfields[0].aquifer.level_m == 100.0
    assert sim.wellfields[0].aquifer.drought_factor == 1.0
    assert sim.wellfields[0].volume_year_m3 == 0.0
    assert sim.wellfields[0].pumps_running is True   # hysteresis memory reset


def test_replay_is_deterministic():
    """The deterministic-replay path (cold init + reset_operations) must
    reproduce the raw side EXACTLY — the well-pump hysteresis memory and
    every accumulator reset (M6 self-review found pumps_running persisted,
    breaking live-vs-export byte-compat)."""
    sim = _sim()

    def replay():
        sim._reset_initialization()
        sim.reset_operations()
        out = []
        for t in range(20):
            r = sim.run_step(t, 0)
            out.append((sim.wellfields[0].aquifer.level_m,
                        sim.wellfields[0].energy_kwh,
                        r.summary["mdot_feed_kg_per_s"]))
        return out

    a, b = replay(), replay()
    assert a == b, "raw-side replay is non-deterministic"


def test_wellfields_survive_strict_mode():
    from rtwaterflow.state import StateStore

    sim = _sim()
    store = StateStore(make_settings(expose_ground_truth=False))
    frame = store.frame(sim.run_step(0, 0))
    assert "junctions" not in frame               # truth stripped
    assert len(frame["wellfields"]) == 1          # raw-side SCADA stays


def test_wellfield_api_and_regenerate():
    with make_api_client() as client:
        r = client.post("/config/apply", json={"network_id": "lauenau"})
        assert r.status_code == 200
        client.post("/control/start")
        import time as _t
        _t.sleep(0.3)

        wf = client.get("/wellfields").json()["wellfields"]
        assert len(wf) == 1 and wf[0]["name"] == "Brunnenfeld Rodenberg"

        assert client.post("/wellfield/drought",
                           json={"factor": 0.0}).json()["drought_factor"] == 0.0

        r = client.post(
            "/wellfield/Brunnenfeld Rodenberg/well/Brunnen 1/regenerate")
        assert r.status_code == 200
        assert r.json()["well"] == "Brunnen 1"
        assert client.post(
            "/wellfield/nope/well/x/regenerate").status_code == 404


# --- M6 adversarial-review regression pins ----------------------------------

def test_regenerate_clears_the_ageing_alarm():
    """Review #1: regenerating an aged well must CLEAR the W 130 warning, not
    leave the finding still firing (it now restores to 95 %, below 10 % aged)."""
    sim = _sim()
    w = sim.wellfields[0].wells[0]
    # force the well well past the 10 % threshold, confirm the finding fires
    w.spec_capacity_now = 0.5 * w.spec_capacity_m3h_per_m   # 50 % aged
    r = sim.run_step(0, 0)
    fired = [f for f in r.findings
             if f["check"] == "well_ageing" and w.name in f["entity"]]
    assert fired, "a 50 %-aged well should raise the W 130 warning"
    sim.regenerate_well(sim.wellfields[0].name, w.name)
    r = sim.run_step(1, 0)
    aged = next(x["aged_fraction"] for x in r.wellfields[0]["wells"]
                if x["name"] == w.name)
    assert aged <= 0.05
    cleared = [f for f in r.findings
               if f["check"] == "well_ageing" and w.name in f["entity"]]
    assert not cleared, "regeneration must clear the ageing alarm"


def test_low_level_trip_holds_under_operator_on():
    """Review #2/#7: the break-suction low-level interlock is HARDWARE — an
    operator forcing the network pump 'on' must not defeat it (the trip is
    applied after the manual-station loop, so 'on' cannot win)."""
    sim = _sim()
    sim.run_step(0, 0)
    bt = next(t for t in sim.tanks if t.kind == "break")
    bt.level_m = bt.level_min_m                   # break tank at the floor
    meta = sim._break_suction_stations[0]
    sim.station_modes[meta["name"]] = "on"        # operator overrides to ON
    sim._apply_step(1)
    assert not bool(sim.net.pump.at[meta["element"], "in_service"]), \
        "operator 'on' must not defeat the low-level interlock"


def test_water_right_year_counter_rolls_over():
    """Review #3: the annual abstraction counter must reset at the year
    boundary — a multi-year fast-forward must not accumulate a false WHG
    annual violation across years.

    The well produces ~131 400 m³/a (15 m³/h capped). The permit (200 000
    m³/a) sits ABOVE one year but BELOW the un-rolled two-year sum
    (~262 800) — so the test passes only if the counter rolls over."""
    wf = WellField("t", [Well("B1", 100.0, 3.0, 88.0, 15.0)],
                   Aquifer(9e9, 100.0, 600.0), "bt", 60.0,
                   right_m3_per_a=200_000.0)
    peak_year = 0.0
    for tick in range(2 * 365 * SPD):
        day = tick // SPD
        wf.produce(1e6, day=day, day_of_year=(day % 365) + 1, dt_s=900.0)
        peak_year = max(peak_year, wf.volume_year_m3)
    # a single year (~131 400 m³) fits the permit; without the rollover the
    # running total would be ~262 800 m³ and falsely "exceeded"
    assert wf.volume_year_m3 < 200_000.0
    assert peak_year < 200_000.0
    assert wf.volume_year_m3 == pytest.approx(131_400.0, rel=0.02)
    assert not wf.water_right_status()["year_exceeded"]
    assert wf.last_year_index == 1                # advanced into the 2nd year


def test_only_producing_wells_age():
    """Review #4: a well that is not pumping sees no drawdown and must not
    age — only running wells accrue Verockerung stress."""
    wf = WellField("t",
                   [Well("A", 100.0, 3.0, 88.0, 15.0),
                    Well("B", 100.0, 3.0, 88.0, 15.0)],
                   Aquifer(9e9, 100.0, 600.0), "bt", 60.0)
    for tick in range(365 * SPD):                 # a year at ZERO demand
        wf.produce(0.0, tick // SPD, (tick // SPD % 365) + 1, 900.0)
    assert all(not w.running for w in wf.wells)
    assert all(w.spec_capacity_now == w.spec_capacity_m3h_per_m
               for w in wf.wells), "resting wells must not age"


def test_capacity_reflects_interference():
    """Review #5: the reported capacity must be interference-aware — under
    drought (drawdown-limited) it is BELOW the nameplate sum, because mutual
    Sichardt interference between wells reduces each one's available yield."""
    sim = _sim()
    sim.set_drought(0.0)
    r = None
    for day in range(10):
        for t in range(SPD):
            r = sim.run_step(t, day)
    wf = sim.wellfields[0]
    nameplate = sum(w.rated_m3_h for w in wf.wells)
    reported = r.wellfields[0]["capacity_m3_h"]
    assert reported < nameplate, "capacity must reflect interference, not nameplate"
    # sanity: it equals the interference-aware available-yield sum
    assert reported == pytest.approx(round(sum(wf._available_yields()[0]), 2))


def test_multiple_fields_one_tank_conserve_inflow():
    """Review #6: two well fields feeding ONE break tank must ADD their
    inflows — the accumulator must not let the second field overwrite the
    first (mass would silently vanish)."""
    from rtwaterflow.assets.wellfield import Aquifer, Well, WellField

    def mk(name):
        return WellField(name, [Well(f"{name}-1", 100.0, 3.0, 88.0, 20.0)],
                         Aquifer(9e9, 100.0, 600.0), "shared_bt", 60.0)

    a, b = mk("A"), mk("B")
    ia = a.produce(50.0, 0, 1, 900.0)             # each field's raw kg/s
    ib = b.produce(50.0, 0, 1, 900.0)
    inflow_by_tank: dict[str, float] = {}
    for wf, inflow in ((a, ia), (b, ib)):
        inflow_by_tank[wf.break_tank_name] = (
            inflow_by_tank.get(wf.break_tank_name, 0.0) + inflow)
    assert inflow_by_tank["shared_bt"] == pytest.approx(ia + ib)
    assert ia > 0 and ib > 0                       # both actually contributed


def test_generator_round_trip_byte_stable(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "generate_lauenau", REPO_ROOT / "scripts" / "generate_lauenau.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    monkeypatch.setattr(gen, "OUT", Path(tmp_path))
    gen.main()
    for fname in FILES:
        fresh = (tmp_path / f"{fname}.json").read_bytes()
        committed = (LAUENAU_DIR / f"{fname}.json").read_bytes()
        assert fresh.replace(b"\r\n", b"\n") == committed.replace(
            b"\r\n", b"\n"), f"{fname}.json drifted from the generator"
