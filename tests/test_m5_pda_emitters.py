"""M5 acceptance: pressure-driven demand + emitters + scenario hooks.

Roadmap M5 bars: PDA is a no-op on a healthy net; an undersupplied net
STARVES gracefully (delivered < demanded, no negative pressure below
p_min in the reported delivered state, solver stable); a fire hydrant
delivers ~its target and the < 1.5 bar rule fires when it cannot;
background leakage raises the night minimum flow (MNF) and pressure
management cuts it back.
"""
from __future__ import annotations

import pytest

from conftest import REPO_ROOT, make_api_client, make_settings
from test_data_contract import _rebuild

from rtwaterflow.data_loader import load_network
from rtwaterflow.hydraulics.emitters import EmitterController
from rtwaterflow.simulator import Simulator

MUSTERDORF_DIR = REPO_ROOT / "data" / "networks" / "musterdorf"
SPD = 96


def _sim(**over):
    inputs = load_network(MUSTERDORF_DIR)
    return Simulator(inputs, make_settings(autostart=False, steps_per_day=SPD,
                                           **over))


# --- PDA --------------------------------------------------------------------

def test_pda_noop_on_healthy_net():
    """Healthy net: every consumer at p â‰¥ p_req, so delivery == demand and
    the balance closes exactly (PDA never bites)."""
    sim = _sim()
    f = sim.run_step(0, 0)
    assert f.converged and f.solver_status == "ok"
    assert f.summary["mdot_deficit_kg_per_s"] == 0.0
    assert all(abs(c["mdot_demand_kg_per_s"] - c["mdot_kg_per_s"]) < 1e-6
               for c in f.consumers)
    assert abs(f.summary["balance_err_kg_per_s"]) < 1e-3


def test_pda_starves_gracefully_no_negative_pressure():
    """A demand the network cannot meet â†’ partial delivery, deficit > 0,
    the solve stays converged, and NO consumer pressure below p_min
    (0.5 bar) in the reported state (the whole point of PDA over fixed
    demand). The 14 kg/s draw settles at a consistent partial-supply
    equilibrium (~1.5 bar)."""
    sim = _sim()
    sim.add_consumer(node="h9", mdot_kg_per_s=14.0, name="Grossbezug")
    f = sim.run_step(0, 0)
    assert f.converged and f.solver_status == "ok"
    gb = next(c for c in f.consumers if c["name"] == "Grossbezug")
    assert gb["mdot_kg_per_s"] < gb["mdot_demand_kg_per_s"]      # throttled
    assert f.summary["mdot_deficit_kg_per_s"] > 1.0
    assert min(c["p_bar"] for c in f.consumers) >= 0.5 - 1e-6    # no dry-neg
    # delivery is Wagner-CONSISTENT with the reported pressure
    factor = gb["mdot_kg_per_s"] / gb["mdot_demand_kg_per_s"]
    assert factor == pytest.approx(sim.pda.factor(gb["p_bar"], 2.0), abs=0.05)


def test_pda_off_shows_negative_pressure_contrast():
    """PDA OFF (the M0 contrast): the same infeasible demand produces the
    impossible negative pressure â€” the teaching motivation for PDA."""
    sim = _sim(pda_enabled=False)
    sim.add_consumer(node="h9", mdot_kg_per_s=20.0, name="Grossbezug")
    f = sim.run_step(0, 0)
    assert f.converged
    assert min(c["p_bar"] for c in f.consumers) < 0.0            # negative!
    # delivery is the full fixed demand (not throttled)
    gb = next(c for c in f.consumers if c["name"] == "Grossbezug")
    assert gb["mdot_kg_per_s"] == pytest.approx(20.0, abs=0.1)


# --- emitters: hydrant ------------------------------------------------------

def test_hydrant_delivers_and_balances():
    """An open hydrant delivers close to its target at a healthy node and
    its withdrawal balances into the feed."""
    sim = _sim()
    sim.run_step(0, 0)
    em = sim.open_hydrant(node="r5", target_m3_h=48.0, duration_ticks=8,
                          name="Hydrant r5")
    assert em["kind"] == "hydrant"
    f = sim.run_step(1, 0)
    live = f.emitters[0]
    assert live["m3_per_h"] == pytest.approx(48.0, rel=0.15)     # ~target
    assert f.summary["mdot_emitted_kg_per_s"] > 0
    assert abs(f.summary["balance_err_kg_per_s"]) < 1e-3


def test_hydrant_expires_after_duration():
    sim = _sim()
    sim.run_step(0, 0)          # abs_tick 0 — the hydrant's start
    sim.open_hydrant(node="r5", target_m3_h=48.0, duration_ticks=3,
                     name="H")  # expires at abs_tick 0 + 3 = 3
    assert len(sim.run_step(1, 0).emitters) == 1
    assert len(sim.run_step(2, 0).emitters) == 1
    assert len(sim.run_step(3, 0).emitters) == 0                 # expired


def test_hydrant_expires_across_day_boundary():
    """M5 review: the absolute (unwrapped) tick — a hydrant opened late on
    day 0 expires early on day 1, not never (the wrapped profile tick
    could never reach its expiry)."""
    sim = _sim()
    sim.run_step(SPD - 2, 0)     # abs_tick = spd-2, near end of day 0
    sim.open_hydrant(node="r5", target_m3_h=48.0, duration_ticks=4,
                     name="H")   # expires at (spd-2)+4 = spd+2 (early day 1)
    assert len(sim.run_step(SPD - 1, 0).emitters) == 1   # abs spd-1 < spd+2
    assert len(sim.run_step(0, 1).emitters) == 1         # abs spd   < spd+2
    assert len(sim.run_step(1, 1).emitters) == 1         # abs spd+1 < spd+2
    assert len(sim.run_step(2, 1).emitters) == 0         # abs spd+2 == expiry


def test_fire_flow_below_1_5_bar_flags_w405():
    """A fire draw the network cannot support drops the hydrant node below
    1.5 bar â†’ W 405 fire_flow violation (the M4 catalog's deferred check)."""
    sim = _sim()
    sim.run_step(0, 0)
    # a large fire flow at the high-zone end drags the node under 1.5 bar
    sim.open_hydrant(node="h9", target_m3_h=192.0, duration_ticks=8,
                     name="Grossbrand")
    f = sim.run_step(1, 0)
    fire = [x for x in f.findings if x["check"] == "fire_flow"]
    assert fire and all("W 405" in x["rule"] for x in fire)
    ph9 = next(j["p_bar"] for j in f.junctions if j["name"] == "h9")
    assert ph9 < 1.5


# --- emitters: burst --------------------------------------------------------

def test_burst_craters_pressure_and_spikes_feed():
    sim = _sim()
    base = sim.run_step(0, 0)
    base_feed = base.summary["mdot_feed_kg_per_s"]
    sim.place_burst(node="r5", area_m2=0.01, name="Rohrbruch r5")
    f = sim.run_step(1, 0)
    pr5 = next(j["p_bar"] for j in f.junctions if j["name"] == "r5")
    base_pr5 = next(j["p_bar"] for j in base.junctions if j["name"] == "r5")
    assert pr5 < base_pr5                                # local crater
    assert f.summary["mdot_feed_kg_per_s"] > base_feed   # feed spike
    assert f.emitters[0]["kind"] == "burst"


def test_negative_pressure_never_reported_ok():
    """M5 review (headline): a big burst can crater OTHER junctions below
    zero while the PDA/emitter fixed point is self-consistent. Negative
    gauge pressure is unphysical, so that frame must NEVER be reported
    'ok' — it degrades honestly (converged, but flagged)."""
    sim = _sim()
    sim.run_step(0, 0)
    # 0.002 m2 (~5 cm hole) settles to a SELF-CONSISTENT state whose
    # surrounding junctions are negative — the exact case the naive gap
    # check reported "ok"; the validity guard now downgrades it
    sim.place_burst(node="r5", area_m2=0.002, name="Grossbruch")
    f = sim.run_step(1, 0)
    assert f.converged                               # never a 500
    assert f.summary["p_min_bar"] < 0.0              # the crater is real
    assert f.solver_status == "degraded"             # and honestly flagged
    assert "physically invalid" in (f.error or "")


def test_hydrant_capped_at_target_flow():
    """A hydrant nozzle cannot pull MORE than its rated target even at high
    pressure (M5 review: without the cap C·p^0.5 overshoots)."""
    sim = _sim()
    sim.run_step(0, 0)
    # r5 sits ~6 bar; a small target would overshoot without the cap
    sim.open_hydrant(node="r5", target_m3_h=20.0, name="Klein")
    f = sim.run_step(1, 0)
    assert f.emitters[0]["m3_per_h"] <= 20.0 + 1e-6


# --- emitters: leakage / MNF ------------------------------------------------

def test_leakage_raises_night_minimum_flow():
    """Background leakage is a real pressure-dependent loss; the emitted
    total is the network's leak rate (the MNF component). Halving the
    coefficient (repair / pressure management) measurably cuts it â€” the
    teachable "pressure management reduces losses"."""
    sim = _sim()
    # a night tick (low demand ~02:00 = tick 8 at spd 96)
    base = sim.run_step(8, 0).summary
    assert base["mdot_emitted_kg_per_s"] == 0.0          # no leaks yet
    info = sim.set_leakage(coefficient_per_km=0.05)
    assert info["leaks"] > 10
    # leaks attach to network nodes only — never the head sources (M5
    # review: add_consumer forbids the same)
    head = {m["node"] for m in sim.index.producer_meta
            if m["kind"] in ("slack", "tank")}
    leak_nodes = {e.node for e in sim.emitters.emitters.values()
                  if e.kind == "leak"}
    assert not (leak_nodes & head)
    leaky = sim.run_step(8, 0).summary
    assert leaky["mdot_emitted_kg_per_s"] > 1.0          # leak rate visible
    assert abs(leaky["balance_err_kg_per_s"]) < 1e-3     # still balances
    # repair half the leakage â†’ the loss drops proportionally
    sim.set_leakage(coefficient_per_km=0.025)
    repaired = sim.run_step(8, 0).summary["mdot_emitted_kg_per_s"]
    assert repaired < 0.6 * leaky["mdot_emitted_kg_per_s"]


# --- lifecycle / wire -------------------------------------------------------

def test_reset_operations_clears_emitters_and_leak():
    sim = _sim()
    sim.run_step(0, 0)
    sim.open_hydrant(node="r5", target_m3_h=48.0, name="H")
    sim.set_leakage(coefficient_per_km=0.05)
    sim.reset_operations()
    assert not sim.emitters.emitters
    assert sim.leak_coefficient_per_km == 0.0


def test_emitters_survive_strict_mode():
    """Emitters are equipment SCADA (an operator sees an open hydrant) â€”
    on the wire in strict mode, while the truth layer is stripped."""
    from rtwaterflow.state import StateStore

    sim = _sim()
    sim.run_step(0, 0)
    sim.open_hydrant(node="r5", target_m3_h=48.0, name="H")
    sim.set_leakage(coefficient_per_km=0.05)
    store = StateStore(make_settings(expose_ground_truth=False))
    frame = store.frame(sim.run_step(1, 0))
    assert "junctions" not in frame          # truth stripped
    kinds = {e["kind"] for e in frame["emitters"]}
    assert kinds == {"hydrant"}              # hydrant stays, leaks hidden

    # full mode: the leaks ARE visible
    store2 = StateStore(make_settings(expose_ground_truth=True))
    full = store2.frame(sim.run_step(2, 0))
    assert "leak" in {e["kind"] for e in full["emitters"]}


# --- API + scenario ---------------------------------------------------------

def test_emitter_and_pda_api():
    with make_api_client() as client:
        client.post("/config/apply", json={"network_id": "musterdorf"})
        client.post("/control/start")
        import time as _t
        _t.sleep(0.2)

        r = client.post("/hydrant", json={"node": "r5", "target_m3_h": 48.0,
                                          "duration_minutes": 120})
        assert r.status_code == 200 and r.json()["kind"] == "hydrant"
        assert len(client.get("/emitters").json()["emitters"]) == 1

        assert client.post("/hydrant", json={"node": "nope",
                                             "target_m3_h": 48.0}
                           ).status_code == 400
        assert client.post("/leakage",
                           json={"coefficient_per_km": 0.05}).json()["leaks"] > 0
        assert client.delete("/leakage").json()["cleared"] > 0

        assert client.post("/pda", json={"enabled": False}
                           ).json()["pda_enabled"] is False
        assert client.get("/pda").json()["pda_enabled"] is False

        name = client.get("/emitters").json()["emitters"][0]["name"]
        assert client.delete(
            f"/emitter/{name}").status_code == 200
        assert client.delete("/emitter/ghost").status_code == 404


def test_scenario_persists_hydraulics():
    """A fire scenario (hydrant + leakage + PDA off) round-trips through
    scenario save/load."""
    with make_api_client() as client:
        client.post("/config/apply", json={"network_id": "musterdorf"})
        client.post("/control/start")
        import time as _t
        _t.sleep(0.2)
        client.post("/hydrant", json={"node": "r5", "target_m3_h": 96.0})
        client.post("/leakage", json={"coefficient_per_km": 0.04})
        client.post("/pda", json={"enabled": False})
        sid = client.post("/scenarios", json={"name": "brandprobe"}).json()["id"]

        # disturb, then reload
        client.delete("/leakage")
        client.post("/pda", json={"enabled": True})
        r = client.post(f"/scenarios/{sid}/load")
        assert r.status_code == 200
        em = client.get("/emitters").json()
        assert em["pda_enabled"] is False
        kinds = {e["kind"] for e in em["emitters"]}
        assert "hydrant" in kinds and "leak" in kinds
        client.delete(f"/scenarios/{sid}")


def test_recorder_writes_emitters_csv(tmp_path):
    import csv

    from rtwaterflow.recorder import Recorder
    from rtwaterflow.state import StateStore

    sim = _sim()
    sim.run_step(0, 0)
    sim.open_hydrant(node="r5", target_m3_h=48.0, name="H")
    store = StateStore(make_settings(recordings_dir=tmp_path))
    rec = Recorder(tmp_path)
    rid = rec.start({"network": {"name": "musterdorf"}})["id"]
    rec.record(store.frame(sim.run_step(1, 0)))
    rec.stop()
    rows = list(csv.DictReader(
        (tmp_path / rid / "emitters.csv").open(encoding="utf-8")))
    assert any(r["kind"] == "hydrant" and r["name"] == "H" for r in rows)
