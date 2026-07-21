"""DVGW W 410 peak factors — the VALIDATION TARGETS of the demand engine.

fd = max-day / mean-day demand, fh = peak-hour / mean-hour demand as a
function of supplied population E (areas > 1,000 inhabitants). TF §6 pins
the curves AND their known bias: the DVGW/TZW project W 201712 found they
OVERESTIMATE peaks in smaller areas — so the engine is validated to land
within ±20 % of them (tests/test_demand_engine.py), never driven by them.
"""
from __future__ import annotations


def w410_fd(population: float) -> float:
    """Daily peak factor fd = 3.9 · E^(−0.0752)."""
    return 3.9 * float(population) ** -0.0752


def w410_fh(population: float) -> float:
    """Hourly peak factor fh = 18.1 · E^(−0.1682)."""
    return 18.1 * float(population) ** -0.1682
