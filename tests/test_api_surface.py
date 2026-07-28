"""API-surface pinning (SPEC §11, §12 M2 acceptance).

The complete route inventory is pinned here: **any** route added, removed,
renamed or re-methoded must force an edit of this file (blueprint's
distinctive test pattern). The generated ``docs/API.md`` is checked for
staleness against the same inventory.
"""
from __future__ import annotations

import importlib.util

from conftest import REPO_ROOT, make_settings

from rtwaterflow.api import create_app
from rtwaterflow.api.runtime import API_VERSION

# ---------------------------------------------------------------------------
# THE M6 SURFACE (61 routes). Deliberately exhaustive and alphabetical —
# change the API, change this list, consciously. The fork parent's thermal
# routes (weather, heatingcurve, dpcontrol, storage, bypass, loadgen,
# producer CRUD) were removed in M0; M2 added the water asset routes
# (GET /tanks, GET /stations, POST /station/{name}); M3 added the weather
# knob (GET/POST /environment); M4 the alarm center (GET /findings); M5 the
# pressure-dependent hydraulics (emitters + PDA toggle); M6 the raw-water
# side (GET /wellfields, POST /wellfield/drought, well regeneration).
# ---------------------------------------------------------------------------
EXPECTED = {
    ("DELETE", "/consumer/{consumer_id}"),
    ("DELETE", "/emitter/{name}"),
    ("DELETE", "/leakage"),
    ("DELETE", "/measurements/consumer/{consumer_id}"),
    ("DELETE", "/measurements/node/{node_id}"),
    ("DELETE", "/recordings/{rid}"),
    ("DELETE", "/scenarios/{sid}"),
    ("GET", "/"),
    ("GET", "/emitters"),
    ("GET", "/export"),
    ("GET", "/recording"),
    ("GET", "/recordings"),
    ("GET", "/recordings/{rid}/download"),
    ("GET", "/config/active"),
    ("GET", "/environment"),
    ("GET", "/estimation/config"),
    ("GET", "/findings"),
    ("GET", "/health"),
    ("GET", "/history"),
    ("GET", "/manual"),
    ("GET", "/measurements"),
    ("GET", "/network"),
    ("GET", "/networks"),
    ("GET", "/networks/{network_id}"),
    ("GET", "/pda"),
    ("GET", "/producers"),
    ("GET", "/scenarios"),
    ("GET", "/state"),
    ("GET", "/stations"),
    ("GET", "/status"),
    ("GET", "/tanks"),
    ("GET", "/wellfields"),
    ("POST", "/burst"),
    ("POST", "/config/apply"),
    ("POST", "/consumer"),
    ("POST", "/control/interval"),
    ("POST", "/control/pause"),
    ("POST", "/control/resume"),
    ("POST", "/control/seek"),
    ("POST", "/control/seekday"),
    ("POST", "/control/start"),
    ("POST", "/environment"),
    ("POST", "/estimation/config"),
    ("POST", "/export/cancel"),
    ("POST", "/export/days"),
    ("POST", "/hydrant"),
    ("POST", "/leakage"),
    ("POST", "/measurements/consumer/{consumer_id}"),
    ("POST", "/measurements/mode"),
    ("POST", "/measurements/node/{node_id}"),
    ("POST", "/measurements/preset"),
    ("POST", "/networks/import"),
    ("POST", "/pda"),
    ("POST", "/recording/start"),
    ("POST", "/recording/stop"),
    ("POST", "/scenarios"),
    ("POST", "/scenarios/{sid}/load"),
    ("POST", "/station/{name}"),
    ("POST", "/wellfield/drought"),
    ("POST", "/wellfield/{name}/well/{well}/regenerate"),
    # M8 stage 2 — NetzStudio editor backend
    ("GET", "/editor/streets"),
    ("GET", "/editor/geocode"),
    ("POST", "/editor/elevation"),
    ("POST", "/editor/loadcheck"),
    # gamebridge — simgames co-simulation contract v1 (docs/contract/v1.md)
    ("GET", "/gb/version"),
    ("GET", "/gb/result/latest"),
    ("POST", "/gb/net/reset"),
    ("POST", "/gb/net/patch"),
    ("POST", "/gb/step"),
    ("WS", "/gb/ws"),
    ("WS", "/ws"),
}


def _walk_ws_routes(routes) -> list:
    """FastAPI ≥0.139 nests included routers lazily (_IncludedRouter)."""
    found = []
    for route in routes:
        if type(route).__name__.endswith("WebSocketRoute"):
            found.append(route)
        inner = getattr(route, "routes", None) or getattr(
            getattr(route, "original_router", None), "routes", None)
        if inner:
            found.extend(_walk_ws_routes(inner))
    return found


def inventory(app) -> set[tuple[str, str]]:
    spec = app.openapi()
    routes = {(m.upper(), path)
              for path, methods in spec["paths"].items() for m in methods}
    routes |= {("WS", r.path) for r in _walk_ws_routes(app.routes)}
    return routes


def test_route_inventory_is_pinned():
    app = create_app(make_settings())
    actual = inventory(app)
    added = actual - EXPECTED
    removed = EXPECTED - actual
    assert not added and not removed, (
        f"API surface changed — update EXPECTED consciously.\n"
        f"  added:   {sorted(added)}\n  removed: {sorted(removed)}")


def test_api_version_reported():
    assert API_VERSION == "0.1.0"
    app = create_app(make_settings())
    assert app.version == API_VERSION


def test_generated_api_md_is_current():
    """docs/API.md is generated (scripts/gen_api_doc.py) — never stale."""
    spec = importlib.util.spec_from_file_location(
        "gen_api_doc", REPO_ROOT / "scripts" / "gen_api_doc.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    expected_md = gen.render(gen.collect(create_app(make_settings())))
    on_disk = (REPO_ROOT / "docs" / "API.md").read_text(encoding="utf-8")
    assert on_disk.replace("\r\n", "\n") == expected_md.replace("\r\n", "\n"), (
        "docs/API.md is stale — regenerate: python scripts/gen_api_doc.py")
