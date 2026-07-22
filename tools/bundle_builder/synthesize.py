"""Synthesise a gravity water network from a street graph (M8, TF §11).

Buried mains are not in OSM, so the pipe *routes* are synthesised along the
streets. The teaching network is a **branched gravity network** (a real shape
for a small town, and robust to build + solve):

1. take the largest connected street component;
2. its **minimum spanning tree** (by street length) is the pipe network — a
   branched tree, so every pipe is a cut edge (the loader's PRV/pump-cut-edge
   and reachability rules hold trivially) and a single head source feeds all;
3. a **Hochbehälter** (tank) floats on the highest node — the zone's head;
4. **consumers** sit on the leaf/street nodes, archetype *residential_village*,
   population apportioned from a per-town estimate;
5. **pipe diameters** taper from the tank outward (trunk DN 150 → branch DN
   100), PE material, roughness from the catalog.

Determinism (byte-stability): every collection is processed in a stable order
(nodes by ``osmid``), so the same snapshot rebuilds the same bundle.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

BAR_PER_M = 0.0979          # 1 m water column (matches the generators)


@dataclass
class SynthConfig:
    """Knobs for the synthesised network (frozen into the snapshot)."""

    network_id: str
    display_name: str
    #: total residents apportioned across the consumer nodes
    population: int = 3000
    #: mean per-capita demand [L/(person·d)] → base sink mdot
    per_capita_l_per_d: float = 130.0
    #: sampling fraction for INTERIOR nodes (every ⌈1/f⌉-th) — leaves are
    #: always consumers (a service connection at a dead-end), so the realized
    #: consumer share is higher than this on a leafy village tree
    consumer_fraction: float = 0.5
    #: diameter taper — trunk near the source, branch at the leaves. PE only
    #: comes in the SDR-17 d-series (…110, 125, 160…), never DN 100/150.
    dn_trunk: int = 160
    dn_branch: int = 110
    material: str = "PE"
    year_laid: int = 2005
    #: head margin held ABOVE the highest consumer at the ext_grid source —
    #: sizes the supply pressure so the whole gravity zone stays in the
    #: DVGW band (2.0 + 0.35·storeys min; 8 bar rest). ~30 m ≈ 3 bar spare.
    head_reserve_m: float = 30.0
    steps: int = 96
    resolution_minutes: int = 15
    season_day_of_year: int = 205


@dataclass
class Bundle:
    """The five file dicts + the merged attribution array."""

    network_structure: dict
    pipes: dict
    consumers: dict
    supply: dict
    environment: dict
    attribution: list[str]

    def files(self) -> dict[str, dict]:
        return {
            "network_structure": self.network_structure,
            "pipes": self.pipes,
            "consumers": self.consumers,
            "supply": self.supply,
            "environment": self.environment,
        }


def synthesize(streets, elevations: dict[int, float], cfg: SynthConfig,
               attribution: list[str]) -> Bundle:
    """Build the five-file bundle. *streets* is an :class:`osm.StreetGraph`
    (or the snapshot's equivalent), *elevations* maps osmid → m a.s.l."""
    import networkx as nx

    # -- 1. largest connected component (undirected, length-weighted) --------
    g = nx.Graph()
    for e in streets.edges:
        if e.u == e.v:
            continue
        w = max(float(e.length_m), 1.0)
        if not g.has_edge(e.u, e.v) or g[e.u][e.v]["length_m"] > w:
            g.add_edge(e.u, e.v, length_m=w, name=e.name, polyline=e.polyline)
    comps = list(nx.connected_components(g))
    if not comps:
        raise ValueError("no connected street component to synthesise a "
                         "network (empty / edge-less snapshot)")
    g = g.subgraph(max(comps, key=len)).copy()

    # -- 2. minimum spanning tree = the mains --------------------------------
    mst = nx.minimum_spanning_tree(g, weight="length_m")
    kept = sorted(mst.nodes(), key=int)
    if len(kept) < 2:
        raise ValueError("street component too small to synthesise a network")

    node_by_id = {n.osmid: n for n in streets.nodes}
    for osmid in kept:
        if osmid not in elevations:
            raise ValueError(f"missing elevation for node {osmid}")

    # stable node names n0..n<k> in osmid order (byte-stable)
    name_of = {osmid: f"n{i}" for i, osmid in enumerate(kept)}

    # -- 3. ext_grid source on the highest node (the gravity head) -----------
    source_id = max(kept, key=lambda n: (elevations[n], -int(n)))
    source_elev = elevations[source_id]

    # -- 4. consumers: leaves + every-other interior node --------------------
    deg = dict(mst.degree())
    interior = [n for n in kept if deg[n] >= 2 and n != source_id]
    leaves = [n for n in kept if deg[n] == 1 and n != source_id]
    keep_interior = interior[:: max(1, round(1.0 / cfg.consumer_fraction))]
    consumer_ids = sorted(set(leaves) | set(keep_interior), key=int)
    if not consumer_ids:
        consumer_ids = [n for n in kept if n != source_id][:1]
    # apportion population + base demand (L/d → kg/s, ρ≈1)
    per_node_pop = max(1, round(cfg.population / len(consumer_ids)))
    base_mdot = per_node_pop * cfg.per_capita_l_per_d / 86400.0     # kg/s

    # supply pressure: hold head_reserve above the HIGHEST consumer so the
    # whole gravity zone stays in-band (friction is small on a village mesh)
    highest_consumer_elev = max(elevations[c] for c in consumer_ids)
    head_abs = highest_consumer_elev + cfg.head_reserve_m
    source_p_bar = round(max(0.5, (head_abs - source_elev) * BAR_PER_M), 2)

    # -- 5. pipe diameters taper with tree depth from the source -------------
    depth = nx.single_source_shortest_path_length(mst, source_id)
    max_depth = max(depth.values()) or 1

    def _pn(osmid: int) -> float:
        return round(max(0.3, (head_abs - elevations[osmid]) * BAR_PER_M), 2)

    junctions = [{
        "name": name_of[osmid],
        "kind": ("source" if osmid == source_id
                 else "consumer" if osmid in consumer_ids else "node"),
        "geo": [round(node_by_id[osmid].lat, 6), round(node_by_id[osmid].lon, 6)],
        "elevation_m": round(elevations[osmid], 1),
        "pn_bar": _pn(osmid),
    } for osmid in kept]

    pipes = []
    for u, v in sorted((tuple(sorted((int(a), int(b))))
                        for a, b in mst.edges()), key=lambda e: (e[0], e[1])):
        data = mst[u][v]
        # trunk near the tank (shallow depth) → DN150, leaves → DN100
        d = min(depth.get(u, max_depth), depth.get(v, max_depth))
        dn = cfg.dn_trunk if d <= max_depth * 0.4 else cfg.dn_branch
        poly = data.get("polyline") or [
            [node_by_id[u].lat, node_by_id[u].lon],
            [node_by_id[v].lat, node_by_id[v].lon]]
        # orient the stored polyline u→v (osm edges are undirected)
        poly = [[round(la, 6), round(lo, 6)] for la, lo in poly]
        if (poly[0][0], poly[0][1]) != (round(node_by_id[u].lat, 6),
                                        round(node_by_id[u].lon, 6)):
            poly = list(reversed(poly))
        pipes.append({
            "from_node": name_of[u], "to_node": name_of[v],
            "dn": dn, "material": cfg.material, "year_laid": cfg.year_laid,
            "geometry": poly,
        })

    consumers = []
    for osmid in consumer_ids:
        # name from the first named incident street, else the node name
        streetname = None
        for nb in mst.neighbors(osmid):
            nm = mst[osmid][nb].get("name")
            if nm:
                streetname = nm
                break
        consumers.append({
            "node": name_of[osmid],
            "name": f"{streetname} ({name_of[osmid]})" if streetname
                    else name_of[osmid],
            "mdot_kg_per_s": round(base_mdot, 5),
            "kind": "residential_village",
            "storeys": 2,
            "size": {"population": per_node_pop},
        })

    supply = {
        "supplies": [{
            "node": name_of[source_id], "name": f"Einspeisung {cfg.display_name}",
            "kind": "ext_grid", "p_bar": source_p_bar,
        }],
        "prvs": [],
        "tanks": [],
        "stations": [],
        "wellfields": [],
    }

    t_air = [round(18.0 - 7.0 * math.cos(2 * math.pi * (i - 8) / cfg.steps), 2)
             for i in range(cfg.steps)]
    environment = {
        "resolution_minutes": cfg.resolution_minutes, "steps": cfg.steps,
        "t_air_c": t_air, "day_types": ["workday"], "dryness": [0.3],
        "season_day_of_year": cfg.season_day_of_year,
    }

    return Bundle(
        network_structure={"name": cfg.display_name, "junctions": junctions,
                           "attribution": sorted(set(attribution))},
        pipes={"pipes": pipes},
        consumers={"consumers": consumers},
        supply=supply,
        environment=environment,
        attribution=sorted(set(attribution)),
    )
