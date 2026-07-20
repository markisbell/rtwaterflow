"""Geo-placement regression: every catalog network must render on land, in
its documented region — no network may silently drift into a lake/ocean
(a real bug class in the fork parent, fixed 2026-07-17 there).

Every network in data/network_library.json MUST have an expected bbox here;
a new catalog entry without one fails loudly with instructions.
"""
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# id -> (lat_min, lat_max, lon_min, lon_max) — generous but region-true
EXPECTED_BBOX = {
    # Odenwald hillside near Eberbach: hilly terrain matching the fixture's
    # 346-400 m elevations
    "tutorial_hillside": (49.40, 49.52, 8.90, 9.06),
}


def catalog_ids():
    lib = json.loads((REPO / "data" / "network_library.json").read_text(encoding="utf-8"))
    return [entry["id"] for entry in lib["networks"]]


@pytest.mark.parametrize("net_id", catalog_ids())
def test_network_geo_in_documented_region(net_id):
    assert net_id in EXPECTED_BBOX, (
        f"catalog network '{net_id}' has no expected geo bbox — add it to "
        f"EXPECTED_BBOX in {__file__} (and make sure the placement is on land)"
    )
    lat_min, lat_max, lon_min, lon_max = EXPECTED_BBOX[net_id]
    doc = json.loads(
        (REPO / "data" / "networks" / net_id / "network_structure.json").read_text(encoding="utf-8")
    )
    geos = [j["geo"] for j in doc["junctions"] if j.get("geo")]
    assert geos, f"{net_id}: no junction carries geo coordinates"
    for lat, lon in geos:
        assert lat_min <= lat <= lat_max and lon_min <= lon <= lon_max, (
            f"{net_id}: junction at ({lat}, {lon}) is outside the documented "
            f"region ({lat_min}..{lat_max}, {lon_min}..{lon_max}) — "
            f"did an anchor/projection change move the network?"
        )
