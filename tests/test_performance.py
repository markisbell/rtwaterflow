"""City-scale performance pass (M9 stage 2, roadmap §6 M9 / §7.4).

The roadmap's performance bar: a city-scale bundle of **≥ 500 junctions warm-
solves < 100 ms/tick**. The committed Kevelaer (Stadtkern) geodata bundle — ~540
real-OSM junctions on the flat Niederrhein — is that stress case. Per-tick cost is
dominated by the number of pipeflow calls (the retry-ladder tiers), not the
junction count, so a clean single-zone gravity net that converges tier-1 stays
fast even at this size.

This is an ACCEPTANCE test of that bar on the **hydraulic solve** (the M7 observer
OFF): the roadmap's performance concern predates the observer, and the observer
is an optional estimation overlay that deep-copies + re-solves a whole twin per
tick. On this host the hydraulic solve medians ~37 ms (max ~59 ms over a day) —
comfortably inside 100 ms with real headroom, so the median-of-N assertion
tolerates ambient-load spikes without flaking, and a regression large enough to
breach 100 ms fails. The DEFAULT engine (observer ON) medians ~73 ms but is
heavy-tailed (deep-copy GC), occasionally exceeding 100 ms — which is exactly why
the observer self-throttles and is disabled here for the hydraulic-solve budget;
that overlay's own cost is its own concern, not the hydraulic-engine bar.
"""
from __future__ import annotations

import statistics
import time

from conftest import REPO_ROOT, make_settings

from rtwaterflow.data_loader import load_network
from rtwaterflow.estimator import EstimationConfig
from rtwaterflow.simulator import Simulator

KEVELAER_DIR = REPO_ROOT / "data" / "networks" / "kevelaer"


def test_city_scale_hydraulic_solve_under_100ms():
    """>= 500-junction warm hydraulic solve (observer off) medians < 100 ms."""
    inputs = load_network(KEVELAER_DIR)
    assert len(inputs.structure.junctions) >= 500        # the city-scale bar
    sim = Simulator(inputs, make_settings(steps_per_day=96))
    sim.set_est_config(EstimationConfig(enabled=False))  # hydraulic-solve budget
    for t in range(5):                                   # warm the numba JIT + pn
        assert sim.run_step(t, 0).converged
    times = []
    for t in range(20):
        t0 = time.perf_counter()
        r = sim.run_step(t % 96, 0)
        times.append((time.perf_counter() - t0) * 1000)
        assert r.converged
    median = statistics.median(times)
    assert median < 100.0, f"warm median {median:.1f} ms over the 100 ms bar: {times}"
