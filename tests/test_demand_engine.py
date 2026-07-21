"""M3 acceptance: the archetype demand engine (roadmap §4.8, TF §6).

Bars: a synthetic year over the Musterdorf population reproduces the DVGW
W 410 peak factors fd/fh within ±20 % (the curves are VALIDATION TARGETS
with a documented small-area overestimation bias — never inputs); the
hot-dry override shifts the daily peak to 19–21 h (the 2018 regime); the
pool's nightly backwash pulse is visible in the Hochbehälter drawdown;
profiles are deterministic; legacy bundles reproduce their M2 demands
bit-exactly.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from conftest import REPO_ROOT, make_api_client, make_settings

from rtwaterflow.data_loader import load_network
from rtwaterflow.demand import (
    EnvironmentState,
    build_demand_profiles,
    w410_fd,
    w410_fh,
)
from rtwaterflow.models import EnvironmentFile
from rtwaterflow.net_inputs import NetInputs
from rtwaterflow.simulator import Simulator

MUSTERDORF_DIR = REPO_ROOT / "data" / "networks" / "musterdorf"
SPD = 96


@pytest.fixture(scope="module")
def inputs():
    return load_network(MUSTERDORF_DIR)


def _synthetic_year(inputs) -> NetInputs:
    """One-year environment over the Musterdorf consumer set: seasonal +
    diurnal temperature, a summer drought block with heat spikes (the fd/fh
    drivers), Monday-anchored default day types."""
    steps = 365 * SPD
    t_air = []
    dryness = []
    for d in range(365):
        doy = d + 1
        seasonal = 10.0 + 12.0 * math.cos(2 * math.pi * (doy - 197) / 365)
        # July/August drought block with a deterministic heat-spike comb
        hot = 183 <= doy <= 243
        spike = 6.0 if (hot and doy % 7 in (2, 3)) else 0.0
        dryness.append(0.8 if hot else 0.2)
        for i in range(SPD):
            diurnal = -6.0 * math.cos(2 * math.pi * (i - 8) / SPD)
            t_air.append(round(seasonal + diurnal + spike, 2))
    env = EnvironmentFile(
        resolution_minutes=15, steps=steps, t_air_c=t_air,
        dryness=dryness, season_day_of_year=1)
    return NetInputs(
        name=inputs.name, structure=inputs.structure, pipes=inputs.pipes,
        consumers=inputs.consumers, supply=inputs.supply, environment=env)


def test_w410_aggregate_over_synthetic_year(inputs):
    """THE M3 bar: aggregate residential peaks over one simulated year land
    within ±20 % of W 410 fd/fh for the bundle's population."""
    year = _synthetic_year(inputs)
    prof = build_demand_profiles(year, SPD)
    res_rows = [i for i, c in enumerate(inputs.consumers.consumers)
                if c.kind.startswith("residential") and c.size
                and c.size.population]
    population = sum(inputs.consumers.consumers[i].size.population
                     for i in res_rows)
    assert population > 1000        # W 410 validity domain
    agg = prof[res_rows, :].sum(axis=0)

    daily = agg.reshape(365, SPD).mean(axis=1)          # mean flow per day
    fd_sim = float(daily.max() / daily.mean())
    fd_ref = w410_fd(population)
    assert fd_ref * 0.8 <= fd_sim <= fd_ref * 1.2, \
        f"fd {fd_sim:.2f} vs W410 {fd_ref:.2f} (E={population})"

    hourly = agg.reshape(365 * 24, SPD // 24).mean(axis=1)  # mean per hour
    fh_sim = float(hourly.max() / hourly.mean())
    fh_ref = w410_fh(population)
    assert fh_ref * 0.8 <= fh_sim <= fh_ref * 1.2, \
        f"fh {fh_sim:.2f} vs W410 {fh_ref:.2f} (E={population})"


def test_hot_dry_day_shifts_peak_to_evening(inputs):
    """The 2018 regime (TF §6): on a hot-dry day the aggregate daily
    maximum migrates to 19–21 h and clearly exceeds the normal peak."""
    normal = build_demand_profiles(inputs, SPD).sum(axis=0)
    hot = build_demand_profiles(
        inputs, SPD,
        env=EnvironmentState(t_offset_c=6.0, dryness_override=0.9),
    ).sum(axis=0)
    peak_tick = int(hot.argmax())
    peak_hour = peak_tick * 24.0 / SPD
    assert 19.0 <= peak_hour <= 21.5, f"hot-day peak at hour {peak_hour:.1f}"
    normal_peak_hour = int(normal.argmax()) * 24.0 / SPD
    assert not 19.0 <= normal_peak_hour <= 21.5 or \
        hot.max() > 1.5 * normal.max()
    assert hot.max() > 1.5 * normal.max()
    # elevated night flows (agricultural irrigation) ride along
    assert hot[4:16].mean() > normal[4:16].mean()


def test_pool_backwash_visible_in_tank_drawdown(inputs):
    """The nightly DIN 19643 filter backwash (02:00) pulls a visible extra
    draw through the Hochbehälter — pinned on the tank's wire mdot."""
    pool_row = next(i for i, c in enumerate(inputs.consumers.consumers)
                    if c.kind == "pool")
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=SPD))
    prof = sim.profiles.mdot_kg_per_s
    # the pulse itself: backwash ticks tower over the pool's night base
    night_base = prof[pool_row, 0:6].mean()
    backwash = prof[pool_row, 8:12].max()
    assert backwash > 5 * night_base

    tank_mdot = []
    for t in range(16):                     # 00:00 – 04:00
        f = sim.run_step(t, 0)
        assert f.converged
        tank_mdot.append(f.tanks[0]["mdot_kg_per_s"])
    # tank balance during the backwash window is lower (more draw / less
    # charge) than in the quiet window before it, by about the pulse height
    quiet = float(np.mean(tank_mdot[0:6]))
    pulse = float(np.min(tank_mdot[8:12]))
    assert quiet - pulse > 0.15, (quiet, pulse)


def test_profiles_deterministic(inputs):
    a = build_demand_profiles(inputs, SPD)
    b = build_demand_profiles(inputs, SPD)
    assert np.array_equal(a, b)


def test_legacy_bundle_reproduces_m2_demands():
    """Mustertal carries no sizes: its profiles must be the M2 path
    bit-exactly (base × demand_factor staircase) — noise-free."""
    inputs = load_network(REPO_ROOT / "data" / "networks" / "mustertal")
    prof = build_demand_profiles(inputs, SPD)
    factor = np.asarray(inputs.environment.demand_factor, dtype=float)
    idx = (np.arange(SPD) * len(factor)) // SPD
    for i, c in enumerate(inputs.consumers.consumers):
        assert np.array_equal(prof[i], float(c.mdot_kg_per_s) * factor[idx])


def test_base_mean_preserved_on_normal_workday(inputs):
    """A residential consumer's normal-workday mean stays ≈ its base
    mdot (shape mean 1.0 × mild-day factors × noise mean 1.0)."""
    prof = build_demand_profiles(inputs, SPD)
    row = next(i for i, c in enumerate(inputs.consumers.consumers)
               if c.kind == "residential")
    base = inputs.consumers.consumers[row].mdot_kg_per_s
    assert prof[row].mean() == pytest.approx(base, rel=0.10)
    assert prof[row].min() > 0


# --- adversarial-review regression pins (M3) --------------------------------

def test_environment_rebuild_survives_consumer_crud(inputs):
    """THE M3 critical pin: after runtime consumer CRUD the rebuild maps
    engine rows BY IDENTITY — a positional write crashed (broadcast
    ValueError → 500) or silently shifted every later consumer onto its
    neighbour's archetype profile and overwrote runtime constants."""
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=SPD))
    sim.add_consumer(node="r5", mdot_kg_per_s=0.5, name="runtime_extra")
    victim_name = sim.index.consumer_names[3]
    sim.remove_consumer(int(sim.index.consumers[3]))

    sim.set_environment(t_offset_c=6.0, dryness_override=0.9)  # must not raise

    assert victim_name not in sim.index.consumer_names
    pos = sim.index.consumer_names.index("runtime_extra")
    assert np.allclose(sim.profiles.mdot_kg_per_s[pos], 0.5)  # stays constant
    # surviving bundle consumers keep their OWN archetype: the school's
    # profile is still school-shaped (mid-morning ≫ midnight)
    spos = sim.index.consumer_names.index("Grundschule Musterdorf")
    row = sim.profiles.mdot_kg_per_s[spos]
    assert row[40:48].mean() > 10 * row[0:8].mean()
    # and the frame still solves
    assert sim.run_step(0, 0).converged


def test_environment_route_survives_consumer_removal():
    """Never-500: DELETE /consumer then POST /environment stays 200."""
    with make_api_client() as client:
        client.post("/config/apply", json={"network_id": "musterdorf"})
        cid = client.get("/network").json()["consumers"][3]["id"]
        assert client.delete(f"/consumer/{cid}").status_code == 200
        r = client.post("/environment",
                        json={"t_offset_c": 6.0, "dryness": 0.9})
        assert r.status_code == 200, r.text


def test_reset_operations_normalizes_environment(inputs):
    """One doctrine for operator overrides: replay normalization resets
    the weather overrides exactly like station modes (the scenario recipe
    restores its own afterwards)."""
    sim = Simulator(inputs, make_settings(autostart=False, steps_per_day=SPD))
    baseline = sim.profiles.mdot_kg_per_s.copy()
    sim.set_environment(t_offset_c=6.0, dryness_override=0.9)
    assert not np.array_equal(sim.profiles.mdot_kg_per_s, baseline)
    sim.reset_operations()
    assert sim.environment.t_offset_c == 0.0
    assert sim.environment.dryness_override is None
    assert np.array_equal(sim.profiles.mdot_kg_per_s, baseline)


def test_recording_metadata_carries_environment(tmp_path):
    """The reproducibility recipe: a Hitzetag pack must record WHICH
    weather produced its demands (metadata.json environment block)."""
    import json

    with make_api_client(recordings_dir=tmp_path) as client:
        client.post("/config/apply", json={"network_id": "musterdorf"})
        client.post("/environment", json={"t_offset_c": 6.0, "dryness": 0.9})
        rid = client.post("/recording/start").json()["id"]
        client.post("/recording/stop")
        meta = json.loads(
            (tmp_path / rid / "metadata.json").read_text(encoding="utf-8"))
        assert meta["environment"] == {
            "t_offset_c": 6.0, "dryness_override": 0.9}


def test_topology_design_demand_is_spec_base():
    """GET /network Anschlusswert = the spec's MEAN base demand, never the
    tick-0 engine value (which bakes shape × noise — the school's midnight
    value is ~5 % of base)."""
    with make_api_client() as client:
        client.post("/config/apply", json={"network_id": "musterdorf"})
        cons = client.get("/network").json()["consumers"]
        school = next(c for c in cons
                      if c["name"] == "Grundschule Musterdorf")
        assert school["mdot_demand_kg_per_s"] == pytest.approx(0.10)


def test_environment_api_roundtrip():
    """GET/POST /environment: the Hitzetag override rebuilds the profiles
    and survives a scenario save/load."""
    with make_api_client() as client:
        client.post("/config/apply", json={"network_id": "musterdorf"})
        env = client.get("/environment").json()
        assert env["t_offset_c"] == 0.0
        assert env["n_profiled_consumers"] == 26

        r = client.post("/environment",
                        json={"t_offset_c": 6.0, "dryness": 0.9})
        assert r.status_code == 200
        assert r.json()["t_offset_c"] == 6.0
        assert r.json()["dryness_override"] == 0.9

        sid = client.post("/scenarios", json={"name": "hitzetag"}).json()["id"]
        client.post("/environment", json={"t_offset_c": 0.0,
                                          "clear_dryness": True})
        assert client.get("/environment").json()["dryness_override"] is None
        r = client.post(f"/scenarios/{sid}/load")
        assert r.status_code == 200
        env = client.get("/environment").json()
        assert env["t_offset_c"] == 6.0
        assert env["dryness_override"] == 0.9
        client.delete(f"/scenarios/{sid}")
