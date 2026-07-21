"""PDAController — Wagner pressure-driven demand (roadmap §4.3, TF §8 gap).

pandapipes solves DEMAND-driven: a fixed sink withdrawal is met whatever
the pressure, so an undersupplied node reports an impossible NEGATIVE
gauge pressure instead of a dry tap. The Wagner pressure-demand model
(the WNTR/EPANET 2.2 PDD default) makes delivery a function of pressure:

    factor(p) = 0                                   for p ≤ p_min
              = ((p − p_min)/(p_req − p_min))^0.5   for p_min < p < p_req
              = 1                                   for p ≥ p_req

Below ``p_req`` (the W 400-1 storey requirement) taps deliver less; at
``p_min`` (0.5 bar above terrain — here 0.5 bar gauge) they run dry. The
Simulator drives the fixed point: solve → factors from the solved
pressures → write ``sink.scaling`` → re-solve, until the factors stop
moving (``TOL``) or ``MAX_ITERS``. A tiny demand floor keeps a starved
node off the zero-flow singularity.

Healthy nets are a NO-OP: every consumer sits at p ≥ p_req, all factors
are 1.0, the first factor pass sees no change and no re-solve happens.
The controller only bites where the physics is genuinely short of head —
exactly the "household taps run dry" teaching signal.
"""
from __future__ import annotations

#: convergence tolerance on the consistency gap max |factor(p) − scaling|
TOL = 0.02
#: cap on PDA re-solves per tick. The naive undamped iteration oscillates
#: on infeasible demands (zero → recover → full → crash), so the update is
#: DAMPED and the cap is above the roadmap's 5 to let damping settle. A
#: tick that exhausts the cap is honestly reported "degraded" (a very
#: stiff undersupply — the frame still carries positive, bounded
#: pressures; the warm-started next tick usually settles).
MAX_ITERS = 20
#: damping on the scaling/withdrawal update — the convergence signal is
#: the UNDAMPED gap, so a converged frame is genuinely Wagner-consistent
#: (delivery matches solved pressure). 0.4 balances stiff-case stability
#: against iteration count on realistic (gradual) undersupply.
DAMP = 0.4
#: default pressure at which delivery ceases [bar gauge]
P_MIN_BAR = 0.5
#: Wagner exponent (½ = the classic square-root law)
EXPONENT = 0.5
#: demand floor as a fraction of base demand — keeps a fully starved sink
#: off the zero-flow singularity without materially changing the balance
FLOOR_FRACTION = 0.001


class PDAController:
    """Stateless Wagner factor evaluator + fixed-point bookkeeping."""

    def __init__(self, enabled: bool = True, p_min_bar: float = P_MIN_BAR,
                 exponent: float = EXPONENT):
        self.enabled = bool(enabled)
        self.p_min_bar = float(p_min_bar)
        self.exponent = float(exponent)

    def factor(self, p_bar: float, p_req_bar: float) -> float:
        """Wagner delivery fraction at solved pressure *p_bar* for a node
        whose full-service requirement is *p_req_bar*."""
        if p_req_bar <= self.p_min_bar:      # degenerate requirement
            return 1.0
        if p_bar >= p_req_bar:
            return 1.0
        if p_bar <= self.p_min_bar:
            return FLOOR_FRACTION            # dry tap (floored, not zero)
        frac = (p_bar - self.p_min_bar) / (p_req_bar - self.p_min_bar)
        return max(FLOOR_FRACTION, frac ** self.exponent)
