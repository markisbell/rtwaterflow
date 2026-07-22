"""The two-step pipeline: ONLINE snapshot → OFFLINE build (M8).

    make_snapshot(place)  ──(online: osmnx + DEM)──►  snapshots/<id>.json
    build_from_snapshot() ──(offline, deterministic)──►  data/networks/<id>/

The build is pure and deterministic, so a committed snapshot always rebuilds
the same bundle (byte-stable) without any network access.
"""
from __future__ import annotations

import json
from pathlib import Path

from .elevation import OpenTopoDataProvider
from .osm import fetch_street_graph, fetch_street_graph_bbox
from .snapshot import load_snapshot, save_snapshot
from .synthesize import Bundle, SynthConfig, synthesize


def make_snapshot(place: str, out_path: str | Path, *,
                  bbox: tuple[float, float, float, float] | None = None,
                  network_type: str = "drive",
                  elevation=None) -> None:
    """ONLINE: fetch the street graph + sample elevations, freeze to a pinned
    snapshot. *bbox* = (north, south, east, west) overrides the place name."""
    if bbox is not None:
        streets = fetch_street_graph_bbox(place, *bbox,
                                          network_type=network_type)
    else:
        streets = fetch_street_graph(place, network_type=network_type)
    provider = elevation or OpenTopoDataProvider()
    result = provider.sample([(n.osmid, n.lat, n.lon) for n in streets.nodes])
    # merge the DEM attribution onto the street graph's OSM attribution
    streets = streets.__class__(
        place=streets.place, epsg=streets.epsg, nodes=streets.nodes,
        edges=streets.edges,
        attribution=sorted(set(streets.attribution) | set(result.attribution)))
    save_snapshot(out_path, streets, result.elevations)


def build_from_snapshot(snapshot_path: str | Path, cfg: SynthConfig) -> Bundle:
    """OFFLINE + deterministic: pinned snapshot → the five-file bundle."""
    streets, elevations = load_snapshot(snapshot_path)
    return synthesize(streets, elevations, cfg, streets.attribution)


def write_bundle(bundle: Bundle, out_dir: str | Path) -> None:
    """Write the five files with the repo's byte-stable JSON convention."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for fname, doc in bundle.files().items():
        (out / f"{fname}.json").write_text(
            json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n")
