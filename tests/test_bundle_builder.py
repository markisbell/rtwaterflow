"""M8 geodata bundle builder — acceptance (roadmap §6, TF §11).

The ONLINE half (osmnx + DEM) is NOT exercised here — it needs the heavy geo
stack + network access. These tests drive the OFFLINE, deterministic half from
the committed **pinned snapshot** (only networkx + the stdlib), which is the
milestone's acceptance: *"the real-town bundle builds offline-reproducibly
from a pinned data snapshot"*. They prove the built Alpen bundle loads,
validates, solves, carries its geodata attribution, and rebuilds byte-for-byte.
"""
from __future__ import annotations

import json
import sys

import pytest

from conftest import REPO_ROOT, make_settings

sys.path.insert(0, str(REPO_ROOT / "tools"))
from bundle_builder.osm import StreetEdge, StreetGraph, StreetNode  # noqa: E402
from bundle_builder.pipeline import build_from_snapshot          # noqa: E402
from bundle_builder.snapshot import load_snapshot, save_snapshot  # noqa: E402
from bundle_builder.synthesize import SynthConfig, synthesize     # noqa: E402

from rtwaterflow.data_loader import load_network                  # noqa: E402
from rtwaterflow.loadcases import run_load_cases                  # noqa: E402
from rtwaterflow.simulator import Simulator                       # noqa: E402

SNAPSHOT = REPO_ROOT / "tools" / "bundle_builder" / "snapshots" / "alpen.json"
ALPEN_DIR = REPO_ROOT / "data" / "networks" / "alpen"
NEUBEUERN_SNAPSHOT = (REPO_ROOT / "tools" / "bundle_builder" / "snapshots"
                      / "neubeuern.json")
NEUBEUERN_DIR = REPO_ROOT / "data" / "networks" / "neubeuern"
FILES = ("network_structure", "pipes", "consumers", "supply", "environment")

#: the exact config the committed Alpen bundle was built with (keep in sync
#: with scripts/build_alpen or the dev-log if these ever change)
ALPEN_CFG = dict(network_id="alpen", display_name="Alpen (Ortskern)",
                 population=5200)
#: … and the committed Neubeuern (Druckzonen) bundle (scripts/build_neubeuern)
NEUBEUERN_CFG = dict(network_id="neubeuern",
                     display_name="Neubeuern (Druckzonen)",
                     population=3000, enable_prv_zoning=True,
                     max_elevation_m=530.0)


def _build():
    return build_from_snapshot(SNAPSHOT, SynthConfig(**ALPEN_CFG))


def _build_neubeuern():
    return build_from_snapshot(NEUBEUERN_SNAPSHOT, SynthConfig(**NEUBEUERN_CFG))


# --- the pinned snapshot -----------------------------------------------------

def test_snapshot_is_offline_and_self_consistent():
    """The snapshot loads with only the stdlib, and every node has a frozen
    elevation (no network / DEM call at build time — TF §11)."""
    streets, elevations = load_snapshot(SNAPSHOT)
    assert len(streets.nodes) > 100
    assert streets.epsg in (25832, 25833)
    assert all(n.osmid in elevations for n in streets.nodes)
    # attribution frozen in: OSM (always) + a DEM source
    joined = " ".join(streets.attribution)
    assert "OpenStreetMap" in joined and "EU-DEM" in joined


def test_snapshot_round_trips_byte_stable(tmp_path):
    streets, elevations = load_snapshot(SNAPSHOT)
    out = tmp_path / "again.json"
    save_snapshot(out, streets, elevations)
    assert (out.read_bytes().replace(b"\r\n", b"\n")
            == SNAPSHOT.read_bytes().replace(b"\r\n", b"\n"))


# --- the deterministic build -------------------------------------------------

def test_build_is_deterministic():
    """Same snapshot + config → identical bundle (no RNG, stable ordering)."""
    a, b = _build(), _build()
    assert a.files() == b.files()


def test_built_bundle_matches_the_committed_files():
    """Byte-stability pin: rebuilding from the snapshot reproduces the
    committed data/networks/alpen/ files exactly (like the generator
    round-trip tests)."""
    built = _build().files()
    for fname in FILES:
        fresh = (json.dumps(built[fname], indent=1, ensure_ascii=False) + "\n")
        committed = (ALPEN_DIR / f"{fname}.json").read_text("utf-8")
        assert fresh.replace("\r\n", "\n") == committed.replace("\r\n", "\n"), \
            f"{fname}.json drifted from the builder"


def test_synthesised_structure_is_a_gravity_tree():
    """One ext_grid source on the high point, a spanning-tree main (n-1
    pipes → every pipe a cut edge), consumers a strict subset of the nodes."""
    b = _build()
    n = len(b.network_structure["junctions"])
    assert len(b.pipes["pipes"]) == n - 1           # spanning tree
    supplies = b.supply["supplies"]
    assert len(supplies) == 1 and supplies[0]["kind"] == "ext_grid"
    assert not b.supply["tanks"] and not b.supply["stations"]
    sources = [j for j in b.network_structure["junctions"]
               if j["kind"] == "source"]
    assert len(sources) == 1 and sources[0]["name"] == supplies[0]["node"]
    # the source sits on the highest node (gravity head)
    top = max(b.network_structure["junctions"], key=lambda j: j["elevation_m"])
    assert top["kind"] == "source"


# --- the built bundle is a real, solvable network ----------------------------

def test_alpen_loads_validates_and_solves():
    inputs = load_network(ALPEN_DIR)
    assert len(inputs.structure.junctions) == 182
    sim = Simulator(inputs, make_settings(steps_per_day=96))
    statuses, p_min, p_max, worst_viol, worst_findings = set(), [], [], 0, 0
    for t in range(96):                          # a full day
        r = sim.run_step(t, 0)
        statuses.add(r.solver_status)
        p_min.append(r.summary["p_min_bar"])
        p_max.append(max(j["p_bar"] for j in r.junctions))
        worst_viol = max(worst_viol, sum(
            1 for f in r.findings if f["severity"] == "violation"))
        worst_findings = max(worst_findings, len(r.findings))
    assert statuses <= {"ok", "degraded"}
    # a flat-town gravity zone sized into the DVGW band: healthy everywhere
    assert min(p_min) > 2.0                      # above the W 400-1 floor
    assert max(p_max) < 8.0                      # below the rest-pressure band
    assert worst_viol == 0                       # a clean network
    # the near-uniformly-stagnant flat village mesh must NOT flood the alarm
    # center (M8 review — the per-pipe hygiene warnings aggregate above the
    # flood threshold, so seeded anomalies still stand out)
    assert worst_findings <= 6, f"alarm flood: {worst_findings} findings/tick"


def test_alpen_carries_geodata_attribution():
    inputs = load_network(ALPEN_DIR)
    attr = inputs.structure.attribution
    assert attr is not None
    joined = " ".join(attr)
    assert "© OpenStreetMap contributors" in joined
    assert "EU-DEM" in joined and "Copernicus" in joined


# --- Neubeuern: the hilly Druckzonen bundle (M8 stage 2c) --------------------

def test_neubeuern_build_is_deterministic_and_matches_committed():
    """Byte-stability pin for the ZONED build (descent tree + PRV splitting is
    deterministic too), and it reproduces the committed data/networks/neubeuern
    files exactly."""
    a, b = _build_neubeuern(), _build_neubeuern()
    assert a.files() == b.files()                    # no RNG in the zoning
    built = a.files()
    for fname in FILES:
        fresh = (json.dumps(built[fname], indent=1, ensure_ascii=False) + "\n")
        committed = (NEUBEUERN_DIR / f"{fname}.json").read_text("utf-8")
        assert fresh.replace("\r\n", "\n") == committed.replace("\r\n", "\n"), \
            f"{fname}.json drifted from the builder"


def test_neubeuern_is_a_zoned_gravity_tree():
    """A single ext_grid source on the high point, Druckminderer splitting the
    hillside into pressure zones, and still a spanning tree overall (pipes +
    PRVs = n − 1, so every edge — pipe or valve — is a cut edge)."""
    inputs = load_network(NEUBEUERN_DIR)                # loads ⇒ PRVs are cut
    j = inputs.structure.junctions
    n = len(j)
    assert n == 215
    assert len(inputs.pipes.pipes) + len(inputs.supply.prvs) == n - 1
    prvs = inputs.supply.prvs
    assert len(prvs) >= 3, "a 78 m-relief town must need real Druckzonen"
    supplies = inputs.supply.supplies
    assert len(supplies) == 1 and supplies[0].kind == "ext_grid"
    elev = {x.name: x.elevation_m for x in j}
    # the source sits on the highest kept node (gravity head)
    assert max(j, key=lambda x: x.elevation_m).name == supplies[0].node
    # the summit outliers above the cap were dropped (settled area only)
    assert max(elev.values()) <= 530.0
    # every Druckminderer feeds DOWNHILL (inlet above outlet) and reduces to a
    # sane band pressure — a real reducer, never a boost
    for v in prvs:
        assert elev[v.from_node] >= elev[v.to_node] - 1e-6
        assert 2.0 <= v.p_out_bar <= 8.0


def test_neubeuern_solves_in_band_without_alarm_flood():
    inputs = load_network(NEUBEUERN_DIR)
    sim = Simulator(inputs, make_settings(steps_per_day=96))
    p_min, p_max, statuses, worst_viol, worst_findings = [], [], set(), 0, 0
    for t in range(96):
        r = sim.run_step(t, 0)
        statuses.add(r.solver_status)
        p_min.append(r.summary["p_min_bar"])
        p_max.append(max(jj["p_bar"] for jj in r.junctions))
        worst_viol = max(worst_viol, sum(
            1 for f in r.findings if f["severity"] == "violation"))
        worst_findings = max(worst_findings, len(r.findings))
    assert statuses <= {"ok", "degraded"}
    # the Druckzonen keep the deep valley in-band despite ~78 m of relief
    assert min(p_min) > 2.0                           # above the W 400-1 floor
    assert max(p_max) < 8.0                           # below the rest band / PN
    assert worst_viol == 0
    assert worst_findings <= 6, f"alarm flood: {worst_findings} findings/tick"


def test_neubeuern_meets_the_peak_load_cases_but_not_the_fire_case():
    """The teaching insight: sized for normal + peak demand (LF1/LF2 pass), a
    hilly gravity town still cannot push the W 405 fire flow to its worst,
    highest, farthest point (LF3 fails) — like musterdorf/lauenau."""
    r = run_load_cases(load_network(NEUBEUERN_DIR), make_settings())
    lf1, lf2, lf3 = r["cases"]
    assert lf1["passed"] and lf2["passed"]
    assert not lf3["passed"]
    assert r["passed"] is False


# --- the PRV zoning algorithm, on a synthetic hilly street tree -------------

def _staircase_streets(drop_m: float, n: int = 14):
    """A descending main + hanging branches, ~*drop_m* of relief over *n*
    nodes — a synthetic hilly town to exercise the zoning in isolation."""
    nodes, edges, elev = [], [], {}
    for i in range(n):
        oid = 100 + i
        lat, lon = 47.80 - i * 0.002, 12.14 + i * 0.001
        nodes.append(StreetNode(osmid=oid, lat=lat, lon=lon,
                                x_utm=lon * 1e3, y_utm=lat * 1e3, degree=2))
        elev[oid] = 500.0 - i * (drop_m / (n - 1))
        if i:
            prev = nodes[i - 1]
            edges.append(StreetEdge(u=prev.osmid, v=oid, length_m=250.0,
                                    name="Hauptstraße",
                                    polyline=[(prev.lat, prev.lon), (lat, lon)]))
    for j, parent_idx in enumerate((3, 6, 9, 11)):     # dead-end branches
        p = nodes[parent_idx]
        oid = 200 + j
        nodes.append(StreetNode(osmid=oid, lat=p.lat + 7e-4, lon=p.lon + 7e-4,
                                x_utm=(p.lon + 7e-4) * 1e3,
                                y_utm=(p.lat + 7e-4) * 1e3, degree=1))
        elev[oid] = elev[p.osmid] - 4.0
        edges.append(StreetEdge(u=p.osmid, v=oid, length_m=120.0, name="Weg",
                                polyline=[(p.lat, p.lon),
                                          (p.lat + 7e-4, p.lon + 7e-4)]))
    streets = StreetGraph(place="Testberg", epsg=25832, nodes=nodes,
                          edges=edges,
                          attribution=["© OpenStreetMap contributors (ODbL)"])
    return streets, elev


def test_zoning_is_a_noop_on_a_flat_town():
    """With enable_prv_zoning ON but almost no relief, NO Druckminderer is
    inserted — a flat town stays one gravity zone."""
    streets, elev = _staircase_streets(drop_m=8.0)
    cfg = SynthConfig(network_id="flat", display_name="Flach",
                      enable_prv_zoning=True)
    b = synthesize(streets, elev, cfg, streets.attribution)
    assert b.supply["prvs"] == []


def test_zoning_splits_a_hilly_tree_and_never_starves(tmp_path):
    """~120 m of relief → multiple Druckzonen; the built bundle loads, solves,
    and stays in the band everywhere — no node is starved by a valve it sits
    above (the subtree-serving reset), none is over PN (the pn_max split)."""
    streets, elev = _staircase_streets(drop_m=120.0)
    cfg = SynthConfig(network_id="testberg", display_name="Testberg",
                      population=2000, enable_prv_zoning=True)
    b = synthesize(streets, elev, cfg, streets.attribution)
    n = len(b.network_structure["junctions"])
    assert len(b.pipes["pipes"]) + len(b.supply["prvs"]) == n - 1
    assert len(b.supply["prvs"]) >= 2                 # a real Druckzonen split
    # every design pn stays inside the band (no starve, and the pn_max cap
    # means no node is ever DESIGNED above the band → below PN-10)
    pns = [jn["pn_bar"] for jn in b.network_structure["junctions"]]
    assert min(pns) > 0.3 and max(pns) <= cfg.pn_max_bar + 0.01

    out = tmp_path / "testberg"
    out.mkdir()
    for fname, doc in b.files().items():
        (out / f"{fname}.json").write_text(
            json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n")
    sim = Simulator(load_network(out), make_settings(steps_per_day=96))
    p_min = min(sim.run_step(t, 0).summary["p_min_bar"] for t in range(24))
    assert p_min > 1.5                                # nothing starved


def test_zoning_caps_a_trapped_high_node_at_the_band(tmp_path):
    """A "trapped" high node — one the descent tree can only reach THROUGH a
    lower node — must not push a Druckminderer outlet above the band (PN-10):
    the reset serves the subtree but the pn_max cap holds the ceiling, so no
    node is designed over PN, and the trapped node (within one band of relief
    above its feed) is still served, not starved (M8 stage-2c review)."""
    # a steep spine S(590)→…→L(450) with a branch up to H(497) hanging ONLY off
    # the low node L — so H is added after L and parented to it (trapped)
    spine = [(300, 590.0), (301, 545.0), (302, 500.0), (303, 455.0), (304, 450.0)]
    nodes, edges, elev = [], [], {}
    for k, (oid, e) in enumerate(spine):
        lat, lon = 47.80 - k * 0.003, 12.14 + k * 0.002
        nodes.append(StreetNode(osmid=oid, lat=lat, lon=lon,
                                x_utm=lon * 1e3, y_utm=lat * 1e3, degree=2))
        elev[oid] = e
        if k:
            p = nodes[k - 1]
            edges.append(StreetEdge(u=p.osmid, v=oid, length_m=300.0,
                                    name="Steige",
                                    polyline=[(p.lat, p.lon), (lat, lon)]))
    low = nodes[-1]                                   # L @ 450 m
    nodes.append(StreetNode(osmid=305, lat=low.lat + 1e-3, lon=low.lon + 1e-3,
                            x_utm=(low.lon + 1e-3) * 1e3,
                            y_utm=(low.lat + 1e-3) * 1e3, degree=1))
    elev[305] = 497.0                                 # H, 47 m ABOVE its feed L
    edges.append(StreetEdge(u=low.osmid, v=305, length_m=150.0, name="Höhenweg",
                            polyline=[(low.lat, low.lon),
                                      (low.lat + 1e-3, low.lon + 1e-3)]))
    streets = StreetGraph(place="Falle", epsg=25832, nodes=nodes, edges=edges,
                          attribution=["© OpenStreetMap contributors (ODbL)"])
    cfg = SynthConfig(network_id="falle", display_name="Falle", population=800,
                      enable_prv_zoning=True)
    b = synthesize(streets, elev, cfg, streets.attribution)
    # the trapped node exists as its own junction and IS reached (no crash)
    names = {jn["name"]: jn for jn in b.network_structure["junctions"]}
    assert len(names) == 6
    # THE FIX: nothing designed above the band, despite the 47 m-taller subtree
    assert max(jn["pn_bar"] for jn in names.values()) <= cfg.pn_max_bar + 0.01
    # and the trapped node (within a band of relief above its feed) is served
    out = tmp_path / "falle"
    out.mkdir()
    for fname, doc in b.files().items():
        (out / f"{fname}.json").write_text(
            json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n")
    sim = Simulator(load_network(out), make_settings(steps_per_day=96))
    assert min(sim.run_step(t, 0).summary["p_min_bar"] for t in range(12)) > 1.0
