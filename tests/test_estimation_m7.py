"""Estimation layer — the forward-simulation observer (roadmap §4.10, M7).

The distinctive estimation test patterns, water domain:

* **Honesty tripwires** — the estimate must not contain information no sensor
  could deliver: an anomaly on an UNMETERED consumer stays invisible (its
  estimated flow remains the prior), a **burst at an unmetered node** does not
  perturb the estimate (every emitter withdrawal is zeroed in the twin), while
  the SAME anomaly on a METERED consumer propagates; with the ``clear`` preset
  the estimated per-consumer flows equal the priors exactly.
* **Estimate tracks reality where measured** — with full coverage the twin is
  pinned by the measured boundary everywhere and the error metric is ~0; the
  reconstruction error rises as coverage shrinks (visible degradation).
* **Throttling / stale attachment** — wall-clock self-throttle plus the
  standard-mode metering raster; the last estimate rides subsequent frames.
* **Strict mode** — ``estimated`` stays visible (it is derived from
  measurements) while the truth keys are stripped.
"""
from __future__ import annotations

import numpy as np
import pytest

from conftest import REPO_ROOT, make_api_client, make_settings, wait_for

from rtwaterflow.data_loader import load_network
from rtwaterflow.estimator import EstimationConfig
from rtwaterflow.simulator import Simulator

MUSTERDORF_DIR = REPO_ROOT / "data" / "networks" / "musterdorf"
HILLSIDE_DIR = REPO_ROOT / "data" / "networks" / "tutorial_hillside"

SPD = 96
TICK = 32          # 08:00 at 96 steps/day — archetype draws active


@pytest.fixture(scope="module")
def musterdorf_inputs():
    return load_network(MUSTERDORF_DIR)


@pytest.fixture(scope="module")
def hillside_inputs():
    return load_network(HILLSIDE_DIR)


def make_sim(inputs, **cfg) -> Simulator:
    sim = Simulator(inputs, make_settings(steps_per_day=SPD))
    cfg.setdefault("throttle_factor", 0.0)   # tests refresh every tick
    sim.set_est_config(EstimationConfig(**cfg))
    return sim


def est_consumer(est: dict, name: str) -> dict:
    return next(c for c in est["consumers"] if c["name"] == name)


def _one(inputs, preset: str, meters=None, node_sensors=None, step=TICK):
    sim = make_sim(inputs)
    sim.measurements.apply_preset(preset)
    for el in meters or []:
        sim.measurements.add_consumer_meter(int(el))
    for n in node_sensors or []:
        sim.measurements.add_node_sensor(n)
    r = sim.run_step(step, 0)
    assert r.converged and r.estimated is not None
    return sim, r, r.estimated


# ---------------------------------------------------------------------------
# payload shape & basic mechanics
# ---------------------------------------------------------------------------

def test_estimated_mirrors_the_truth_shape(musterdorf_inputs):
    sim = make_sim(musterdorf_inputs)
    r = sim.run_step(TICK, 0)
    assert r.converged
    est = r.estimated
    assert est is not None
    for key in ("junctions", "pipes", "consumers", "summary"):
        assert key in est
    assert [c["id"] for c in est["consumers"]] == [c["id"] for c in r.consumers]
    assert len(est["junctions"]) == len(r.junctions)
    assert len(est["pipes"]) == len(r.pipes)
    assert set(est["summary"]) == set(r.summary)
    # bookkeeping: which step it estimated + telegram id
    assert est["step"] == TICK and est["day"] == 0 and est["seq"] == 1
    assert est["solve_ms"] > 0 and est["solver_status"] in ("ok", "degraded")
    # error = deviation at sensored points (water channels)
    err = est["error"]
    assert set(err) == {"max_dmdot_kg_per_s", "mean_dmdot_kg_per_s",
                        "max_dp_bar", "mean_dp_bar", "n_points"}
    assert err["n_points"] > 0


def test_estimation_disabled_yields_no_estimate(musterdorf_inputs):
    sim = make_sim(musterdorf_inputs, enabled=False)
    r = sim.run_step(TICK, 0)
    assert r.converged and r.estimated is None


# ---------------------------------------------------------------------------
# acceptance: the estimate tracks reality where measured (roadmap §4.10)
# ---------------------------------------------------------------------------

def test_full_coverage_estimate_matches_truth(musterdorf_inputs):
    """all_consumers + full fidelity = full observability: the twin is pinned
    by the measured boundary everywhere — near-exact reconstruction, and the
    innovation at the sensored points is ~0 (the M7 acceptance)."""
    _, r, est = _one(musterdorf_inputs, "all_consumers")
    s, e = r.summary, est["summary"]
    assert abs(e["mdot_feed_kg_per_s"] - s["mdot_feed_kg_per_s"]) < 1e-2
    assert abs(e["mdot_demand_kg_per_s"] - s["mdot_demand_kg_per_s"]) < 1e-3
    err = est["error"]
    assert err["max_dmdot_kg_per_s"] < 1e-3
    assert err["max_dp_bar"] < 1e-2


def test_reconstruction_degrades_as_coverage_shrinks(musterdorf_inputs):
    """The estimate reconstructs the truth well where metered and falls back
    to priors elsewhere — so the reconstruction error over ALL consumers
    grows monotonically as sensor coverage shrinks (visible degradation)."""
    idx = make_sim(musterdorf_inputs).index
    half = [int(c) for c in idx.consumers[:13]]

    def recon_error(r, est):
        tr = {c["id"]: c["mdot_kg_per_s"] for c in r.consumers}
        return sum(abs(c["mdot_kg_per_s"] - tr[c["id"]])
                   for c in est["consumers"])

    _, r_full, est_full = _one(musterdorf_inputs, "all_consumers")
    _, r_half, est_half = _one(musterdorf_inputs, "clear", meters=half)
    _, r_clear, est_clear = _one(musterdorf_inputs, "clear")

    full = recon_error(r_full, est_full)
    half_cov = recon_error(r_half, est_half)
    clear = recon_error(r_clear, est_clear)
    assert full < 1e-3            # metered everywhere → exact
    assert half_cov > full        # unmetered half falls back to priors
    assert clear > half_cov       # nothing metered → all priors


# ---------------------------------------------------------------------------
# honesty tripwires (roadmap §4.10 design rails)
# ---------------------------------------------------------------------------

def test_tripwire_unmetered_burst_stays_invisible(musterdorf_inputs):
    """A burst at an UNMETERED node is visible in the truth (a real
    withdrawal) but NOT in the estimate — the twin zeros every emitter, so
    the WHOLE estimate is byte-for-byte the no-burst estimate."""
    node = make_sim(musterdorf_inputs).index.consumer_nodes[0]

    _, r0, est0 = _one(musterdorf_inputs, "clear")          # baseline
    p_est0 = {j["name"]: j["p_bar"] for j in est0["junctions"]}

    sim = make_sim(musterdorf_inputs)
    sim.measurements.apply_preset("clear")                  # nothing metered
    sim.emitters.add(name="Rohrbruch", node=node, kind="burst",
                     coefficient=0.5, exponent=0.5, start_tick=0,
                     duration_ticks=None)
    r = sim.run_step(TICK, 0)
    assert r.converged
    burst = next(e for e in r.emitters if e["name"] == "Rohrbruch")
    assert burst["m3_per_h"] > 1.0                          # a real burst
    p_truth = next(j["p_bar"] for j in r.junctions if j["name"] == node)
    p_est = next(j["p_bar"] for j in r.estimated["junctions"]
                 if j["name"] == node)
    # the burst dropped the TRUE pressure at that node ...
    assert p_truth < p_est - 0.02
    # ... but the WHOLE estimate is unchanged — every junction pressure AND
    # the feed match the no-burst run (the hidden burst leaks nowhere)
    for j in r.estimated["junctions"]:
        assert abs(j["p_bar"] - p_est0[j["name"]]) < 1e-3
    assert abs(r.estimated["summary"]["mdot_feed_kg_per_s"]
               - est0["summary"]["mdot_feed_kg_per_s"]) < 1e-3


def test_tripwire_unmetered_burst_exact_on_fixed_boundary(hillside_inputs):
    """On the fixed-ext_grid hillside net (no tank buffering, no station,
    legacy demand = its own prior) the invariant is EXACT: a burst at an
    unmetered node moves the estimate by exactly zero at every junction over
    multiple ticks."""
    base = make_sim(hillside_inputs)
    base.measurements.apply_preset("clear")
    perturbed = make_sim(hillside_inputs)
    perturbed.measurements.apply_preset("clear")
    node = perturbed.index.consumer_nodes[0]
    perturbed.emitters.add(name="Rohrbruch", node=node, kind="burst",
                           coefficient=0.4, exponent=0.5, start_tick=0,
                           duration_ticks=None)
    for step in range(3):
        rb = base.run_step(step, 0)
        rp = perturbed.run_step(step, 0)
        eb = {j["name"]: j["p_bar"] for j in rb.estimated["junctions"]}
        # truth diverges (the burst is real) but the estimate is identical
        assert rp.summary["mdot_emitted_kg_per_s"] > 0.0
        for j in rp.estimated["junctions"]:
            assert abs(j["p_bar"] - eb[j["name"]]) < 1e-9


def test_tripwire_a_sensor_at_the_burst_reveals_it(musterdorf_inputs):
    """The complement: a pressure logger AT the burst node makes the burst
    show up as innovation (the operator now measures the low pressure the
    prior-driven twin does not predict)."""
    node = make_sim(musterdorf_inputs).index.consumer_nodes[0]
    sim = make_sim(musterdorf_inputs)
    sim.measurements.apply_preset("clear")
    sim.measurements.add_node_sensor(node)
    sim.emitters.add(name="Rohrbruch", node=node, kind="burst",
                     coefficient=0.5, exponent=0.5, start_tick=0,
                     duration_ticks=None)
    r = sim.run_step(TICK, 0)
    assert r.estimated["error"]["max_dp_bar"] > 0.01


def test_tripwire_unmetered_consumer_anomaly_invisible(musterdorf_inputs):
    """A doubled demand on an UNMETERED consumer is in the truth but not the
    estimate — the estimated flow stays at the prior."""
    sim = make_sim(musterdorf_inputs)
    sim.measurements.apply_preset("clear")
    sim.measurements.add_consumer_meter(int(sim.index.consumers[1]))  # meter B
    name = sim.index.consumer_names[0]                                # A unmet.
    sim.profiles.mdot_kg_per_s[0, :] *= 3.0                           # anomaly
    r = sim.run_step(TICK, 0)
    assert r.converged
    truth_a = next(c for c in r.consumers if c["name"] == name)
    est_a = est_consumer(r.estimated, name)
    prior = sim._observer.book.demand_at(sim._tick(TICK, 0))[0]
    assert truth_a["mdot_kg_per_s"] > 1.5 * prior     # anomaly in the truth
    assert abs(est_a["mdot_kg_per_s"] - prior) < 1e-3  # ... prior in the est


def test_tripwire_metered_anomaly_propagates(musterdorf_inputs):
    """The SAME anomaly on a METERED consumer appears in the estimate — the
    meter delivers it."""
    sim = make_sim(musterdorf_inputs)
    sim.measurements.apply_preset("clear")
    sim.measurements.add_consumer_meter(int(sim.index.consumers[0]))  # meter A
    name = sim.index.consumer_names[0]
    prior = None
    sim.profiles.mdot_kg_per_s[0, :] *= 3.0
    r = sim.run_step(TICK, 0)
    prior = sim._observer.book.demand_at(sim._tick(TICK, 0))[0]
    est_a = est_consumer(r.estimated, name)
    truth_a = next(c for c in r.consumers if c["name"] == name)
    assert est_a["mdot_kg_per_s"] > 1.5 * prior       # measured anomaly rides
    assert abs(est_a["mdot_kg_per_s"] - truth_a["mdot_kg_per_s"]) < 1e-2


def test_clear_preset_estimate_equals_priors(musterdorf_inputs):
    """With ``clear`` (source SCADA only) the estimated per-consumer flows
    equal the priors exactly — the twin is *driven* by them — and the priors
    are NOT the per-tick truth (stochastic archetype noise)."""
    sim, r, est = _one(musterdorf_inputs, "clear")
    prior = sim._observer.book.demand_at(sim._tick(TICK, 0))
    for i, c in enumerate(est["consumers"]):
        assert abs(c["mdot_kg_per_s"] - prior[i]) < 1e-3
    truth_q = np.array([c["mdot_kg_per_s"] for c in r.consumers])
    est_q = np.array([c["mdot_kg_per_s"] for c in est["consumers"]])
    assert not np.allclose(truth_q, est_q, atol=1e-3)   # noise ≠ expectation


# ---------------------------------------------------------------------------
# throttling, raster, stale attachment
# ---------------------------------------------------------------------------

def test_wall_clock_throttle_attaches_stale_estimate(hillside_inputs):
    sim = make_sim(hillside_inputs, throttle_factor=1000.0)
    r0 = sim.run_step(0, 0)
    assert r0.estimated["seq"] == 1 and r0.estimated["step"] == 0
    r1 = sim.run_step(1, 0)
    # throttled: the LAST estimate rides along, honestly stamped step 0
    assert r1.estimated["seq"] == 1 and r1.estimated["step"] == 0
    sim.set_est_config(EstimationConfig(throttle_factor=0.0))
    r2 = sim.run_step(2, 0)
    assert r2.estimated["seq"] == 1 and r2.estimated["step"] == 2


def test_standard_mode_estimates_on_the_metering_raster(hillside_inputs):
    """Standard-mode devices publish only at window boundaries — no new
    information between them, so the observer refreshes on the same raster."""
    sim = Simulator(hillside_inputs, make_settings(steps_per_day=1440))
    sim.set_est_config(EstimationConfig(throttle_factor=0.0))
    sim.measurements.set_mode("standard")
    assert sim.measurements.window_steps == 15
    seqs = []
    for t in range(17):
        r = sim.run_step(t, 0)
        seqs.append(r.estimated["seq"] if r.estimated else None)
    assert seqs[0] == 1
    assert all(s == 1 for s in seqs[1:15])
    assert seqs[15] == 2 and seqs[16] == 2


def test_estimation_failure_is_data(hillside_inputs, monkeypatch):
    """A broken twin never crashes the engine: the estimate goes stale."""
    sim = make_sim(hillside_inputs)
    r0 = sim.run_step(0, 0)
    assert r0.estimated["seq"] == 1
    obs = sim._observer

    def boom(*a, **k):
        raise RuntimeError("twin sabotage")

    monkeypatch.setattr(obs, "_apply", boom)
    r1 = sim.run_step(1, 0)
    assert r1.converged                        # the truth loop is untouched
    assert r1.estimated["seq"] == 1            # stale attachment, no crash
    assert r1.estimated["step"] == 0


def test_non_convergent_twin_stays_stale(hillside_inputs, monkeypatch):
    """The DISTINCT non-exception path: the twin SOLVE returns non-converged
    (not an exception). _estimate returns None, so the last estimate rides
    stale with an unchanged seq — the truth loop is untouched."""
    import rtwaterflow.simulator as simmod
    from rtwaterflow.simulator import SolveOutcome

    sim = make_sim(hillside_inputs)
    r0 = sim.run_step(0, 0)
    assert r0.estimated["seq"] == 1
    twin = sim._observer.twin
    real = simmod.solve_hydraulic

    def only_twin_fails(net, *a, **k):
        if net is twin:                       # truth net still solves normally
            return SolveOutcome(False, "failed", 0, 0.0, error="twin diverged")
        return real(net, *a, **k)

    monkeypatch.setattr(simmod, "solve_hydraulic", only_twin_fails)
    r1 = sim.run_step(1, 0)
    assert r1.converged                        # truth loop untouched
    assert r1.estimated["seq"] == 1            # non-converged twin → stale
    assert r1.estimated["step"] == 0


def test_estimation_across_day_boundary(musterdorf_inputs):
    """Estimation on day > 0: the global tick wraps but the estimate stamps
    the true day/step and refreshes on the raster as on day 0."""
    sim = make_sim(musterdorf_inputs)
    r = sim.run_step(TICK, 3)
    assert r.converged and r.estimated is not None
    assert r.estimated["day"] == 3 and r.estimated["step"] == TICK
    assert r.estimated["seq"] >= 1


def test_topology_crud_rebuilds_the_twin(hillside_inputs):
    sim = make_sim(hillside_inputs)
    r0 = sim.run_step(0, 0)
    n0 = len(r0.estimated["consumers"])
    sim.add_consumer(node="j3", mdot_kg_per_s=0.03, name="Neubau")
    r1 = sim.run_step(1, 0)
    assert r1.converged
    names = [c["name"] for c in r1.estimated["consumers"]]
    assert len(names) == n0 + 1 and "Neubau" in names   # twin followed
    nb = est_consumer(r1.estimated, "Neubau")
    assert abs(nb["mdot_kg_per_s"] - 0.03) < 1e-3        # prior = configured


# ---------------------------------------------------------------------------
# presets + API + strict mode
# ---------------------------------------------------------------------------

def test_scada_preset_places_pressure_loggers(musterdorf_inputs):
    """The SCADA-realistic preset: pressure loggers at the source + net ends,
    NO household meters (roadmap §4.10)."""
    sim = make_sim(musterdorf_inputs)
    sim.run_step(0, 0)                          # establish context
    sim.measurements.apply_preset("scada")
    ms = sim.measurements
    assert ms.preset == "scada"
    assert not ms.consumer_meters               # no household metering
    assert sim.index.ext_grid_node in ms.node_sensors
    assert len(ms.node_sensors) > 1             # source + at least one end


def test_estimation_config_api_and_strict_mode():
    with make_api_client(expose_ground_truth=False) as client:
        cfg = client.get("/estimation/config").json()
        assert cfg["enabled"] is True
        assert cfg["prior_basis"] == "archetype"
        assert cfg["throttle_factor"] == 2.0

        # 422 discipline on the knobs
        assert client.post("/estimation/config",
                           json={"throttle_factor": -1}).status_code == 422
        assert client.post("/estimation/config",
                           json={"prior_basis": "magic"}).status_code == 422

        out = client.post("/estimation/config",
                          json={"prior_basis": "design",
                                "throttle_factor": 0.0}).json()
        assert out["prior_basis"] == "design" and out["enabled"] is True

        client.post("/control/start")
        frame = wait_for(lambda: (
            (f := client.get("/state").json()) and f.get("estimated") and f))
        # strict mode: truth keys stripped, estimated VISIBLE (derived from
        # measurements) incl. its error internals
        for key in ("junctions", "pipes", "consumers", "summary"):
            assert key not in frame
        est = frame["estimated"]
        for key in ("junctions", "pipes", "consumers", "summary", "error"):
            assert key in est
        assert est["error"]["n_points"] > 0
        client.post("/control/pause")

        # disable → estimated disappears from fresh frames
        client.post("/estimation/config", json={"enabled": False})
        client.post("/control/resume")
        wait_for(lambda: client.get("/state").json().get("estimated") is None)
        client.post("/control/pause")


def test_estimation_config_survives_network_swap():
    with make_api_client() as client:
        client.post("/estimation/config", json={"prior_basis": "design"})
        r = client.post("/config/apply", json={"network_id": "musterdorf"})
        assert r.status_code == 200
        cfg = client.get("/estimation/config").json()
        assert cfg["prior_basis"] == "design"   # engine-held policy survived


def test_estimation_policy_survives_scenario_round_trip(tmp_path):
    """The estimation policy is an operator setting — save it into a recipe,
    reset it, load the recipe, and it comes back (M7 review gap)."""
    with make_api_client(scenarios_dir=tmp_path) as client:
        client.post("/estimation/config",
                    json={"prior_basis": "design", "enabled": False})
        sid = client.post("/scenarios",
                          json={"name": "Est Policy"}).json()["id"]
        # reset to the defaults ...
        client.post("/estimation/config",
                    json={"prior_basis": "archetype", "enabled": True})
        # ... then load the recipe back
        assert client.post(f"/scenarios/{sid}/load").status_code == 200
        cfg = client.get("/estimation/config").json()
        assert cfg["prior_basis"] == "design" and cfg["enabled"] is False
