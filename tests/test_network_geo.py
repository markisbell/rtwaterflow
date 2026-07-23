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
    # Musterdorf: the fictional two-zone village, same Odenwald region
    # (302-420 m elevations need real hills under them)
    "musterdorf": (49.44, 49.47, 8.99, 9.02),
    # Mustertal: the Gegenbehälter valley line, a few km east of Musterdorf
    # (300-330 m elevations, Wasserturm on the eastern rise)
    "mustertal": (49.46, 49.48, 9.02, 9.05),
    # Lauenau: the M6 wells & aquifer town in the Weserbergland (modelled
    # on the real Lauenau 2020 water emergency; 210-268 m elevations)
    "lauenau": (52.29, 52.30, 9.38, 9.41),
    # Alpen (Ortskern): the M8 geodata-built bundle — REAL OSM streets on the
    # flat Niederrhein (21-53 m elevations from EU-DEM), UTM32
    "alpen": (51.56, 51.59, 6.49, 6.53),
    # Neubeuern (Druckzonen): the M8 stage-2c geodata bundle — REAL OSM streets
    # on the hilly Inn valley in Upper Bavaria (449-527 m from EU-DEM, UTM33),
    # PRV-zoned across ~78 m of relief
    "neubeuern": (47.75, 47.81, 12.12, 12.17),
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
