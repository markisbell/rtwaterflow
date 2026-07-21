"""Process-wide runtime container — the blueprint's ``api/runtime.py`` App singleton.

One live :class:`App` per process (set by the FastAPI lifespan in
:mod:`rtwaterflow.api`): settings, the :class:`~rtwaterflow.state.StateStore`,
the :class:`~rtwaterflow.engine.RealtimeEngine`, and the active-network
metadata incl. the static topology payload served by ``GET /network``.

Routers never hold references of their own — they call :func:`get_app` per
request, so ``engine.reconfigure`` (grid swap) transparently swaps the world
under them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..engine import RealtimeEngine
from ..exporter import BulkExporter
from ..network_catalog import NetworkCatalog
from ..recorder import Recorder
from ..simulator import Simulator
from ..state import StateStore

#: API contract version, reported by /health and /status and stamped into the
#: generated docs/API.md. Bump with every milestone that changes the surface.
API_VERSION = "0.1.0"

# The network loaded at startup is ``settings.default_network``
# (``RTWATERFLOW_DEFAULT_NETWORK``, default ``tutorial_hillside``); the
# catalog (``/networks`` + ``/config/apply``) swaps it at runtime.


@dataclass
class App:
    """Everything the routers need, in one place."""

    settings: Settings
    store: StateStore
    engine: RealtimeEngine
    network_id: str
    network_dir: Path
    topology: dict = field(default_factory=dict)
    loaded_at: float = 0.0
    catalog: NetworkCatalog | None = None          # network library
    active: dict = field(default_factory=dict)     # /config/active metadata
    recorder: Recorder | None = None               # session recorder
    exporter: BulkExporter | None = None           # bulk exporter

    @property
    def sim(self) -> Simulator:
        return self.engine.sim


_app: App | None = None


def set_app(app: App) -> None:
    global _app
    _app = app


def clear_app() -> None:
    global _app
    _app = None


def get_app() -> App:
    if _app is None:
        raise RuntimeError(
            "rtwaterflow App not initialized — the FastAPI lifespan has not "
            "run (serve via uvicorn, or use the TestClient as a context "
            "manager so startup executes)")
    return _app


def status_payload(app: App | None = None) -> dict:
    """Engine status — ``GET /status`` and the fresh return of every control verb."""
    app = app or get_app()
    engine, latest = app.engine, app.store.latest
    return {
        "api_version": API_VERSION,
        "running": engine.running,
        "step": engine.step,
        "day": engine.day,
        "time_of_day": app.sim._time_of_day(engine.step),
        "interval_seconds": engine.interval,
        "steps_per_day": engine.steps_per_day,
        "network": {"id": app.network_id, "name": app.sim.inputs.name},
        "latest": None if latest is None else {
            "step": latest.step,
            "day": latest.day,
            "time_of_day": latest.time_of_day,
            "converged": latest.converged,
            "solver_status": latest.solver_status,
            "solve_ms": latest.solve_ms,
        },
    }


def recording_meta(app: App | None = None) -> dict:
    """The reproducibility recipe stored in a recording's metadata.json:
    what was simulated (network), what was measurable (sensor placement +
    fidelity mode), the estimation policy, how fast the clock ticked, and
    whether ground truth was on the wire at all."""
    app = app or get_app()
    sim = app.sim
    return {
        "rtwaterflow_version": API_VERSION,
        "network": app.active,
        "measurements": sim.measurement_placement(),
        "estimation": sim.est_config.as_dict(),
        "engine": status_payload(app),
        "expose_ground_truth": bool(app.settings.expose_ground_truth),
    }


def build_topology(network_id: str, sim: Simulator) -> dict:
    """Static topology payload for ``GET /network``.

    Single pipe layer: one entry per pipe (the "trench" vocabulary survives
    on the wire for UI continuity — trench id == pipe id). Geometry falls
    back to the straight from→to line when the input file carries none.
    """
    inputs, idx = sim.inputs, sim.index
    node_geo = {j.name: list(j.geo) for j in inputs.structure.junctions}
    nodes = [
        {"name": j.name, "kind": j.kind, "geo": list(j.geo),
         "elevation_m": j.elevation_m, "pn_bar": j.pn_bar}
        for j in inputs.structure.junctions
    ]
    trenches = []
    for i, p in enumerate(inputs.pipes.pipes):
        geometry = ([list(g) for g in p.geometry] if p.geometry
                    else [node_geo[p.from_node], node_geo[p.to_node]])
        trenches.append({
            "id": i,
            "from_node": p.from_node,
            "to_node": p.to_node,
            "length_km": p.length_km,
            "dn": p.dn,
            "material": p.material,
            "inner_diameter_mm": p.inner_diameter_mm,
            "k_mm": p.k_mm,
            "sections": p.sections,
            "geometry": geometry,  # [[lat, lon], ...] — WGS84, Leaflet-native
            "pipe": int(idx.pipes[i]),
        })
    # PRV branches (zone boundaries) — the UI needs their edges for the
    # Drucklinie path and their location for the station marker
    prvs = [
        {"id": int(m["pid"]), "name": m["name"],
         "from_node": m["from_node"], "to_node": m["node"]}
        for m in idx.producer_meta if m["kind"] == "prv"
    ]
    # pump-station branches (Drucklinie path edges + ⚙️ markers)
    stations = [
        {"id": int(m["pid"]), "name": m["name"],
         "from_node": m["from_node"], "to_node": m["node"]}
        for m in idx.producer_meta if m["kind"] == "station"
    ]
    consumers = [
        {"id": int(idx.consumers[i]), "name": idx.consumer_names[i],
         "node": idx.consumer_nodes[i],
         "kind": (idx.consumer_kinds[i] if idx.consumer_kinds else "consumer"),
         "mdot_demand_kg_per_s": float(sim.profiles.mdot_kg_per_s[i, 0])
         if i < sim.profiles.mdot_kg_per_s.shape[0] else None}
        for i in range(len(idx.consumers))
    ]
    producers = [
        {"id": int(m["pid"]), "kind": m["kind"], "name": m["name"],
         "node": m["node"]}
        for m in idx.producer_meta
    ]
    return {
        "id": network_id,
        "name": inputs.name,
        "nodes": nodes,
        "trenches": trenches,
        "consumers": consumers,
        "producers": producers,
        "prvs": prvs,
        "stations": stations,
        "steps_per_day": sim.profiles.steps_per_day,
        "n_days": sim.profiles.n_days,
    }
