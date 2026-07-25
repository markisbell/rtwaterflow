# rtwaterflow — Geodata Bundle Builder

`tools/bundle_builder/` turns a **real German town** into an rtwaterflow
five-file network bundle from open data: the town's OpenStreetMap street graph
and a digital elevation model. It is the offline analogue of the in-app NetzStudio
editor, and it produces the three shipped real-geodata bundles — `alpen`,
`neubeuern`, `kevelaer`.

The key modelling premise: **buried distribution mains are not in OSM**. So the
builder does not *extract* a pipe network — it **synthesises** one whose pipe
*routes* follow the real streets, anchored to the real terrain. The output is a
plausible, solvable teaching network for a real place, with honest attribution.

---

## 1. The two-step pipeline

The builder is split into an **online** step that touches the network once and an
**offline** step that is pure and deterministic:

```
  make_snapshot(place)   ──(ONLINE: osmnx + DEM HTTP)──►   snapshots/<id>.json   (committed, pinned)
  build_from_snapshot()  ──(OFFLINE: networkx + stdlib)──►  data/networks/<id>/   (the 5-file bundle)
```

This split is the milestone's acceptance criterion — *"the real-town bundle
builds offline-reproducibly from a pinned data snapshot"*. The heavy geo stack
(osmnx, rasterio/GDAL, pyproj) is needed **only** for the snapshot step; the
offline build + the whole test suite need only networkx + the stdlib, so CI
rebuilds every bundle from its committed snapshot without any geo dependency or
network access.

`pipeline.py` is the entry point (`make_snapshot`, `build_from_snapshot`,
`write_bundle`); `__main__.py` is the CLI.

---

## 2. The online half — freezing a snapshot

### 2.1 Street graph (`osm.py`)

`fetch_street_graph(place)` / `fetch_street_graph_bbox(place, N, S, E, W)` pull
the town's drivable street graph from OpenStreetMap via **osmnx → Overpass**,
project it to the metric **UTM** zone (EPSG:25832 west / 25833 east — all lengths
and snapping in metres, *never* degrees), simplify it to a clean
intersection-and-street graph, and flatten the osmnx MultiDiGraph into plain
Python geometry: `StreetNode` (osmid, WGS84 lat/lon, UTM x/y, true undirected
`degree`) and `StreetEdge` (u, v, metric `length_m`, name, WGS84 polyline). The
undirected degree matters — osmnx returns two directed half-edges per two-way
street, so a naive degree double-counts and a genuine dead-end (degree 1) becomes
unrepresentable; the builder collapses to a simple undirected graph first. The
required ODbL credit `© OpenStreetMap contributors (ODbL)` is attached.

### 2.2 Elevation (`elevation.py`)

Elevation is sampled **once** at snapshot time and frozen — the tick loop never
touches geodata. Two providers:

- **`OpenTopoDataProvider`** (the default) — the public OpenTopoData REST API,
  **EU-DEM v1.1 25 m** across Europe over standard HTTPS. Batched at 100 points
  per request with a 1.1 s pause (the public rate limit); bilinear interpolation.
  Chosen because it works anywhere, whereas Germany's DGM1 API (hoehendaten.de)
  sits on a non-standard, often-firewalled port. EU-DEM is coarser (~±2 m) but
  plenty to show terrain and Druckzonen. Credit: *Copernicus / EU-DEM*.
- **`RasterDGMProvider`** — samples a local **DGM GeoTIFF** (DGM1 1 m / DGM200
  200 m) with rasterio in its native CRS — the roadmap's primary path when a real
  Länder DEM tile is on disk. A point outside the tile or on the no-data mask is a
  **loud error**, never a silent NaN/0.0 frozen into the bundle.

Both return `(elevations, attribution)`, so the visible credit array in the bundle
reflects what was actually sampled.

### 2.3 The pinned snapshot (`snapshot.py`)

`save_snapshot` writes everything the online half produced — the projected street
graph, the per-node elevations, and the merged OSM+DEM attribution — into one
committed JSON, with nodes/edges in a **stable sort order** (by osmid) and
rounded coordinates, so the snapshot file itself is **byte-stable**.
`load_snapshot` reconstructs the `(StreetGraph, elevations)` with only the stdlib.

---

## 3. The offline half — synthesising a network (`synthesize.py`)

`synthesize(streets, elevations, cfg, attribution)` turns the street skeleton
into the five-file bundle. The base case is a **branched gravity network** — a
real shape for a small town and robust to build + solve:

1. **Largest connected component.** Take the largest connected street component
   (length-weighted, undirected). Optionally drop nodes above
   `cfg.max_elevation_m` first — a municipality boundary sweeps in forested-summit
   / castle access roads that are not real distribution mains.
2. **The mains tree.** For a flat town, the **minimum spanning tree** (by street
   length) is the mains — a branched tree, so every pipe is a cut edge (the
   loader's PRV/pump-cut-edge and reachability rules hold trivially) fed by a
   single head source. For a hilly town, a **gravity descent tree** is used
   instead (§ 4).
3. **The source.** An **ext_grid Hochbehälter** floats on the highest node, sized
   so the whole gravity zone stays in the DVGW band (head reserve above the
   highest consumer).
4. **Consumers.** The leaf nodes (a service connection at a dead-end) plus a
   sampled fraction of interior nodes become `residential_village` consumers, with
   the town population apportioned across them.
5. **Pipe diameters** taper from the source outward (trunk near the tank → branch
   at the leaves) with the configured material and the catalog roughness.

`SynthConfig` carries the knobs: `population`, `per_capita_l_per_d`,
`consumer_fraction`, `dn_trunk`/`dn_branch`/`material`, `head_reserve_m`,
`max_elevation_m`, and the zoning switches (§ 4). **Determinism** is enforced
throughout — every collection is processed in a stable order (nodes by osmid), so
the same snapshot rebuilds the same bundle byte-for-byte.

---

## 4. PRV zone-splitting — the hilly case

On a hilly town, one gravity zone would drive the deep streets far above PN 10
(10 bar), so `cfg.enable_prv_zoning` synthesises real German **Druckzonen** with
Druckminderer (`press_control` cut edges). Two pieces:

- **`_descent_tree`** — the mains are grown as a *gravity descent tree* from the
  source (attach the highest-elevation frontier node next, via its shortest street
  edge), so nodes are added top-down and each pressure zone is a **coherent
  elevation band**, not the wandering cut a length-MST makes across a flat valley
  mesh. (On Neubeuern, the length-MST + zoning first produced 25 spurious valves;
  the descent tree gives 6 clean ones.)
- **`_assign_zones`** — walk the tree from the source; where a child's static
  pressure `(head − elev)·BAR_PER_M` would exceed `pn_max_bar` (7 bar, under the
  8 bar Ruhedruck warning), insert a Druckminderer and reset the child's zone head
  to **serve the tallest node of that subtree** (+ the head reserve). That reset
  is the no-starvation guarantee — a branch that dips below a valve then climbs
  back up still gets pressure. The outlet pressure is **capped at `pn_max_bar`**
  so no node is ever *designed* above the band: a "trapped" high node (one the
  tree can only reach through a lower node) holds the ceiling rather than
  over-pressurising past PN; only a node further above than single-source gravity
  can physically reach is honestly under-pressured (it would need a booster).

A Druckminderer never raises head; the whole scheme is deterministic (a BFS in
ascending-osmid neighbour order).

---

## 5. The shipped bundles

| Bundle | Town | Config | Result |
|---|---|---|---|
| `alpen` | Alpen (Ortskern), Niederrhein — flat | `population=5200` | 182 j / 181 pipes, one gravity zone, 2.8–5.9 bar over the day, 0 violations |
| `neubeuern` | Neubeuern, Inn valley — hilly (~78 m relief) | `population=3000, enable_prv_zoning=True, max_elevation_m=530` | 215 j, **6 Druckminderer** (two-tier zones), 2.5–6.8 bar, LF3 fire case fails at the worst point |
| `kevelaer` | Kevelaer (Stadtkern), Niederrhein — flat, city-scale | `population=10000, material="GGG", dn_trunk=300, dn_branch=200, head_reserve_m=45` | 544 j, city-grade ductile-iron mains, 4.3–5.2 bar, the < 100 ms/tick performance stress case |

Each has a build script (`scripts/build_<id>.py`) that rebuilds it offline from
its pinned snapshot; `tests/test_bundle_builder.py` asserts every one is
byte-stable and solves in-band with no alarm flood.

---

## 6. Usage

### Rebuild a shipped bundle (offline, no geo stack)

```bash
python scripts/build_alpen.py         # or build_neubeuern.py / build_kevelaer.py
```

### Add a new town

```bash
# 1. install the geo stack (only for the snapshot step)
pip install -r tools/bundle_builder/requirements.txt

# 2. ONLINE: freeze a pinned snapshot (a place name, or a --bbox for a town core)
python -m bundle_builder snapshot "Kevelaer, Nordrhein-Westfalen, Germany" \
    --out tools/bundle_builder/snapshots/kevelaer.json \
    --bbox 51.5999 51.5599 6.2663 6.2263

# 3. OFFLINE: deterministic build (or a scripts/build_<id>.py for a tuned config)
python -m bundle_builder build tools/bundle_builder/snapshots/kevelaer.json \
    --id kevelaer --name "Kevelaer (Stadtkern)" --population 10000
```

`--bbox` (order N S E W) carves a compact, dense town **core** — a full
municipality is spread out (long rural roads → long high-friction mains), whereas
a ~2 km core is dense, short-pathed, and solves cleanly. Register the new bundle
in `data/network_library.json` and add its expected geo bbox to
`tests/test_network_geo.py`.

### Tuning for in-band + fast

A flat town has little gravity relief, so the source head reserve must cover the
friction over the tree: a village's PE DN110 branches cannot carry a *town's*
throughput in-band, so a city bundle (Kevelaer) uses **GGG DN200/300** mains and a
taller (45 m) head reserve. A hilly town instead needs `enable_prv_zoning` (§ 4)
and usually a `max_elevation_m` cap to exclude forested-summit outliers. The build
script's docstring records the exact `--bbox` and config for each bundle.
