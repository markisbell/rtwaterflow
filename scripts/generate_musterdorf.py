"""Generate the Musterdorf demo bundle (deterministic, no RNG).

Musterdorf is the M1 flagship: a fictional Odenwald village (~35 nodes)
showing the canonical German small-town supply chain in one picture
(TECHNICAL_FOUNDATIONS.md §4):

* **Hochbehälter Musterberg** (420 m) — the floating head of the high zone.
* **Hochzone** (363–385 m): branched street net fed by gravity; rest
  pressures land in the recommended 4–6 bar mid-zone band.
* **Druckminderer Talstraße** (PRV at 345 m, p_out 2.8 bar) — the zone
  boundary; without it the low zone would sit above 10 bar.
* **Tiefzone** (302–332 m): a ring core (two-sided supply — the
  Ringnetz teaching point) with branched fringes, including legacy grey
  cast iron (GG, k = 1.0 mm) segments in the old town.
* Mixed consumers: residential street groups (1–4 storeys), the school,
  a brewery, a dairy farm and the Freibad — the M3 demand engine and the
  M4 compliance checks (per-storey minimum pressure) plug into the
  ``kind``/``storeys`` attributes without a bundle rewrite.

Pipe sizing uses the catalog (dn + material; k defaults per GW 303-1).
Pipe lengths are derived from the street geometry (great-circle), so the
bundle carries no redundant length column.

Run:  python scripts/generate_musterdorf.py     (writes data/networks/musterdorf/)
The output is committed; tests/test_musterdorf.py asserts byte-stability.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "networks" / "musterdorf"

# ---------------------------------------------------------------------------
# nodes: name -> (lat, lon, elevation_m)
# ---------------------------------------------------------------------------

NODES: dict[str, tuple[float, float, float]] = {
    # waterworks (suction side of the pump station, M2) + tank + Zubringer
    "ww":   (49.4500, 9.0055, 336.0),
    "ws":   (49.4502, 9.0057, 336.0),
    "hb":   (49.4600, 9.0000, 420.0),
    "z1":   (49.4593, 9.0012, 403.0),
    # Hochzone (branched streets, 363-385 m)
    "h1":   (49.4585, 9.0022, 385.0),
    "h2":   (49.4578, 9.0035, 378.0),
    "h3":   (49.4571, 9.0048, 372.0),
    "h4":   (49.4563, 9.0060, 368.0),
    "h5":   (49.4585, 9.0043, 375.0),
    "h6":   (49.4590, 9.0055, 370.0),
    "h7":   (49.4595, 9.0068, 366.0),
    "h8":   (49.4564, 9.0040, 380.0),
    "h9":   (49.4557, 9.0033, 383.0),
    "h10":  (49.4596, 9.0047, 373.0),
    "h11":  (49.4602, 9.0075, 363.0),
    # PRV station (Fallleitung end, both junctions at the station)
    "dm_i": (49.4550, 9.0075, 345.0),
    "dm_o": (49.4549, 9.0076, 345.0),
    # Tiefzone ring (village core, 306-332 m)
    "r1":   (49.4543, 9.0085, 332.0),
    "r2":   (49.4536, 9.0098, 326.0),
    "r3":   (49.4529, 9.0111, 320.0),
    "r4":   (49.4521, 9.0123, 314.0),
    "r5":   (49.4513, 9.0135, 309.0),
    "r6":   (49.4506, 9.0122, 306.0),
    "r7":   (49.4510, 9.0105, 310.0),
    "r8":   (49.4517, 9.0092, 316.0),
    "r9":   (49.4525, 9.0080, 322.0),
    "r10":  (49.4534, 9.0072, 328.0),
    # branched fringes (302-331 m)
    "b1":   (49.4534, 9.0122, 317.0),
    "b2":   (49.4526, 9.0135, 311.0),
    "b3":   (49.4497, 9.0140, 303.0),
    "b4":   (49.4498, 9.0110, 305.0),
    "b5":   (49.4503, 9.0090, 309.0),
    "b6":   (49.4512, 9.0070, 313.0),
    "b7":   (49.4530, 9.0060, 325.0),
    "b8":   (49.4542, 9.0060, 331.0),
}

CONSUMER_NODES = {
    "h2", "h3", "h5", "h6", "h7", "h8", "h9", "h10", "h11",
    "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10",
    "b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8",
}

# ---------------------------------------------------------------------------
# pipes: (from, to, dn, material, year_laid)
# ---------------------------------------------------------------------------

PIPES: list[tuple[str, str, int, str, int]] = [
    # Steigleitung: pump discharge up to the tank (M2)
    ("ws", "hb", 150, "GGG", 1994),
    # Zubringer + Hochzone (ductile iron mains, PE side streets)
    ("hb", "z1", 150, "GGG", 1992),
    ("z1", "h1", 150, "GGG", 1992),
    ("h1", "h2", 100, "GGG", 1992),
    ("h2", "h3", 100, "GGG", 1992),
    ("h3", "h4", 100, "GGG", 1992),
    ("h2", "h5", 90, "PE", 2011),
    ("h5", "h6", 90, "PE", 2011),
    ("h6", "h7", 90, "PE", 2011),
    ("h6", "h10", 90, "PE", 2014),
    ("h7", "h11", 90, "PE", 2014),
    ("h3", "h8", 90, "PE", 2008),
    ("h8", "h9", 90, "PE", 2008),
    # Fallleitung to the PRV station
    ("h4", "dm_i", 150, "GGG", 1992),
    # Tiefzone feed + ring (mixed stock incl. legacy grey cast iron)
    ("dm_o", "r1", 150, "GGG", 1995),
    ("r1", "r2", 125, "GGG", 1995),
    ("r2", "r3", 125, "GGG", 1995),
    ("r3", "r4", 110, "PE", 2016),
    ("r4", "r5", 110, "PE", 2016),
    ("r5", "r6", 110, "PE", 2016),
    ("r6", "r7", 110, "PE", 2016),
    ("r7", "r8", 125, "GG", 1965),
    ("r8", "r9", 125, "GG", 1965),
    ("r9", "r10", 125, "GGG", 1988),
    ("r10", "r1", 125, "GGG", 1988),
    # fringes
    ("r3", "b1", 90, "PE", 2016),
    ("r4", "b2", 90, "PE", 2016),
    ("r5", "b3", 110, "PE", 2018),
    ("r6", "b4", 90, "PE", 2005),
    ("r7", "b5", 90, "PE", 2005),
    ("r8", "b6", 110, "PE", 2010),
    ("r9", "b7", 90, "PE", 1999),
    ("r10", "b8", 90, "PE", 1999),
]

# ---------------------------------------------------------------------------
# consumers: node -> (name, mdot_kg_per_s, kind, storeys)
# ---------------------------------------------------------------------------

CONSUMERS: dict[str, tuple[str, float, str, int]] = {
    "h2":  ("Bergstraße 1-9", 0.08, "residential", 2),
    "h3":  ("Bergstraße 11-21", 0.09, "residential", 2),
    "h5":  ("Panoramaweg 2-12", 0.07, "residential", 1),
    "h6":  ("Panoramaweg 14-20", 0.06, "residential", 1),
    "h7":  ("Am Hang 1-15", 0.11, "residential", 2),
    "h8":  ("Kapellenweg 2-8", 0.05, "residential", 1),
    "h9":  ("Kapellenweg 10-16", 0.06, "residential", 1),
    "h10": ("Panoramaweg 22-28", 0.06, "residential", 1),
    "h11": ("Am Hang 17-23", 0.07, "residential", 2),
    "r2":  ("Hauptstraße 1-19 (MFH)", 0.28, "residential", 4),
    "r3":  ("Grundschule Musterdorf", 0.10, "school", 3),
    "r4":  ("Hauptstraße 21-39", 0.16, "residential", 3),
    "r5":  ("Talstraße 2-16", 0.14, "residential", 2),
    "r6":  ("Talstraße 18-30", 0.12, "residential", 2),
    "r7":  ("Marktplatz (MFH)", 0.24, "residential", 4),
    "r8":  ("Kirchgasse 1-11", 0.10, "residential", 2),
    "r9":  ("Ringstraße 2-14", 0.12, "residential", 2),
    "r10": ("Brauerei Musterbräu", 0.40, "industry", 2),
    "b1":  ("Gartenweg 1-9", 0.07, "residential", 1),
    "b2":  ("Gartenweg 11-17", 0.06, "residential", 1),
    "b3":  ("Milchviehhof Wiedemann", 0.22, "farm", 1),
    "b4":  ("Wiesenweg 2-10", 0.08, "residential", 1),
    "b5":  ("Wiesenweg 12-18", 0.06, "residential", 1),
    "b6":  ("Freibad Musterdorf", 0.12, "pool", 1),
    "b7":  ("Ringstraße 16-24", 0.09, "residential", 2),
    "b8":  ("Ringstraße 26-32", 0.08, "residential", 2),
}

STEPS = 96             # 15-min environment resolution, one day
RES_MIN = 15

#: typical German residential diurnal factors per hour (TF §6: night valley
#: 02-04, morning peak 07-08, evening peak 19-20); mean ≈ 0.94
HOURLY_FACTOR = [
    0.42, 0.38, 0.35, 0.36, 0.45, 0.65, 1.10, 1.60,
    1.45, 1.20, 1.10, 1.15, 1.20, 1.10, 0.95, 0.90,
    0.95, 1.10, 1.30, 1.45, 1.30, 1.00, 0.70, 0.50,
]

#: tank + hysteresis band (level metres above the tank bottom at 420 m)
TANK = dict(area_m2=60.0, level_min_m=1.0, level_max_m=4.6,
            level_initial_m=3.2, fire_reserve_m3=48.0)
PUMP_BAND = dict(on_below_m=2.4, off_above_m=4.2)

#: high zone reference head [m]: tank bottom + initial level
_HEAD_HIGH = 420.0 + TANK["level_initial_m"]
#: low zone reference head [m]: PRV outlet 2.8 bar at 345 m
_PRV_M = 345.0
_PRV_BAR = 2.8
_BAR_PER_M = 0.0979

HIGH_ZONE = {"hb", "z1", "h1", "h2", "h3", "h4", "h5", "h6", "h7", "h8",
             "h9", "h10", "h11", "dm_i"}


def _pn_bar(name: str, elev: float) -> float:
    """Hydrostatic init estimate per node — pump nets diverge from a flat
    cold start (runtime-pinned M2 discovery), so the bundle seeds realistic
    pressures for the first solve / failure resets."""
    if name == "ww":
        return 0.3
    if name == "ws":
        return 8.6            # pump discharge toward the tank
    if name in HIGH_ZONE:
        return round(max(0.31, (_HEAD_HIGH - elev) * _BAR_PER_M), 2)
    return round(_PRV_BAR + (_PRV_M - elev) * _BAR_PER_M, 2)


def _geometry(a: str, b: str) -> list[list[float]]:
    la, lo, _ = NODES[a]
    lb, lob, _ = NODES[b]
    return [[la, lo], [lb, lob]]


def build() -> dict[str, dict]:
    junctions = []
    for name, (lat, lon, elev) in NODES.items():
        kind = ("source" if name == "hb"
                else "consumer" if name in CONSUMER_NODES else "node")
        junctions.append({
            "name": name, "kind": kind, "geo": [lat, lon],
            "elevation_m": elev, "pn_bar": _pn_bar(name, elev),
        })

    pipes = [
        {"from_node": a, "to_node": b, "dn": dn, "material": mat,
         "year_laid": year, "geometry": _geometry(a, b)}
        for a, b, dn, mat, year in PIPES
    ]

    consumers = [
        {"node": node, "name": name, "mdot_kg_per_s": mdot,
         "kind": kind, "storeys": storeys}
        for node, (name, mdot, kind, storeys) in CONSUMERS.items()
    ]

    supply = {
        "supplies": [
            {"node": "ww", "name": "Wasserwerk Mustertal (Reinwasser)",
             "kind": "ext_grid", "p_bar": 0.3},
        ],
        "tanks": [
            {"node": "hb", "name": "Hochbehälter Musterberg",
             "kind": "durchlauf", **TANK},
        ],
        "stations": [
            {"from_node": "ww", "to_node": "ws",
             "name": "Pumpwerk Mustertal",
             "curve": [[0.0, 10.0], [15.0, 9.4], [30.0, 8.4], [45.0, 6.8]],
             "control": {"mode": "hysteresis",
                         "tank": "Hochbehälter Musterberg", **PUMP_BAND}},
        ],
        "prvs": [
            {"from_node": "dm_i", "to_node": "dm_o",
             "name": "Druckminderer Talstraße", "p_out_bar": 2.8},
        ],
    }

    # deterministic mild diurnal air temperature (M3 demand-driver slot)
    t_air = [round(10.0 - 4.0 * math.cos(2 * math.pi * (i - 8) / STEPS), 2)
             for i in range(STEPS)]
    factor = [HOURLY_FACTOR[i * 24 // STEPS] for i in range(STEPS)]
    environment = {
        "resolution_minutes": RES_MIN, "steps": STEPS, "t_air_c": t_air,
        "demand_factor": factor,
    }

    return {
        "network_structure": {"name": "Musterdorf", "junctions": junctions},
        "pipes": {"pipes": pipes},
        "consumers": {"consumers": consumers},
        "supply": supply,
        "environment": environment,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    docs = build()
    for fname, doc in docs.items():
        path = OUT / f"{fname}.json"
        path.write_text(
            json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(OUT.parents[2])}")


if __name__ == "__main__":
    main()
