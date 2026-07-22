"""NetzStudio water editor — backend (M8 stage 2, roadmap §5).

The interactive "how networks are built" editor draws a water network on real
OpenStreetMap streets, then verifies it against the DVGW W 400-1 design load
cases before committing it. This module is the thin service behind that
(mirroring the sibling `gridedit` tool's pattern — raw Overpass/Nominatim via
httpx, no heavy geo stack in the runtime):

* ``GET  /editor/streets`` — streets + building footprints for a bbox
  (Overpass, disk-cached) the frontend snaps drawn pipes onto.
* ``GET  /editor/geocode`` — place-name search (Nominatim) for the map.
* ``POST /editor/elevation`` — DEM elevation for clicked points (OpenTopoData;
  frozen into the bundle at edit time — TF §11).
* ``POST /editor/loadcheck`` — run the three W 400-1 load cases on the bundle
  the editor is building; pass/fail per case before "commission network"
  (which reuses ``POST /networks/import``).
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, ValidationError

from ..data_loader import DataContractError, load_network_from_docs
from ..loadcases import run_load_cases
from .runtime import get_app

log = logging.getLogger(__name__)
router = APIRouter(tags=["editor"])

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OPENTOPO_URL = "https://api.opentopodata.org/v1/eudem25m"
_UA = {"User-Agent": "rtwaterflow-editor/0.1 (educational water-network editor)"}
#: ~3 km² at German latitudes — keep the editable area village-sized
MAX_BBOX_DEG2 = 0.001
_CACHE = Path("./cache/editor")

_STREET_QUERY = """
[out:json][timeout:25];
(
  way["highway"~"^(residential|living_street|service|unclassified|tertiary|secondary|primary|track)$"]({s},{w},{n},{e});
  way["building"]({s},{w},{n},{e});
);
out body geom;
"""


def _cache_get(key: str) -> Any | None:
    p = _CACHE / f"{key}.json"
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def _cache_put(key: str, value: Any) -> None:
    _CACHE.mkdir(parents=True, exist_ok=True)
    (_CACHE / f"{key}.json").write_text(
        json.dumps(value, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# OSM streets + buildings
# ---------------------------------------------------------------------------

@router.get("/editor/streets", summary="OSM streets + buildings for a bbox")
def editor_streets(s: float = Query(...), w: float = Query(...),
                   n: float = Query(...), e: float = Query(...)) -> dict:
    """Streets (drawn pipes snap onto them) + building footprints (consumer
    placement) for the bbox (south, west, north, east). Overpass, cached."""
    if not (-90.0 <= s < n <= 90.0) or not (-180.0 <= w < e <= 180.0):
        raise HTTPException(
            422, "invalid bbox (need -90 ≤ south < north ≤ 90, "
                 "-180 ≤ west < east ≤ 180)")
    if (n - s) * (e - w) > MAX_BBOX_DEG2:
        raise HTTPException(
            422, f"bbox is {(n - s) * (e - w):.5f} deg² — choose a smaller "
                 f"area (limit {MAX_BBOX_DEG2} ≈ a village)")
    key = "streets_" + hashlib.sha1(
        f"{s:.5f},{w:.5f},{n:.5f},{e:.5f}".encode()).hexdigest()[:16]
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        r = httpx.post(OVERPASS_URL,
                       data={"data": _STREET_QUERY.format(s=s, w=w, n=n, e=e)},
                       headers=_UA, timeout=60.0)
        r.raise_for_status()
        raw = r.json()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Overpass request failed: {exc}")
    except (ValueError, KeyError) as exc:
        raise HTTPException(502, f"Overpass returned an unusable response: {exc!r}")

    streets, buildings = [], []
    for el in raw.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        pts = [[round(g["lat"], 7), round(g["lon"], 7)] for g in el["geometry"]]
        tags = el.get("tags", {})
        if "building" in tags:
            lat = sum(p[0] for p in pts) / len(pts)
            lon = sum(p[1] for p in pts) / len(pts)
            buildings.append({"id": el["id"],
                              "center": [round(lat, 7), round(lon, 7)]})
        elif "highway" in tags:
            streets.append({"id": el["id"], "name": tags.get("name"),
                            "nodes": pts})
    out = {"bbox": [s, w, n, e], "streets": streets, "buildings": buildings,
           "attribution": "© OpenStreetMap contributors (ODbL)"}
    _cache_put(key, out)
    return out


@router.get("/editor/geocode", summary="Place-name search (Nominatim)")
def editor_geocode(q: str = Query(..., min_length=2, max_length=120)) -> dict:
    """Resolve a place name → [{name, lat, lon}] for the map search box."""
    q = " ".join(q.split())
    key = "geo_" + hashlib.sha1(q.lower().encode()).hexdigest()[:16]
    cached = _cache_get(key)
    if cached is not None:
        return {"hits": cached}
    try:
        r = httpx.get(NOMINATIM_URL,
                      params={"q": q, "format": "jsonv2", "limit": 5,
                              "accept-language": "de"},
                      headers=_UA, timeout=20.0)
        r.raise_for_status()
        hits = [{"name": h.get("display_name", q),
                 "lat": float(h["lat"]), "lon": float(h["lon"])}
                for h in r.json()]
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"geocoding failed: {exc}")
    _cache_put(key, hits)
    return {"hits": hits}


class ElevationBody(BaseModel):
    #: [[lat, lon], ...] — WGS84 (Leaflet-native order)
    points: list[tuple[float, float]]


@router.post("/editor/elevation", summary="DEM elevation for clicked points")
def editor_elevation(body: ElevationBody) -> dict:
    """Frozen elevation for each point (EU-DEM 25 m via OpenTopoData) — the
    editor stamps it into the junction at edit time (TF §11)."""
    pts = body.points
    if len(pts) > 100:            # OpenTopoData caps a request at 100 — make
        raise HTTPException(      # the limit explicit, never silently truncate
            422, "at most 100 points per request (batch client-side)")
    if not pts:
        return {"elevations": [], "attribution": None}
    locs = "|".join(f"{lat:.6f},{lon:.6f}" for lat, lon in pts)
    try:
        r = httpx.get(OPENTOPO_URL,
                      params={"locations": locs, "interpolation": "bilinear"},
                      headers=_UA, timeout=30.0)
        r.raise_for_status()
        doc = r.json()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"elevation lookup failed: {exc}")
    if doc.get("status") != "OK":
        raise HTTPException(502, f"elevation service: {doc.get('error')}")
    elevs = [None if res.get("elevation") is None
             else round(float(res["elevation"]), 1) for res in doc["results"]]
    return {"elevations": elevs,
            "attribution": ("Elevation: EU-DEM v1.1 — Copernicus data, "
                            "funded by the European Union")}


# ---------------------------------------------------------------------------
# W 400-1 load-case check (the "commission a passing network" gate)
# ---------------------------------------------------------------------------

@router.post("/editor/loadcheck", summary="DVGW W 400-1 three-load-case check")
def editor_loadcheck(bundle: dict = Body(...)) -> dict:
    """Run the three W 400-1 sizing load cases (max delivery / peak-hour max
    day / fire case) on the five-file *bundle* the editor is building. Returns
    pass/fail per case + the binding quantities. 422 if the bundle does not
    validate (a structural problem the editor must fix first)."""
    try:
        inputs = load_network_from_docs(bundle)
    except DataContractError as exc:
        raise HTTPException(422, {"error": "bundle does not validate",
                                  "problems": exc.errors})
    except ValidationError as exc:
        # a per-document schema error (a field out of bounds, a bad enum) is
        # ALSO the editor's to fix — 422 with a normalised problem list, never
        # a bare 500 (review)
        raise HTTPException(422, {"error": "bundle does not validate",
                                  "problems": [
                                      f"{'/'.join(str(p) for p in e['loc'])}: "
                                      f"{e['msg']}" for e in exc.errors()]})
    settings = get_app().settings
    return run_load_cases(inputs, settings)
