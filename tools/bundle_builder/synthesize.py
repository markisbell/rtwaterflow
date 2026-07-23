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

For a HILLY town one gravity zone would drive the deep streets far above PN 10,
so ``SynthConfig.enable_prv_zoning`` walks the tree from the source and inserts
a **Druckminderer** (``press_control`` cut edge) wherever the downhill static
pressure would exceed ``pn_max_bar`` — the downstream subtree becomes its own
pressure zone reset to ``zone_reset_bar`` at the valve outlet (the real German
Druckzonen answer to relief, TF §2). It is OFF by default so a flat town stays
a single byte-identical zone.

Determinism (byte-stability): every collection is processed in a stable order
(nodes by ``osmid``, a BFS in ascending-osmid neighbour order for zoning), so
the same snapshot rebuilds the same bundle.
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
    #: PRV zone-splitting (Druckzonen). OFF by default so a flat town (Alpen)
    #: synthesises a single gravity zone byte-identically. When ON, the tree
    #: is walked from the source and a Druckminderer is inserted on the edge
    #: where the downhill static pressure would exceed ``pn_max_bar`` — the
    #: downstream subtree becomes a new zone reset to ``zone_reset_bar`` at the
    #: valve outlet (the real answer to > ~100 m of relief, TF §2).
    enable_prv_zoning: bool = False
    #: split when a node's static pressure would exceed this [bar] — kept below
    #: the 8 bar Ruhedruck warning / PN-10 so a shipped bundle never floods the
    #: alarm center (M8 review discipline).
    pn_max_bar: float = 7.0
    #: the FLOOR on a Druckminderer's outlet pressure [bar]. The reset head is
    #: normally computed to serve the tallest node downstream of the valve; this
    #: is the minimum it may fall to (a real PRV is rarely set below ~3 bar).
    zone_reset_bar: float = 3.0
    #: drop street nodes ABOVE this elevation [m a.s.l.] before synthesising —
    #: a distribution network serves the settled area, not the forested summit
    #: / castle access roads a municipality boundary sweeps in. None = keep all.
    max_elevation_m: float | None = None
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


def _descent_tree(g, source_id: int, elevations: dict[int, float]):
    """A gravity spanning tree of *g* rooted at the source: grow by always
    attaching the HIGHEST-elevation frontier node next (via its shortest street
    edge to a node already in the tree). Nodes are therefore added in
    descending-elevation order, so a node's parent is (almost always) uphill of
    it — the mains flow downhill and each pressure zone is a coherent elevation
    band, not the wandering cut a length-MST makes across a flat valley mesh.
    Deterministic: a lazy max-heap keyed (−elevation, osmid); parents by
    (edge length, osmid)."""
    import heapq

    import networkx as nx

    tree = nx.Graph()
    tree.add_nodes_from(g.nodes)
    in_tree = {source_id}
    heap: list[tuple[float, int, int]] = []

    def _push(u: int) -> None:
        for w in g.neighbors(u):
            if w not in in_tree:
                heapq.heappush(heap, (-elevations[w], int(w), w))

    _push(source_id)
    while heap and len(in_tree) < g.number_of_nodes():
        _, _, w = heapq.heappop(heap)
        if w in in_tree:
            continue
        parent = min((u for u in g.neighbors(w) if u in in_tree),
                     key=lambda u: (g[w][u]["length_m"], int(u)))
        d = g[w][parent]
        tree.add_edge(parent, w, length_m=d["length_m"], name=d.get("name"),
                      polyline=d.get("polyline"))
        in_tree.add(w)
        _push(w)
    return tree


def _assign_zones(mst, source_id: int, elevations: dict[int, float],
                  source_head: float, cfg: SynthConfig
                  ) -> tuple[dict[int, float], dict[tuple[int, int], float]]:
    """Walk the tree from the source and split it into pressure zones.

    Returns ``(zone_head, prv_edges)``: the absolute piezometric head [m a.s.l.]
    feeding every node, and the tree edges ``(parent, child)`` that become
    Druckminderer, mapped to the outlet pressure [bar] their downstream zone
    resets to. A split fires on the edge where the child's static pressure
    ``(head − elev)·BAR_PER_M`` would exceed ``pn_max_bar``.

    The reset head serves the HIGHEST node of the child's subtree (+ the head
    reserve), so no downstream node is ever starved even though the mains tree
    follows street length, not elevation (a branch that dips below the valve
    then climbs back up still gets pressure); the ``pn_max_bar`` split keeps
    each zone's low end under the band, so a tall subtree simply splits again
    deeper. The outlet pressure is CAPPED at ``pn_max_bar`` too, so no node is
    ever *designed* above the band — with a "trapped" high node (one the mains
    tree can only reach through a lower node, the descent tree's rare
    imperfection) the valve holds the band ceiling rather than over-pressurise
    the outlet past PN; that still serves anything up to ~one PN-band of relief
    above the valve, and only a node further above than that (unservable by
    single-source gravity anyway) is honestly under-pressured. A Druckminderer
    never raises head. Deterministic: a BFS in ascending-osmid neighbour order
    (byte-stable for the snapshot rebuild)."""
    from collections import deque

    # root the tree at the source (deterministic parent/children + BFS order)
    parent_of: dict[int, int | None] = {source_id: None}
    children: dict[int, list[int]] = {source_id: []}
    order: list[int] = []
    visited = {source_id}
    queue = deque([source_id])
    while queue:
        u = queue.popleft()
        order.append(u)
        for w in sorted(mst.neighbors(u), key=int):
            if w not in visited:
                visited.add(w)
                parent_of[w] = u
                children.setdefault(u, []).append(w)
                children.setdefault(w, [])
                queue.append(w)

    # highest elevation in each node's subtree (post-order = reversed BFS)
    max_sub = {n: elevations[n] for n in order}
    for u in reversed(order):
        p = parent_of[u]
        if p is not None and max_sub[u] > max_sub[p]:
            max_sub[p] = max_sub[u]

    floor = round(cfg.zone_reset_bar, 2)
    zone_head = {source_id: source_head}
    prv_edges: dict[tuple[int, int], float] = {}
    for u in order:
        for c in children.get(u, []):
            p_static = (zone_head[u] - elevations[c]) * BAR_PER_M
            if p_static > cfg.pn_max_bar:
                # serve the tallest node in c's subtree, but never raise head
                # and never DESIGN the outlet above the band (a trapped high
                # node holds the ceiling instead of over-pressurising past PN)
                new_head = min(max_sub[c] + cfg.head_reserve_m, zone_head[u])
                p_out = min(cfg.pn_max_bar,
                            max(round((new_head - elevations[c]) * BAR_PER_M, 2),
                                floor))
                zone_head[c] = elevations[c] + p_out / BAR_PER_M
                prv_edges[(u, c)] = p_out
            else:
                zone_head[c] = zone_head[u]
    return zone_head, prv_edges


def synthesize(streets, elevations: dict[int, float], cfg: SynthConfig,
               attribution: list[str]) -> Bundle:
    """Build the five-file bundle. *streets* is an :class:`osm.StreetGraph`
    (or the snapshot's equivalent), *elevations* maps osmid → m a.s.l."""
    import networkx as nx

    # -- 1. largest connected component (undirected, length-weighted) --------
    cap = cfg.max_elevation_m
    g = nx.Graph()
    for e in streets.edges:
        if e.u == e.v:
            continue
        if cap is not None and (elevations.get(e.u, float("-inf")) > cap
                                or elevations.get(e.v, float("-inf")) > cap):
            continue                        # above the distribution area
        w = max(float(e.length_m), 1.0)
        if not g.has_edge(e.u, e.v) or g[e.u][e.v]["length_m"] > w:
            g.add_edge(e.u, e.v, length_m=w, name=e.name, polyline=e.polyline)
    comps = list(nx.connected_components(g))
    if not comps:
        raise ValueError("no connected street component to synthesise a "
                         "network (empty / edge-less snapshot)")
    g = g.subgraph(max(comps, key=len)).copy()
    for osmid in g.nodes:
        if osmid not in elevations:
            raise ValueError(f"missing elevation for node {osmid}")

    # -- 3. ext_grid source on the highest node (the gravity head) -----------
    source_id = max(g.nodes, key=lambda n: (elevations[n], -int(n)))
    source_elev = elevations[source_id]

    # -- 2. the mains tree ---------------------------------------------------
    # a min-length spanning tree for a flat town (shortest mains); a gravity
    # DESCENT tree from the source when we split Druckzonen, so each zone is a
    # coherent elevation band instead of a wandering mesh cut (M8 stage 2c)
    if cfg.enable_prv_zoning:
        mst = _descent_tree(g, source_id, elevations)
    else:
        mst = nx.minimum_spanning_tree(g, weight="length_m")
    kept = sorted(mst.nodes(), key=int)
    if len(kept) < 2:
        raise ValueError("street component too small to synthesise a network")

    node_by_id = {n.osmid: n for n in streets.nodes}
    # stable node names n0..n<k> in osmid order (byte-stable)
    name_of = {osmid: f"n{i}" for i, osmid in enumerate(kept)}

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

    # -- 4a. pressure zones (Druckzonen): split the tree with Druckminderer --
    # the ext_grid's ACTUAL head after the 0.5-bar clamp — equals head_abs when
    # the reserve fits (flat towns → byte-identical), lower when the source
    # towers over its consumers (a hilly town, where zoning matters). A PRV
    # endpoint may still carry a consumer (a house fed at the reduced zone
    # pressure) — only HEAD sources may not, and a Druckminderer is not one.
    actual_head = source_elev + source_p_bar / BAR_PER_M
    if cfg.enable_prv_zoning:
        zone_head, prv_edges = _assign_zones(
            mst, source_id, elevations, actual_head, cfg)
    else:
        zone_head = {n: head_abs for n in kept}
        prv_edges = {}

    # -- 5. pipe diameters taper with tree depth from the source -------------
    depth = nx.single_source_shortest_path_length(mst, source_id)
    max_depth = max(depth.values()) or 1

    def _pn(osmid: int) -> float:
        return round(max(0.3, (zone_head[osmid] - elevations[osmid])
                         * BAR_PER_M), 2)

    junctions = [{
        "name": name_of[osmid],
        "kind": ("source" if osmid == source_id
                 else "consumer" if osmid in consumer_ids else "node"),
        "geo": [round(node_by_id[osmid].lat, 6), round(node_by_id[osmid].lon, 6)],
        "elevation_m": round(elevations[osmid], 1),
        "pn_bar": _pn(osmid),
    } for osmid in kept]

    # PRV edges are cut out of the pipe list (the Druckminderer IS that edge)
    prv_edge_set = {tuple(sorted((int(a), int(b)))) for a, b in prv_edges}

    pipes = []
    for u, v in sorted((tuple(sorted((int(a), int(b))))
                        for a, b in mst.edges()), key=lambda e: (e[0], e[1])):
        if (u, v) in prv_edge_set:
            continue
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

    # Druckminderer on each split edge (from = upper/inlet, to = lower/outlet)
    prvs = [{
        "from_node": name_of[parent], "to_node": name_of[child],
        "name": f"Druckminderer {name_of[child]}", "p_out_bar": p_out,
    } for (parent, child), p_out in sorted(
        prv_edges.items(), key=lambda kv: (int(kv[0][0]), int(kv[0][1])))]

    supply = {
        "supplies": [{
            "node": name_of[source_id], "name": f"Einspeisung {cfg.display_name}",
            "kind": "ext_grid", "p_bar": source_p_bar,
        }],
        "prvs": prvs,
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
