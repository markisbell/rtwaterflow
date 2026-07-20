"""Retry ladder — hydraulic tier semantics + never-500 discipline.

Water hydraulics converge far more readily than the fork parent's thermal
solves, so the ladder shrank to 3 tiers: colebrook (n iter) → colebrook
(3n) → nikuradse fallback reported as "degraded" (upstream issue #803 biases
low-Re friction — the vocabulary ok|degraded|failed survives).
"""
from __future__ import annotations

from rtwaterflow.simulator import SolveOutcome, retry_attempts, solve_with_retry


def test_ladder_tiers_and_solver_iter_semantics():
    """RTWATERFLOW_SOLVER_ITER sets base iter of tier 1; tiers 2/3 use 3x."""
    tiers = retry_attempts(50)
    assert [t["mode"] for t in tiers] == ["hydraulics"] * 3
    assert [t["iter"] for t in tiers] == [50, 150, 150]
    assert [t["friction_model"] for t in tiers] == [
        "colebrook", "colebrook", "nikuradse"]
    # nikuradse must never be the primary model (issue #803)
    assert tiers[0]["friction_model"] == "colebrook"


def test_hillside_tier1(hillside_inputs):
    from rtwaterflow.network_builder import build_network
    net, _ = build_network(hillside_inputs)
    outcome = solve_with_retry(net, iter_base=100)
    assert isinstance(outcome, SolveOutcome)
    assert outcome.converged is True
    assert outcome.status == "ok"
    assert outcome.tier == 1
    assert net.converged


def test_nikuradse_tier_reports_degraded(hillside_inputs, monkeypatch):
    """When the colebrook tiers fail, the nikuradse fallback still converges
    but the outcome is honestly 'degraded' (friction bias, issue #803)."""
    import rtwaterflow.simulator as sim_module
    from rtwaterflow.network_builder import build_network

    net, _ = build_network(hillside_inputs)
    real_pipeflow = sim_module.pipeflow
    calls = {"n": 0}

    def flaky(net_, **kwargs):
        calls["n"] += 1
        if kwargs.get("friction_model") == "colebrook":
            from pandapipes.pf.pipeflow_setup import PipeflowNotConverged
            raise PipeflowNotConverged("poisoned colebrook tier")
        return real_pipeflow(net_, **kwargs)

    monkeypatch.setattr(sim_module, "pipeflow", flaky)
    outcome = sim_module.solve_with_retry(net, iter_base=50)
    assert outcome.converged is True
    assert outcome.status == "degraded"
    assert outcome.tier == 3
    assert "nikuradse" in (outcome.error or "")
