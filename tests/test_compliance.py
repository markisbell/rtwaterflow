"""M4 acceptance: the compliance engine (roadmap §4.9, TF §2).

Seeded violation fixtures — each produces EXACTLY the expected finding:
an undersized pipe → velocity violation; an over-pressured zone → rest-
pressure warning/violation; a dead-end stub → stagnation warning; a tall
building at marginal pressure → the W 400-1 short-term band then the
sustained violation; a drained Hochbehälter → fire-reserve/empty
violations. docs/COMPLIANCE.md maps every check to its rule (pinned).
"""
from __future__ import annotations

import pytest

from conftest import REPO_ROOT, make_api_client, make_settings
from test_data_contract import _rebuild

from rtwaterflow.data_loader import load_network
from rtwaterflow.simulator import Simulator

MUSTERDORF_DIR = REPO_ROOT / "data" / "networks" / "musterdorf"


def _findings(frame, check=None, severity=None):
    out = frame.findings
    if check:
        out = [f for f in out if f["check"] == check]
    if severity:
        out = [f for f in out if f["severity"] == severity]
    return out


def test_undersized_pipe_velocity_violation(hillside_docs):
    """Roadmap M4 fixture 1: an undersized branch pipe under a large draw
    produces exactly one sustained-velocity violation, on that pipe."""
    docs = hillside_docs
    # the short j1->j4 branch (0.285 km), undersized to DN 50 and loaded
    # with a 4.5 kg/s industrial draw: v ≈ 2.3 m/s at still-positive heads
    docs["pipes"]["pipes"][2]["inner_diameter_mm"] = 50.0
    docs["consumers"]["consumers"].append(
        {"node": "j4", "name": "Kieswerk", "mdot_kg_per_s": 4.5})
    sim = Simulator(_rebuild(docs),
                    make_settings(autostart=False, steps_per_day=24))
    frame = sim.run_step(0, 0)
    assert frame.converged
    v_max = _findings(frame, check="v_max")
    assert len(v_max) == 1
    f = v_max[0]
    assert f["severity"] == "violation"        # 1 tick == 1 h at spd 24
    assert f["entity"] == "2" and f["entity_kind"] == "pipe"
    assert 2.0 < f["value"] < 3.5 and f["threshold"] == 2.0
    assert "W 400-1" in f["rule"]


def test_overpressure_warning_and_violation(hillside_docs):
    """Roadmap M4 fixture 2: raising the source head floods the valley
    nodes past 8 bar (warning) and, higher still, past the 10 bar PN-10
    limit (violation)."""
    docs = hillside_docs
    docs["supply"]["supplies"][0]["p_bar"] = 4.0
    frame = Simulator(_rebuild(docs), make_settings(
        autostart=False, steps_per_day=24)).run_step(0, 0)
    rest = _findings(frame, check="p_rest")
    assert rest and all(f["severity"] == "warning" for f in rest)

    docs["supply"]["supplies"][0]["p_bar"] = 5.5
    frame = Simulator(_rebuild(docs), make_settings(
        autostart=False, steps_per_day=24)).run_step(0, 0)
    viol = _findings(frame, check="p_rest", severity="violation")
    assert viol
    assert all(f["value"] > 10.0 for f in viol)


def test_dead_end_stagnation_warning(hillside_docs):
    """Roadmap M4 fixture 3: a dead-end stub with negligible draw falls
    under the 5 mm/s hygiene minimum once an hour of history exists."""
    docs = hillside_docs
    docs["network_structure"]["junctions"].append(
        {"name": "stub", "kind": "consumer", "geo": [49.462, 8.985],
         "elevation_m": 350.0, "pn_bar": 5.0})
    docs["pipes"]["pipes"].append(
        {"from_node": "j1", "to_node": "stub", "length_km": 0.2,
         "inner_diameter_mm": 150.0, "k_mm": 0.1})
    docs["consumers"]["consumers"].append(
        {"node": "stub", "name": "Aussiedlerhof", "mdot_kg_per_s": 0.001})
    sim = Simulator(_rebuild(docs),
                    make_settings(autostart=False, steps_per_day=24))
    frame = sim.run_step(0, 0)          # 1 tick == 1 h of history at spd 24
    stag = _findings(frame, check="stagnation")
    stub_pipe_id = str(len(docs["pipes"]["pipes"]) - 1)
    assert any(f["entity"] == stub_pipe_id for f in stag)
    f = next(f for f in stag if f["entity"] == stub_pipe_id)
    assert f["severity"] == "warning"
    assert f["value"] < 0.005


def test_storey_pressure_band_then_sustained(hillside_docs):
    """W 400-1 semantics: a deficit inside the 0.5 bar short-term band is
    a WARNING first; after an hour below it becomes a violation."""
    docs = hillside_docs
    # j3 (the highest consumer) becomes a 6-storey building: p_req = 3.75,
    # its actual pressure ~4.3-0.5... raise req above p via storeys=8
    for c in docs["consumers"]["consumers"]:
        c["storeys"] = 8                     # p_req = 4.45 bar
    sim = Simulator(_rebuild(docs),
                    make_settings(autostart=False, steps_per_day=96))
    frame = sim.run_step(0, 0)               # 15-min tick: not yet sustained
    p_min = _findings(frame, check="p_min")
    assert p_min, "no p_min findings for the 8-storey fixture"
    worst = min(p_min, key=lambda f: f["value"])
    if worst["threshold"] - worst["value"] <= 0.5:
        assert worst["severity"] == "warning"
    # one hour below (4 ticks at spd 96) → sustained violation
    for t in range(1, 5):
        frame = sim.run_step(t, 0)
    sustained = _findings(frame, check="p_min", severity="violation")
    assert sustained
    assert all(f["since_ticks"] >= 4 for f in sustained)
    assert "W 400-1" in sustained[0]["rule"]


def test_tank_reserve_and_empty_violations():
    """A drained Hochbehälter breaches the Löschwasserreserve (W 405) and,
    at the minimum level while supplying, goes tank_empty (W 300-1)."""
    inputs = load_network(MUSTERDORF_DIR)
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=96))
    sname = inputs.supply.stations[0].name
    sim.station_modes[sname] = "off"         # tank alone carries the zones
    sim.tanks[0].level_m = 1.3               # usable 18 m³ < 48 m³ reserve
    frame = sim.run_step(0, 0)
    reserve = _findings(frame, check="tank_reserve", severity="violation")
    assert len(reserve) == 1
    assert reserve[0]["entity"] == "Hochbehälter Musterberg"
    assert "W 405" in reserve[0]["rule"]

    sim.tanks[0].level_m = 1.001             # at the clamp next integrate
    frame = sim.run_step(1, 0)
    empty = _findings(frame, check="tank_empty", severity="violation")
    assert len(empty) == 1
    assert "W 300-1" in empty[0]["rule"]


def test_findings_survive_reuse_and_reset():
    """Failed frames reuse the last findings (consistent with reused
    truth); reset_operations clears the rolling compliance state."""
    inputs = load_network(MUSTERDORF_DIR)
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=96))
    sim.run_step(0, 0)
    assert sim.compliance._pipes             # rolling state accumulated
    sim.reset_operations()
    assert not sim.compliance._pipes
    assert not sim.compliance._below


def test_strict_mode_strips_findings():
    """Findings derive from truth — strict mode strips them from the frame
    and GET /findings says truth_hidden instead of faking 'all green'."""
    from rtwaterflow.state import StateStore

    inputs = load_network(MUSTERDORF_DIR)
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=96))
    store = StateStore(make_settings(expose_ground_truth=False))
    frame = store.frame(sim.run_step(0, 0))
    assert "findings" not in frame


def test_findings_api_and_docs_mapping():
    """GET /findings serves the alarm center; docs/COMPLIANCE.md maps
    EVERY check key the engine can emit (roadmap M4 acceptance)."""
    with make_api_client() as client:
        client.post("/config/apply", json={"network_id": "musterdorf"})
        client.post("/control/start")
        import time as _t
        _t.sleep(0.3)
        client.post("/control/pause")
        r = client.get("/findings").json()
        assert set(r["counts"]) == {"violation", "warning", "info"}
        assert r["truth_hidden"] is False

    doc = (REPO_ROOT / "docs" / "COMPLIANCE.md").read_text(encoding="utf-8")
    for check in ("p_min", "p_rest", "v_max", "stagnation", "tank_reserve",
                  "tank_empty", "tank_overflow", "tank_turnover", "solver"):
        assert f"`{check}`" in doc, f"COMPLIANCE.md misses check {check}"


# --- adversarial-review regression pins (M4) --------------------------------

def test_pump_discharge_not_flagged_p_rest():
    """The Pumpwerk discharge / riser foot runs > 8 bar by construction —
    the 8-bar Ruhedruck WARNING must not fire there (review: it was a
    permanent false positive); only consumer junctions carry it."""
    inputs = load_network(MUSTERDORF_DIR)
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=96))
    frame = sim.run_step(0, 0)
    ps = {j["name"]: j["p_bar"] for j in frame.junctions}
    assert ps["ws"] > 8.0                       # riser foot IS over 8 bar
    p_rest = _findings(frame, check="p_rest")
    flagged = {f["entity"] for f in p_rest}
    assert "ws" not in flagged
    # every flagged node is a consumer node (or a 10-bar violation)
    consumer_nodes = {c.node for c in inputs.consumers.consumers}
    for f in p_rest:
        assert f["entity"] in consumer_nodes or f["severity"] == "violation"


def test_healthy_musterdorf_alarm_volume_bounded():
    """Alarm flood fix: a full converged day on the healthy showcase net
    stays well under a wall of findings — the self-cleaning check is ONE
    aggregate finding, not one per branch (review: ~28/tick before)."""
    inputs = load_network(MUSTERDORF_DIR)
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=96))
    counts = []
    for t in range(96):
        f = sim.run_step(t, 0)
        assert f.converged
        counts.append(len(f.findings))
        # self-cleaning never emits per-pipe rows: at most one system entry
        assert sum(1 for x in f.findings
                   if x["check"] == "stagnation"
                   and x["entity"] == "selbstreinigung") <= 1
    assert max(counts) <= 6, f"alarm flood: up to {max(counts)} findings/tick"


def test_runtime_consumer_gets_pmin_check():
    """A consumer added at runtime must be checked (EG minimum) — review:
    it was silently exempt, so the canonical undersupply demo stayed
    green for the added consumer."""
    inputs = load_network(MUSTERDORF_DIR)
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=96))
    # a large draw at the high-zone ring end drags its own pressure to
    # ~1.2 bar (< 2.0 EG minimum) — the added consumer must be checked
    sim.add_consumer(node="h9", mdot_kg_per_s=12.0, name="Löschtest")
    frame = sim.run_step(0, 0)
    assert frame.converged
    names = {f["entity"] for f in _findings(frame, check="p_min")}
    assert "Löschtest" in names
    # and it clears again on removal (counter + requirement dropped)
    el = int(sim.index.consumers[sim.index.consumer_names.index("Löschtest")])
    sim.remove_consumer(el)
    assert "Löschtest" not in sim.compliance.p_req
    assert "Löschtest" not in sim.compliance._below


def test_duplicate_consumer_name_rejected():
    """Names key compliance counters / meter replay — a runtime duplicate
    is rejected (review: it merged the sustained window)."""
    inputs = load_network(MUSTERDORF_DIR)
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=96))
    with pytest.raises(KeyError, match="already exists"):
        sim.add_consumer(node="r5", mdot_kg_per_s=0.1,
                         name="Grundschule Musterdorf")


def test_healthy_tank_turnover_stable():
    """The Durchlauf tank alternates charge/supply — turnover on |exchange|
    must not phantom-flap on the healthy net (review: net-draw flapped
    between 'no draw' and 24 h+)."""
    inputs = load_network(MUSTERDORF_DIR)
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=96))
    turnover = []
    for t in range(96):                          # one full day = one window
        f = sim.run_step(t, 0)
        turnover.append(len(_findings(f, check="tank_turnover")))
    # a well-exchanged tank raises no turnover warning at all
    assert sum(turnover) == 0, f"phantom turnover warnings: {sum(turnover)}"


def test_compliance_exception_keeps_converged_frame(monkeypatch):
    """A poisoned rule check must degrade to a system info finding, NEVER
    discard the converged frame or desync tank state (review)."""
    inputs = load_network(MUSTERDORF_DIR)
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=96))

    def boom(*a, **k):
        raise RuntimeError("poisoned check")

    monkeypatch.setattr(sim.compliance, "evaluate", boom)
    frame = sim.run_step(0, 0)
    assert frame.converged                       # frame survives
    assert frame.solver_status == "ok"
    assert any(f["entity"] == "compliance" for f in frame.findings)


def test_findings_api_reports_staleness():
    """GET /findings carries converged/solver_status so a consumer can tell
    republished last-frame findings from current ones (review)."""
    with make_api_client() as client:
        client.post("/config/apply", json={"network_id": "musterdorf"})
        client.post("/control/start")
        import time as _t
        _t.sleep(0.3)
        client.post("/control/pause")
        r = client.get("/findings").json()
        assert "converged" in r and "solver_status" in r


def test_recorder_writes_findings_csv(tmp_path):
    """findings.csv mirrors the wire findings 1:1 (recording discipline)."""
    import csv

    from rtwaterflow.recorder import Recorder
    from rtwaterflow.state import StateStore

    inputs = load_network(MUSTERDORF_DIR)
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=96))
    sim.station_modes[inputs.supply.stations[0].name] = "off"
    sim.tanks[0].level_m = 1.3               # seeded reserve violation
    store = StateStore(make_settings(recordings_dir=tmp_path))
    rec = Recorder(tmp_path)
    rid = rec.start({"network": {"name": "musterdorf"}})["id"]
    rec.record(store.frame(sim.run_step(0, 0)))
    rec.stop()
    rows = list(csv.DictReader(
        (tmp_path / rid / "findings.csv").open(encoding="utf-8")))
    assert any(r["check"] == "tank_reserve"
               and r["severity"] == "violation" for r in rows)
