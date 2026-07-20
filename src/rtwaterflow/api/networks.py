"""Network catalog and the runtime network swap.

* ``GET /networks`` / ``GET /networks/{id}`` — committed library (manifest
  ``data/network_library.json``) + net-free preview stats.
* ``POST /networks/import`` — five-file JSON bundle upload into
  ``data/user_networks/<id>/``, validated by actually loading it through the
  full contract; a bundle that does not load is removed again (400, the
  blueprint convention).
* ``POST /config/apply`` — swap the running engine onto a catalog network
  (``engine.reconfigure`` + store reset — never a process restart). An
  active recording documents ONE configuration and is auto-stopped by the
  swap.
* ``GET /config/active`` — metadata of the current configuration.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..network_catalog import preview
from ..scenarios import _slug
from .runtime import build_topology, get_app, recording_meta, status_payload

log = logging.getLogger(__name__)

router = APIRouter(tags=["networks"])


@router.get("/networks", summary="Network library")
def networks() -> dict:
    """List the loadable networks of the committed library manifest."""
    app = get_app()
    return {
        "available": app.catalog is not None and app.catalog.available,
        "networks": app.catalog.list() if app.catalog else [],
    }


@router.get("/networks/{network_id}", summary="Network preview")
def network_preview(network_id: str) -> dict:
    """Net-free preview stats of a catalog network (loads + validates the
    five-file bundle on first access, cached)."""
    app = get_app()
    if app.catalog is None or not app.catalog.has(network_id):
        raise HTTPException(404, f"unknown network '{network_id}'")
    try:
        inputs = app.catalog.get_inputs(network_id)
    except Exception as exc:  # noqa: BLE001 — contract violation in files
        raise HTTPException(400, f"network '{network_id}' failed to load: {exc}")
    return preview(app.catalog.entry(network_id), inputs)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

class NetworkImportRequest(BaseModel):
    """The five contract documents (roadmap §3) as one JSON bundle."""
    name: Optional[str] = None          # catalog display name (defaults to the doc's)
    network_structure: dict
    pipes: dict
    consumers: dict
    supply: dict
    environment: dict


@router.post("/networks/import", summary="Import a network (five-file bundle)")
def networks_import(req: NetworkImportRequest) -> dict:
    """Import a five-file network bundle into ``data/user_networks/<id>/``.

    The documents are written to disk and validated by actually loading them
    through the full five-file contract (pydantic models + cross-validation);
    a bundle that does not load is removed again (400 — blueprint
    convention). On success the catalog is rescanned and the network appears
    in ``GET /networks`` with ``source="user"``."""
    import json as _json
    import shutil

    app = get_app()
    if app.catalog is None:
        raise HTTPException(409, "no network catalog configured")
    base = _slug(str(req.name or req.network_structure.get("name") or "import"))
    directory = Path(app.settings.user_networks_dir)
    directory.mkdir(parents=True, exist_ok=True)
    d, n = directory / base, 1
    while d.exists():                    # never overwrite an earlier import
        n += 1
        d = directory / f"{base}-{n}"
    d.mkdir(parents=True)
    docs = {"network_structure": req.network_structure, "pipes": req.pipes,
            "consumers": req.consumers, "supply": req.supply,
            "environment": req.environment}
    for fname, doc in docs.items():
        (d / f"{fname}.json").write_text(
            _json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    nid = f"user_{d.name}"
    try:
        if not app.catalog.has(nid):     # has() rescans the user dir
            raise ValueError("bundle not recognized by the catalog scan")
        inputs = app.catalog.get_inputs(nid)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — contract violation: reject upload
        shutil.rmtree(d, ignore_errors=True)
        app.catalog.rescan_user()
        raise HTTPException(
            400, f"not an importable five-file network bundle: {exc}")
    log.info("imported network %s -> %s", nid, d)
    return preview(app.catalog.entry(nid), inputs)


# ---------------------------------------------------------------------------
# Runtime network swap
# ---------------------------------------------------------------------------

class ApplyRequest(BaseModel):
    network_id: str


async def apply_network(app, network_id: str, source: str) -> dict:
    """Shared swap path for ``/config/apply`` and the scenario replay."""
    if app.catalog is None or not app.catalog.has(network_id):
        raise HTTPException(404, f"unknown network '{network_id}'")
    try:
        # refresh: a swap must ALWAYS reflect the on-disk five files — the
        # engine rebuild dwarfs a JSON re-parse (stale-cache bug 2026-07-17)
        inputs = app.catalog.get_inputs(network_id, refresh=True)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — conversion/validation failure
        raise HTTPException(400, f"failed to load network "
                                 f"'{network_id}': {exc}")
    # a recording documents ONE configuration — auto-stop before the swap
    # (stop drains the writer queue off the event loop)
    if app.recorder is not None:
        await asyncio.to_thread(app.recorder.stop)
    await app.engine.reconfigure(inputs)
    app.network_id = network_id
    entry = app.catalog.entry(network_id)
    if entry.dir:
        app.network_dir = Path(entry.dir)
    app.loaded_at = time.time()
    topo = build_topology(network_id, app.sim)
    app.topology = topo
    app.active = {
        "network_id": network_id,
        "name": inputs.name,
        "source": source,
        "applied_at": app.loaded_at,
        "n_consumers": len(inputs.consumers.consumers),
        "n_days": inputs.n_days,
    }
    return topo


@router.post("/config/apply", summary="Swap the running network")
async def config_apply(req: ApplyRequest) -> dict:
    """Load a catalog network and swap the running engine onto it: new
    Simulator built off-thread, store reset, clock at day 0 / step 0 —
    never a process restart."""
    app = get_app()
    topo = await apply_network(app, req.network_id, "catalog")
    if app.settings.record and app.recorder is not None:
        # continuous operation: one recording pack per configuration
        app.recorder.start(recording_meta(app))
    log.info("applied network %s", req.network_id)
    return {"status": status_payload(app), "active": app.active,
            "network": topo}


@router.get("/config/active", summary="Active configuration")
def config_active() -> dict:
    """Metadata of the currently loaded network (id, source)."""
    return get_app().active
