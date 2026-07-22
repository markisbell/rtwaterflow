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
from bundle_builder.pipeline import build_from_snapshot          # noqa: E402
from bundle_builder.snapshot import load_snapshot, save_snapshot  # noqa: E402
from bundle_builder.synthesize import SynthConfig                 # noqa: E402

from rtwaterflow.data_loader import load_network                  # noqa: E402
from rtwaterflow.simulator import Simulator                       # noqa: E402

SNAPSHOT = REPO_ROOT / "tools" / "bundle_builder" / "snapshots" / "alpen.json"
ALPEN_DIR = REPO_ROOT / "data" / "networks" / "alpen"
FILES = ("network_structure", "pipes", "consumers", "supply", "environment")

#: the exact config the committed Alpen bundle was built with (keep in sync
#: with scripts/build_alpen or the dev-log if these ever change)
ALPEN_CFG = dict(network_id="alpen", display_name="Alpen (Ortskern)",
                 population=5200)


def _build():
    return build_from_snapshot(SNAPSHOT, SynthConfig(**ALPEN_CFG))


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
