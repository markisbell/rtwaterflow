"""Generate the Lauenau demo bundle (deterministic, no RNG) — the M6
wells & aquifer reference.

A small Weserbergland town (fictional, modelled on the real Lauenau 2020
water emergency, TF §7): a well field feeds a Reinwasserbehälter (break
tank), a Netzpumpe lifts it to the Hochbehälter, and the village draws by
gravity. The well field is deliberately UNDERSIZED for the hot-weekend
peak — under a drought the aquifer falls, well production caps below the
peak, the break tank empties, the network pump's low-level protection
trips, the Hochbehälter drains, and households run dry (PDA).

Run:  python scripts/generate_lauenau.py   (writes data/networks/lauenau/)
The output is committed; tests/test_lauenau.py asserts byte-stability.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "networks" / "lauenau"

NODES: dict[str, tuple[float, float, float]] = {
    "bw":  (52.2960, 9.3870, 210.0),   # Reinwasserbehälter (break tank)
    "pw":  (52.2961, 9.3872, 210.0),   # Netzpumpe discharge
    "hb":  (52.2990, 9.3930, 268.0),   # Hochbehälter Kalversberg
    "d1":  (52.2985, 9.3945, 250.0),
    "d2":  (52.2980, 9.3960, 244.0),
    "d3":  (52.2975, 9.3975, 240.0),
    "d4":  (52.2970, 9.3990, 236.0),
    "d5":  (52.2965, 9.4005, 232.0),
}

CONSUMERS: dict[str, tuple[str, float, str, int, dict]] = {
    "d1": ("Hauptstraße 1-25", 0.9, "residential_village", 2,
           {"population": 630}),
    "d2": ("Am Kalversberg (MFH)", 1.1, "residential_village", 3,
           {"population": 770}),
    "d3": ("Talstraße 2-40", 0.8, "residential_village", 2,
           {"population": 560}),
    "d4": ("Gewerbegebiet Süd", 0.7, "industry", 1, {"employees": 55}),
    "d5": ("Rodenberger Weg", 0.6, "residential_village", 2,
           {"population": 420}),
}

PIPES: list[tuple[str, str, int, str, int]] = [
    ("pw", "hb", 200, "GGG", 1985),    # Steigleitung to the Hochbehälter
    ("hb", "d1", 150, "GGG", 1985),
    ("d1", "d2", 160, "PE", 2008),
    ("d2", "d3", 125, "PE", 2008),
    ("d3", "d4", 125, "PE", 2008),
    ("d4", "d5", 110, "PE", 2008),
]

#: Reinwasserbehälter (break tank): small buffer, drains in hours at peak
BREAK_TANK = dict(area_m2=40.0, level_min_m=0.5, level_max_m=4.0,
                  level_initial_m=3.0, fire_reserve_m3=0.0)
#: Hochbehälter: the village's floating head
HB_TANK = dict(area_m2=90.0, level_min_m=1.0, level_max_m=5.0,
               level_initial_m=3.5, fire_reserve_m3=100.0)

STEPS = 96
RES_MIN = 15


def _pn_bar(name: str, elev: float) -> float:
    if name == "bw":
        return round(BREAK_TANK["level_initial_m"] * 0.0979, 2)
    if name == "pw":
        return 5.0
    head = 268.0 + HB_TANK["level_initial_m"]
    return round((head - elev) * 0.0979, 2)


def build() -> dict[str, dict]:
    junctions = [
        {"name": n,
         "kind": ("source" if n in ("bw", "hb")
                  else "consumer" if n in CONSUMERS else "node"),
         "geo": [lat, lon], "elevation_m": elev, "pn_bar": _pn_bar(n, elev)}
        for n, (lat, lon, elev) in NODES.items()
    ]
    pipes = [
        {"from_node": a, "to_node": b, "dn": dn, "material": mat,
         "year_laid": yr,
         "geometry": [[NODES[a][0], NODES[a][1]], [NODES[b][0], NODES[b][1]]]}
        for a, b, dn, mat, yr in PIPES
    ]
    consumers = [
        {"node": nd, "name": nm, "mdot_kg_per_s": mdot, "kind": kind,
         "storeys": st, "size": size}
        for nd, (nm, mdot, kind, st, size) in CONSUMERS.items()
    ]
    supply = {
        "supplies": [],
        "prvs": [],
        "tanks": [
            {"node": "bw", "name": "Reinwasserbehälter", "kind": "break",
             **BREAK_TANK},
            {"node": "hb", "name": "Hochbehälter Kalversberg",
             "kind": "durchlauf", **HB_TANK},
        ],
        "stations": [
            {"from_node": "bw", "to_node": "pw", "name": "Netzpumpe Lauenau",
             "curve": [[0.0, 7.0], [15.0, 6.4], [30.0, 5.2], [45.0, 3.4]],
             "control": {"mode": "hysteresis",
                         "tank": "Hochbehälter Kalversberg",
                         "on_below_m": 2.5, "off_above_m": 4.5}},
        ],
        "wellfields": [
            {"name": "Brunnenfeld Rodenberg",
             "break_tank": "Reinwasserbehälter",
             "pump_head_m": 85.0, "efficiency": 0.6,
             "on_below_m": 2.0, "off_above_m": 3.6,
             "interference_fraction": 0.15,
             # deliberately SMALL storativity (a shallow teaching aquifer):
             # responds over the sim's fast-forward days, not the real
             # months (TF §5) — so the drought decline is visible on-screen
             "aquifer": {"storativity_area_m2": 1500.0,
                         "level_initial_m": 100.0,
                         "recharge_m3_per_d_mean": 520.0},
             "water_right": {"m3_per_a": 260000.0, "m3_per_d": 750.0},
             "wells": [
                 {"name": "Brunnen 1", "static_level_m": 100.0,
                  "spec_capacity_m3h_per_m": 1.6, "screen_top_m": 88.0,
                  "rated_m3_h": 14.0, "q_s_decay_per_a": 0.04},
                 {"name": "Brunnen 2", "static_level_m": 100.0,
                  "spec_capacity_m3h_per_m": 1.5, "screen_top_m": 88.0,
                  "rated_m3_h": 14.0, "q_s_decay_per_a": 0.04},
             ]},
        ],
    }
    t_air = [round(18.0 - 7.0 * math.cos(2 * math.pi * (i - 8) / STEPS), 2)
             for i in range(STEPS)]
    environment = {
        "resolution_minutes": RES_MIN, "steps": STEPS, "t_air_c": t_air,
        "day_types": ["workday"], "dryness": [0.4],
        "season_day_of_year": 205,
    }
    return {
        "network_structure": {"name": "Lauenau (Brunnenfeld)",
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
        path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")
        print(f"wrote {path.relative_to(OUT.parents[2])}")


if __name__ == "__main__":
    main()
