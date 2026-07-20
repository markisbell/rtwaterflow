"""Estimation layer — STUBBED in M0.

The fork parent (rtheatflow) ships a full forward-simulation observer here
(twin net driven only by measured boundaries + priors, honesty tripwires).
The water observer returns in M7 of the roadmap with demand-archetype priors;
until then this module keeps the *plumbing* alive so that

* the engine can hold and re-apply an :class:`EstimationConfig` across grid
  swaps (``engine.set_est_config``),
* ``GET/POST /estimation/config`` serve an honest ``enabled=false`` policy,
* every frame carries ``estimated=null`` through the existing three-view
  fallback chain (the UI "Schätzung" segment stays wired but disabled).

``maybe_estimate`` never produces an estimate in M0.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class EstimationConfig:
    """Operator-facing estimation policy (GET/POST /estimation/config).

    ``enabled`` defaults to **False** in M0 — there is no observer to run.
    ``throttle_factor`` is kept for wire/scenario compatibility with the
    future water observer (M7).
    """

    enabled: bool = False
    throttle_factor: float = 2.0

    def as_dict(self) -> dict:
        return asdict(self)


class ForwardObserver:
    """M0 stub — attributes mirror the fork parent's surface
    (``seq``/``_ms``/``last`` are read by ``/estimation/config``) but no
    estimate is ever produced."""

    def __init__(self, sim, config: EstimationConfig):
        self.sim = sim
        self.config = config
        self.seq = 0
        self._ms = 0.0
        self.last: dict | None = None

    def maybe_estimate(self, payload: dict, tick: int,
                       step: int, day: int) -> dict | None:
        return None
