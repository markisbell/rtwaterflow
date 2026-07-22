"""The pinned data snapshot (M8 offline reproducibility).

A snapshot freezes everything the ONLINE half produced — the projected street
graph and the per-node elevations + the attribution — into one committed JSON.
The OFFLINE build reads it and rebuilds the bundle byte-for-byte with no
network access (roadmap M8 acceptance: "builds offline-reproducibly from a
pinned data snapshot"). Nodes/edges are stored in a stable order so the
snapshot file itself is byte-stable.
"""
from __future__ import annotations

import json
from pathlib import Path

from .osm import StreetEdge, StreetGraph, StreetNode


def save_snapshot(path: str | Path, streets: StreetGraph,
                  elevations: dict[int, float]) -> None:
    nodes = sorted(streets.nodes, key=lambda n: n.osmid)
    edges = sorted(streets.edges, key=lambda e: (min(e.u, e.v), max(e.u, e.v)))
    doc = {
        "place": streets.place,
        "epsg": streets.epsg,
        "attribution": sorted(set(streets.attribution)),
        "nodes": [{
            "osmid": n.osmid,
            "lat": round(n.lat, 7), "lon": round(n.lon, 7),
            "x_utm": round(n.x_utm, 2), "y_utm": round(n.y_utm, 2),
            "degree": n.degree,
            "elevation_m": round(float(elevations[n.osmid]), 2),
        } for n in nodes],
        "edges": [{
            "u": e.u, "v": e.v, "length_m": round(e.length_m, 2),
            "name": e.name,
            "polyline": [[round(la, 7), round(lo, 7)] for la, lo in e.polyline],
        } for e in edges],
    }
    Path(path).write_text(
        json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8", newline="\n")


def load_snapshot(path: str | Path) -> tuple[StreetGraph, dict[int, float]]:
    """Reconstruct the (StreetGraph, elevations) from a pinned snapshot."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    nodes = [StreetNode(
        osmid=int(n["osmid"]), lat=float(n["lat"]), lon=float(n["lon"]),
        x_utm=float(n["x_utm"]), y_utm=float(n["y_utm"]),
        degree=int(n["degree"])) for n in doc["nodes"]]
    edges = [StreetEdge(
        u=int(e["u"]), v=int(e["v"]), length_m=float(e["length_m"]),
        name=e["name"],
        polyline=[(float(la), float(lo)) for la, lo in e["polyline"]])
        for e in doc["edges"]]
    elevations = {int(n["osmid"]): float(n["elevation_m"]) for n in doc["nodes"]}
    graph = StreetGraph(place=doc["place"], epsg=int(doc["epsg"]),
                        nodes=nodes, edges=edges,
                        attribution=list(doc["attribution"]))
    return graph, elevations
