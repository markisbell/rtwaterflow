"""Compliance endpoint — the M4 alarm center's data source.

``GET /findings`` — the latest frame's typed findings (German rule
citations) + severity counts. Findings derive from the ground-truth layer,
so strict mode serves an empty list with ``truth_hidden: true`` (the
observed-layer alarm view arrives with the M7 observer).
"""
from __future__ import annotations

from fastapi import APIRouter

from .runtime import get_app

router = APIRouter(tags=["compliance"])


@router.get("/findings", summary="Compliance findings (alarm center)")
def findings() -> dict:
    """Current findings with severity counts. Every finding cites its rule
    (DVGW W 400-1 / W 405 / W 300-1 — docs/COMPLIANCE.md maps the checks)."""
    app = get_app()
    latest = app.store.latest
    items: list[dict] = []
    if latest is not None and app.settings.expose_ground_truth:
        items = list(latest.findings)
    counts = {"violation": 0, "warning": 0, "info": 0}
    for f in items:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return {
        "findings": items,
        "counts": counts,
        "truth_hidden": not app.settings.expose_ground_truth,
        "step": None if latest is None else latest.step,
        "day": None if latest is None else latest.day,
        # staleness honesty: failed frames republish the last converged
        # findings — say so instead of serving them as current (M4 review)
        "converged": None if latest is None else latest.converged,
        "solver_status": None if latest is None else latest.solver_status,
    }
