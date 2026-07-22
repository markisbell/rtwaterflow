"""OSM street-graph fetch + UTM projection (M8 geodata pipeline, TF §11).

The offline builder's ONLINE half: pull a town's drivable street graph from
OpenStreetMap (osmnx → Overpass), project it to the metric UTM zone
(EPSG:25832/25833 — all lengths/snapping in metres, never in degrees, TF §11),
simplify it to a clean intersection-and-street graph, and hand back plain
Python geometry (WGS84 lat/lon for the bundle + UTM metres for length work)
plus the OSM attribution string.

Buried distribution mains are NOT in OSM (TF §11), so the water network's pipe
*routes* are synthesised along the streets (``synthesize.py``) — this module
only provides the street skeleton the mains follow.
"""
from __future__ import annotations

from dataclasses import dataclass


#: "© OpenStreetMap contributors" is ALWAYS required (ODbL) — TF §11.
OSM_ATTRIBUTION = "© OpenStreetMap contributors (ODbL)"


@dataclass(frozen=True)
class StreetNode:
    """A street-graph node (intersection or dead-end)."""

    osmid: int
    lat: float
    lon: float
    x_utm: float          # easting  [m] in the town's UTM zone
    y_utm: float          # northing [m]
    degree: int           # street-graph degree (1 = dead-end, ≥3 = junction)


@dataclass(frozen=True)
class StreetEdge:
    """A street segment between two nodes, with its WGS84 polyline."""

    u: int                # from osmid
    v: int                # to osmid
    length_m: float       # metric length along the polyline (UTM)
    name: str | None      # OSM street name, if tagged
    polyline: list[tuple[float, float]]   # [(lat, lon), ...] incl. endpoints


@dataclass(frozen=True)
class StreetGraph:
    """Projected, simplified street graph of a place."""

    place: str
    epsg: int             # the UTM CRS the metres are in (25832 or 25833)
    nodes: list[StreetNode]
    edges: list[StreetEdge]
    attribution: list[str]

    def bbox(self) -> tuple[float, float, float, float]:
        """(min_lat, min_lon, max_lat, max_lon) over all nodes."""
        lats = [n.lat for n in self.nodes]
        lons = [n.lon for n in self.nodes]
        return (min(lats), min(lons), max(lats), max(lons))


def fetch_street_graph(place: str, network_type: str = "drive") -> StreetGraph:
    """Fetch + project + simplify the street graph of *place* (a geocodable
    name, e.g. ``"Neubeuern, Bayern, Germany"``). ONLINE (Overpass). Raises
    the underlying osmnx error if the place cannot be resolved / is empty."""
    import osmnx as ox

    ox.settings.use_cache = True
    ox.settings.log_console = False

    graph = ox.graph_from_place(place, network_type=network_type)
    return _to_street_graph(graph, place)


def fetch_street_graph_bbox(
    place: str, north: float, south: float, east: float, west: float,
    network_type: str = "drive",
) -> StreetGraph:
    """Same, from an explicit WGS84 bbox (osmnx 2.x order: left, bottom,
    right, top). Used when a place name is ambiguous."""
    import osmnx as ox

    ox.settings.use_cache = True
    ox.settings.log_console = False
    graph = ox.graph_from_bbox(bbox=(west, south, east, north),
                               network_type=network_type)
    return _to_street_graph(graph, place)


def _to_street_graph(graph, place: str) -> StreetGraph:
    """Project to auto-UTM and flatten the multigraph into plain geometry."""
    import osmnx as ox

    proj = ox.project_graph(graph)          # auto-selects the UTM zone
    epsg = int(str(proj.graph["crs"]).split(":")[-1])
    # osmnx uses UTM-N EPSG:326xx; the German ETRS89 equivalent is 258xx
    # (same metres for our purposes) — record the ETRS89 code the bundle
    # convention expects (32632→25832, 32633→25833)
    etrs = 25832 if epsg == 32632 else 25833 if epsg == 32633 else epsg

    # true undirected street degree: osmnx returns a MultiDiGraph (each
    # two-way street is TWO directed edges + parallel ways), so collapse to a
    # simple undirected graph first — else every node reads ~2× and a genuine
    # dead-end (degree 1) is unrepresentable (M8 review)
    import networkx as nx
    degree = dict(nx.Graph(graph).degree())

    nodes = [
        StreetNode(
            osmid=int(n),
            lat=float(graph.nodes[n]["y"]), lon=float(graph.nodes[n]["x"]),
            x_utm=float(proj.nodes[n]["x"]), y_utm=float(proj.nodes[n]["y"]),
            degree=int(degree[n]))
        for n in graph.nodes
    ]

    edges: list[StreetEdge] = []
    seen: set[tuple[int, int]] = set()
    for u, v, data in graph.edges(data=True):
        key = (min(int(u), int(v)), max(int(u), int(v)))
        if key in seen:                     # collapse parallel osm ways
            continue
        seen.add(key)
        if "geometry" in data:              # osmnx LineString (lon, lat)
            poly = [(float(lat), float(lon))
                    for lon, lat in data["geometry"].coords]
        else:
            poly = [(float(graph.nodes[u]["y"]), float(graph.nodes[u]["x"])),
                    (float(graph.nodes[v]["y"]), float(graph.nodes[v]["x"]))]
        name = data.get("name")
        if isinstance(name, list):
            name = name[0] if name else None
        edges.append(StreetEdge(
            u=int(u), v=int(v), length_m=float(data.get("length", 0.0)),
            name=str(name) if name is not None else None, polyline=poly))

    return StreetGraph(place=place, epsg=etrs, nodes=nodes, edges=edges,
                       attribution=[OSM_ATTRIBUTION])
