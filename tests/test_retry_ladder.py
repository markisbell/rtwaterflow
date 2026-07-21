"""Retry ladder — hydraulic tier semantics + never-500 discipline.

M3 ladder (4 tiers, pinned here AND in test_nonconvergence.py — change in
lockstep): colebrook (n iter) → colebrook (3n) → swamee-jain (3n, the
EXPLICIT Colebrook approximation — converges on transitional-Reynolds
states where the implicit model's Newton flip-flops) → nikuradse last
resort (upstream issue #803 biases low-Re friction). Both fallbacks are
honestly "degraded"; the vocabulary ok|degraded|failed survives.
"""
from __future__ import annotations

from rtwaterflow.simulator import SolveOutcome, retry_attempts, solve_with_retry


def test_ladder_tiers_and_solver_iter_semantics():
    """RTWATERFLOW_SOLVER_ITER sets base iter of tier 1; retries use 3x."""
    tiers = retry_attempts(50)
    assert [t["mode"] for t in tiers] == ["hydraulics"] * 4
    assert [t["iter"] for t in tiers] == [50, 150, 150, 150]
    assert [t["friction_model"] for t in tiers] == [
        "colebrook", "colebrook", "swamee-jain", "nikuradse"]
    # nikuradse must never be the primary model (issue #803); the
    # swamee-jain literal is HYPHENATED (swamee_jain silently falls back
    # to nikuradse in 0.14.0 — pinned in test_pandapipes_pins)
    assert tiers[0]["friction_model"] == "colebrook"
    # the inner Colebrook lambda Newton gets real iteration budgets (the
    # upstream default of 10 fails on near-stagnant noisy-demand stubs)
    assert tiers[0]["max_iter_colebrook"] == 100
    assert tiers[1]["max_iter_colebrook"] == 300


def test_hillside_tier1(hillside_inputs):
    from rtwaterflow.network_builder import build_network
    net, _ = build_network(hillside_inputs)
    outcome = solve_with_retry(net, iter_base=100)
    assert isinstance(outcome, SolveOutcome)
    assert outcome.converged is True
    assert outcome.status == "ok"
    assert outcome.tier == 1
    assert net.converged


def test_swamee_jain_tier_reports_degraded(hillside_inputs, monkeypatch):
    """When the colebrook tiers fail, the swamee-jain tier converges and
    the outcome is honestly 'degraded' with the explicit-approximation
    reason (NOT the nikuradse #803 message)."""
    import rtwaterflow.simulator as sim_module
    from rtwaterflow.network_builder import build_network

    net, _ = build_network(hillside_inputs)
    real_pipeflow = sim_module.pipeflow

    def flaky(net_, **kwargs):
        if kwargs.get("friction_model") == "colebrook":
            from pandapipes.pf.pipeflow_setup import PipeflowNotConverged
            raise PipeflowNotConverged("poisoned colebrook tier")
        return real_pipeflow(net_, **kwargs)

    monkeypatch.setattr(sim_module, "pipeflow", flaky)
    outcome = sim_module.solve_with_retry(net, iter_base=50)
    assert outcome.converged is True
    assert outcome.status == "degraded"
    assert outcome.tier == 3
    assert "swamee-jain" in (outcome.error or "")


def test_nikuradse_last_resort_reports_degraded(hillside_inputs, monkeypatch):
    """With colebrook AND swamee-jain poisoned, the nikuradse last resort
    still converges and carries the #803 friction-bias reason."""
    import rtwaterflow.simulator as sim_module
    from rtwaterflow.network_builder import build_network

    net, _ = build_network(hillside_inputs)
    real_pipeflow = sim_module.pipeflow

    def flaky(net_, **kwargs):
        if kwargs.get("friction_model") in ("colebrook", "swamee-jain"):
            from pandapipes.pf.pipeflow_setup import PipeflowNotConverged
            raise PipeflowNotConverged("poisoned tier")
        return real_pipeflow(net_, **kwargs)

    monkeypatch.setattr(sim_module, "pipeflow", flaky)
    outcome = sim_module.solve_with_retry(net, iter_base=50)
    assert outcome.converged is True
    assert outcome.status == "degraded"
    assert outcome.tier == 4
    assert "nikuradse" in (outcome.error or "")
