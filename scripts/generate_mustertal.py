"""Generate the Mustertal demo bundle (deterministic, no RNG).

Mustertal is the M2 **Gegenbehälter** reference: a single-zone valley line
where the pump station feeds at the WEST end and the Wasserturm sits on the
EAST hill — the network between them sees the classic two-state operation
(TF §4):

* pump ON, surplus over demand → water flows east THROUGH the village into
  the tank (the tank charges through the net);
* pump OFF (or peak demand) → the tank feeds back westward — the segment
  next to the tank REVERSES flow.

Small on purpose (8 nodes): the flow-direction arrows and the tank sawtooth
tell the whole story on one screen.

Run:  python scripts/generate_mustertal.py     (writes data/networks/mustertal/)
The output is committed; tests/test_mustertal.py asserts byte-stability.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "networks" / "mustertal"

NODES: dict[str, tuple[float, float, float]] = {
    "ww2": (49.4700, 9.0300, 300.0),   # waterworks (suction)
    "wp":  (49.4701, 9.0302, 300.0),   # pump discharge
    "wt1": (49.4700, 9.0320, 302.0),
    "wt2": (49.4699, 9.0340, 303.0),
    "wt3": (49.4699, 9.0360, 304.0),
    "wt4": (49.4698, 9.0380, 305.0),
    "wt5": (49.4697, 9.0400, 306.0),
    "twr": (49.4695, 9.0420, 330.0),   # Wasserturm hill (Gegenbehälter)
}

CONSUMERS: dict[str, tuple[str, float, str, int]] = {
    "wt1": ("Talweg 1-15", 0.35, "residential", 2),
    "wt2": ("Talweg 17-35 (MFH)", 0.45, "residential", 3),
    "wt3": ("Talweg 37-49", 0.30, "residential", 2),
    "wt4": ("Gewerbehof Mustertal", 0.50, "industry", 1),
    "wt5": ("Talweg 51-63", 0.40, "residential", 2),
}

PIPES: list[tuple[str, str, int, str, int]] = [
    ("wp", "wt1", 125, "GGG", 1990),
    ("wt1", "wt2", 160, "PE", 2012),
    ("wt2", "wt3", 160, "PE", 2012),
    ("wt3", "wt4", 160, "PE", 2012),
    ("wt4", "wt5", 160, "PE", 2012),
    ("wt5", "twr", 100, "GGG", 1990),   # the reversal segment
]

TANK = dict(area_m2=20.0, level_min_m=0.5, level_max_m=4.0,
            level_initial_m=2.0, fire_reserve_m3=20.0)

HOURLY_FACTOR = [
    0.42, 0.38, 0.35, 0.36, 0.45, 0.65, 1.10, 1.60,
    1.45, 1.20, 1.10, 1.15, 1.20, 1.10, 0.95, 0.90,
    0.95, 1.10, 1.30, 1.45, 1.30, 1.00, 0.70, 0.50,
]

STEPS = 96
RES_MIN = 15


def _pn_bar(name: str, elev: float) -> float:
    if name == "ww2":
        return 0.3
    if name == "wp":
        return 3.4                       # pump discharge estimate
    if name == "twr":
        return 0.2                       # tank column above its junction
    head = 330.0 + TANK["level_initial_m"]
    return round((head - elev) * 0.0979, 2)


def build() -> dict[str, dict]:
    junctions = [
        {"name": name,
         "kind": ("source" if name == "twr"
                  else "consumer" if name in CONSUMERS else "node"),
         "geo": [lat, lon], "elevation_m": elev,
         "pn_bar": _pn_bar(name, elev)}
        for name, (lat, lon, elev) in NODES.items()
    ]
    pipes = [
        {"from_node": a, "to_node": b, "dn": dn, "material": mat,
         "year_laid": year,
         "geometry": [[NODES[a][0], NODES[a][1]], [NODES[b][0], NODES[b][1]]]}
        for a, b, dn, mat, year in PIPES
    ]
    consumers = [
        {"node": node, "name": name, "mdot_kg_per_s": mdot,
         "kind": kind, "storeys": storeys}
        for node, (name, mdot, kind, storeys) in CONSUMERS.items()
    ]
    supply = {
        "supplies": [
            {"node": "ww2", "name": "Wasserwerk Mustertal II (Reinwasser)",
             "kind": "ext_grid", "p_bar": 0.3},
        ],
        "tanks": [
            {"node": "twr", "name": "Wasserturm Mustertal",
             "kind": "gegen", **TANK},
        ],
        "stations": [
            {"from_node": "ww2", "to_node": "wp",
             "name": "Pumpwerk Mustertal II",
             "curve": [[0.0, 4.5], [10.0, 4.1], [20.0, 3.4], [30.0, 2.4]],
             "control": {"mode": "hysteresis",
                         "tank": "Wasserturm Mustertal",
                         "on_below_m": 1.2, "off_above_m": 3.4}},
        ],
        "prvs": [],
    }
    t_air = [round(10.0 - 4.0 * math.cos(2 * math.pi * (i - 8) / STEPS), 2)
             for i in range(STEPS)]
    factor = [HOURLY_FACTOR[i * 24 // STEPS] for i in range(STEPS)]
    environment = {
        "resolution_minutes": RES_MIN, "steps": STEPS, "t_air_c": t_air,
        "demand_factor": factor,
    }
    return {
        "network_structure": {"name": "Mustertal (Gegenbehälter)",
                              "junctions": junctions},
        "pipes": {"pipes": pipes},
        "consumers": {"consumers": consumers},
        "supply": supply,
        "environment": environment,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for fname, doc in build().items():
        path = OUT / f"{fname}.json"
        path.write_text(
            json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(OUT.parents[2])}")


if __name__ == "__main__":
    main()
