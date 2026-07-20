"""Known-answer test — the hillside elevation fixture through the full
platform path (loader → builder → simulator → wire payload).

Pinned values re-derived on this machine 2026-07-20 (pandapipes 0.14.0,
friction_model="colebrook"); the detailed per-junction pins live in
``test_tutorial_hillside.py`` — this file pins the PLATFORM behaviors around
them (wire shape, warm start, single-layer conventions).
"""
from __future__ import annotations

import pytest

from rtwaterflow.simulator import Simulator

from conftest import make_settings

REL = 0.005  # ~0.5 % relative tolerance


@pytest.fixture(scope="module")
def result_and_sim(hillside_inputs):
    sim = Simulator(hillside_inputs, make_settings())
    result = sim.run_step(0, 0)
    return result, sim


def test_converges_tier1(result_and_sim):
    result, _ = result_and_sim
    assert result.converged is True
    assert result.solver_status == "ok"
    assert result.error is None


def test_sink_setpoints(result_and_sim):
    _, sim = result_and_sim
    rs = sim.net.res_sink
    assert rs.mdot_kg_per_s.iloc[0] == pytest.approx(0.277, abs=1e-9)
    assert rs.mdot_kg_per_s.iloc[1] == pytest.approx(0.139, abs=1e-9)
    # ext_grid supplies the sum (withdrawal reported negative by pandapipes)
    eg = sim.net.res_ext_grid.mdot_kg_per_s.iloc[0]
    assert abs(eg) == pytest.approx(0.416, rel=REL)


def test_wire_payload_shape(result_and_sim):
    result, _ = result_and_sim
    # SINGLE layer: one junction per node, one pipe per entry — no x2
    # supply/return doubling (the fork parent's expansion is gone)
    assert len(result.junctions) == 5
    assert len(result.pipes) == 4
    assert len(result.consumers) == 2
    assert len(result.producers) == 1
    assert result.producers[0]["kind"] == "slack"
    assert result.producers[0]["p_bar"] == pytest.approx(0.5)
    # no thermal keys anywhere on the hydraulic wire
    for j in result.junctions:
        assert set(j) == {"id", "name", "p_bar"}
    for p in result.pipes:
        assert set(p) == {"id", "trench", "mdot_kg_per_s", "v_m_per_s",
                          "dp_bar"}
        assert p["trench"] == p["id"]  # single layer: trench id == pipe id
    for c in result.consumers:
        assert set(c) == {"id", "name", "node", "kind",
                          "mdot_demand_kg_per_s", "mdot_kg_per_s", "p_bar"}
    assert result.time_of_day == "00:00"
    # velocities are tiny on the tutorial net (max ~0.024 m/s)
    assert all(abs(p["v_m_per_s"]) < 0.05 for p in result.pipes)


def test_flow_direction_downhill(result_and_sim):
    """Pipe j1->j5 carries the full feed AGAINST its from->to orientation
    (the source sits at j5): mdot is negative — the tutorial's signature."""
    result, _ = result_and_sim
    feed_pipe = result.pipes[3]  # pipes.json order: j1->j5 is entry 4
    assert feed_pipe["mdot_kg_per_s"] == pytest.approx(-0.416, rel=REL)


def test_warm_start_second_step(result_and_sim):
    """Platform warm start (pn_bar only): the second step must converge at
    tier 1 and reproduce the state exactly (constant demand)."""
    result, sim = result_and_sim
    res2 = sim.run_step(1, 0)
    assert res2.converged and res2.solver_status == "ok"
    assert res2.summary["p_min_bar"] == pytest.approx(
        result.summary["p_min_bar"], rel=1e-6)
    assert res2.time_of_day == "00:01"


def test_reset_initialization_is_pn_bar_only(result_and_sim):
    """After failures/swaps the init reset restores BUILD-TIME pressures;
    there is no thermal state to reset in water mode."""
    _, sim = result_and_sim
    sim._reset_initialization()
    assert (sim.net.junction["pn_bar"].to_numpy()
            == sim.index.init_pn_bar).all()
