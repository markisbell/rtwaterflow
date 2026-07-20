"""Five-file water contract validation: schema rules + cross-document checks."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from rtwaterflow.data_loader import DataContractError, cross_validate, load_network
from rtwaterflow.models import (
    ConsumerSpec,
    ConsumersFile,
    NetworkStructure,
    PipeSpec,
    SupplyFile,
)
from rtwaterflow.net_inputs import NetInputs

from conftest import HILLSIDE_DIR


def _rebuild(docs) -> NetInputs:
    """Validate mutated fixture documents through the full contract path."""
    from rtwaterflow.models import EnvironmentFile, PipesFile

    inputs = NetInputs(
        name=docs["network_structure"]["name"],
        structure=NetworkStructure.model_validate(docs["network_structure"]),
        pipes=PipesFile.model_validate(docs["pipes"]),
        consumers=ConsumersFile.model_validate(docs["consumers"]),
        supply=SupplyFile.model_validate(docs["supply"]),
        environment=EnvironmentFile.model_validate(docs["environment"]),
    )
    cross_validate(inputs)
    return inputs


def test_fixture_loads_clean():
    inputs = load_network(HILLSIDE_DIR)
    assert inputs.name == "Tutorial Hanglage"
    assert inputs.n_days == 1
    assert len(inputs.consumers.consumers) == 2


# --- schema rules -----------------------------------------------------------

def test_consumer_needs_positive_demand():
    with pytest.raises(ValidationError):
        ConsumerSpec.model_validate(dict(node="j1", mdot_kg_per_s=0.0))
    with pytest.raises(ValidationError):
        ConsumerSpec.model_validate(dict(node="j1", mdot_kg_per_s=-1.0))


def test_consumer_rejects_thermal_fields():
    """The heat-era fields must be gone (extra='forbid' guards the contract)."""
    with pytest.raises(ValidationError):
        ConsumerSpec.model_validate(dict(
            node="j1", mdot_kg_per_s=0.1, q_sh_w=[1000.0]))


def test_pipe_needs_diameter():
    with pytest.raises(ValidationError):
        PipeSpec.model_validate(dict(from_node="a", to_node="b",
                                     length_km=0.1))


# --- pipe catalog (M1): dn+material path -------------------------------------

def test_pipe_catalog_resolves_dn_material():
    p = PipeSpec.model_validate(dict(from_node="a", to_node="b",
                                     length_km=0.1, dn=100, material="GGG"))
    assert p.inner_diameter_mm == 100.0   # metallic: ID ~ DN
    assert p.k_mm == 0.4                  # GW 303-1 default for GGG
    pe = PipeSpec.model_validate(dict(from_node="a", to_node="b",
                                      length_km=0.1, dn=110, material="PE"))
    assert pe.inner_diameter_mm == pytest.approx(96.8)  # SDR 17 bore
    assert pe.k_mm == 0.1
    gg = PipeSpec.model_validate(dict(from_node="a", to_node="b",
                                      length_km=0.1, dn=125, material="GG"))
    assert gg.k_mm == 1.0                 # old grey cast iron


def test_pipe_catalog_rejects_bad_combinations():
    with pytest.raises(ValidationError, match="not both"):
        PipeSpec.model_validate(dict(from_node="a", to_node="b",
                                     length_km=0.1, dn=100, material="GGG",
                                     inner_diameter_mm=100))
    with pytest.raises(ValidationError, match="belong together"):
        PipeSpec.model_validate(dict(from_node="a", to_node="b",
                                     length_km=0.1, dn=100))
    with pytest.raises(ValidationError, match="SDR 17"):
        PipeSpec.model_validate(dict(from_node="a", to_node="b",
                                     length_km=0.1, dn=100, material="PE"))


def test_pipe_explicit_k_beats_material_default():
    p = PipeSpec.model_validate(dict(from_node="a", to_node="b",
                                     length_km=0.1, dn=100, material="GGG",
                                     k_mm=0.7))
    assert p.k_mm == 0.7


def test_pipe_length_from_geometry():
    # ~111 m of northing at constant longitude (0.001 deg latitude)
    p = PipeSpec.model_validate(dict(
        from_node="a", to_node="b", dn=100, material="GGG",
        geometry=[[49.45, 9.0], [49.451, 9.0]]))
    assert p.length_km == pytest.approx(0.1112, abs=0.001)
    with pytest.raises(ValidationError, match="geometry polyline"):
        PipeSpec.model_validate(dict(from_node="a", to_node="b",
                                     dn=100, material="GGG"))


# --- PRVs (M1) ---------------------------------------------------------------

def test_reversed_geometry_rejected(hillside_docs):
    """The polyline is load-bearing (derived length + arrow orientation):
    ends must anchor to the from/to nodes (M1 review finding)."""
    docs = hillside_docs
    # digitized the wrong way round: geometry runs to->from
    docs["pipes"]["pipes"][0]["geometry"] = [
        docs["network_structure"]["junctions"][1]["geo"],
        docs["network_structure"]["junctions"][0]["geo"],
    ]
    with pytest.raises(DataContractError, match="anchor"):
        _rebuild(docs)


def test_bypassed_prv_rejected(hillside_docs):
    """A pipe path around a PRV lets the low zone see two heads — the zone
    boundary must be a cut edge (M1 review finding)."""
    docs = hillside_docs
    docs["network_structure"]["junctions"] += [
        {"name": "z_in", "kind": "node", "geo": [49.468, 8.986],
         "elevation_m": 345.0, "pn_bar": 1.0},
        {"name": "z_out", "kind": "node", "geo": [49.4681, 8.9861],
         "elevation_m": 345.0, "pn_bar": 1.0},
    ]
    docs["pipes"]["pipes"] += [
        {"from_node": "j1", "to_node": "z_in", "length_km": 0.2,
         "inner_diameter_mm": 150},
        # the bypass: a plain pipe parallel to the PRV
        {"from_node": "z_in", "to_node": "z_out", "length_km": 0.05,
         "inner_diameter_mm": 100},
    ]
    docs["supply"]["prvs"] = [
        {"from_node": "z_in", "to_node": "z_out", "p_out_bar": 3.0}]
    with pytest.raises(DataContractError, match="bypasses"):
        _rebuild(docs)


def test_prv_unknown_node_rejected(hillside_docs):
    docs = hillside_docs
    docs["supply"]["prvs"] = [
        {"from_node": "j1", "to_node": "nirgendwo", "p_out_bar": 3.0}]
    with pytest.raises(DataContractError, match="unknown node"):
        _rebuild(docs)


def test_zone_behind_prv_is_reachable(hillside_docs):
    """A zone fed ONLY through a PRV counts as reachable (the PRV is a
    branch element — the M1 Musterdorf pattern)."""
    docs = hillside_docs
    docs["network_structure"]["junctions"] += [
        {"name": "z_in", "kind": "node", "geo": [49.468, 8.986],
         "elevation_m": 345.0, "pn_bar": 1.0},
        {"name": "z_out", "kind": "node", "geo": [49.4681, 8.9861],
         "elevation_m": 345.0, "pn_bar": 1.0},
        {"name": "z_low", "kind": "consumer", "geo": [49.469, 8.987],
         "elevation_m": 320.0, "pn_bar": 1.0},
    ]
    docs["pipes"]["pipes"] += [
        {"from_node": "j1", "to_node": "z_in", "length_km": 0.2,
         "inner_diameter_mm": 150},
        {"from_node": "z_out", "to_node": "z_low", "length_km": 0.3,
         "inner_diameter_mm": 100},
    ]
    docs["supply"]["prvs"] = [
        {"from_node": "z_in", "to_node": "z_out", "p_out_bar": 3.0}]
    docs["consumers"]["consumers"].append(
        {"node": "z_low", "name": "Tiefzone", "mdot_kg_per_s": 0.1})
    inputs = _rebuild(docs)  # must NOT raise (reachable through the PRV)
    assert len(inputs.supply.prvs) == 1


def test_pipe_rejects_self_loop():
    with pytest.raises(ValidationError, match="self-loop"):
        PipeSpec.model_validate(dict(from_node="a", to_node="a",
                                     length_km=0.1, inner_diameter_mm=100))


def test_pipe_rejects_std_type():
    """The heat-era ISOPLUS std_type path is gone from the water contract."""
    with pytest.raises(ValidationError):
        PipeSpec.model_validate(dict(
            from_node="a", to_node="b", length_km=0.1,
            std_type="ISOPLUS_DRE100_STD"))


def test_environment_length_check():
    from rtwaterflow.models import EnvironmentFile
    with pytest.raises(ValidationError, match="length"):
        EnvironmentFile.model_validate(dict(
            resolution_minutes=15, steps=96, t_air_c=[10.0] * 10))


# --- exactly one slack (single head source in M0) ---------------------------

def test_second_slack_rejected(hillside_docs):
    docs = hillside_docs
    docs["supply"]["supplies"].append({
        "node": "j1", "kind": "ext_grid", "p_bar": 3.0})
    with pytest.raises(ValidationError, match="exactly one slack"):
        _rebuild(docs)


def test_no_slack_rejected(hillside_docs):
    docs = hillside_docs
    docs["supply"]["supplies"] = []
    with pytest.raises(ValidationError):
        _rebuild(docs)


# --- cross-document checks ---------------------------------------------------

def test_unknown_node_rejected(hillside_docs):
    docs = hillside_docs
    docs["consumers"]["consumers"][0]["node"] = "nope"
    with pytest.raises(DataContractError, match="unknown node"):
        _rebuild(docs)


def test_partial_day_horizon_rejected(hillside_docs):
    docs = hillside_docs
    docs["environment"]["steps"] = 48
    docs["environment"]["t_air_c"] = [10.0] * 48
    with pytest.raises(DataContractError, match="whole number of days"):
        _rebuild(docs)


def test_unreachable_consumer_rejected(hillside_docs):
    docs = hillside_docs
    # island: j6/j7 connected to each other but not to the slack's component
    docs["network_structure"]["junctions"] += [
        {"name": "j6", "kind": "consumer", "geo": [49.47, 8.99],
         "elevation_m": 350.0, "pn_bar": 1.0},
        {"name": "j7", "kind": "node", "geo": [49.471, 8.991],
         "elevation_m": 351.0, "pn_bar": 1.0},
    ]
    docs["pipes"]["pipes"].append({
        "from_node": "j6", "to_node": "j7",
        "length_km": 0.1, "inner_diameter_mm": 100})
    docs["consumers"]["consumers"].append({
        "node": "j6", "name": "island consumer", "mdot_kg_per_s": 0.1})
    with pytest.raises(DataContractError, match="not reachable"):
        _rebuild(docs)


def test_dead_end_without_consumer_is_legal(hillside_docs):
    """Hydraulics-only dead ends are LEGAL (stagnation is an M4 compliance
    finding, not a solver singularity like in the thermal fork parent)."""
    docs = hillside_docs
    docs["network_structure"]["junctions"].append(
        {"name": "j8", "kind": "node", "geo": [49.468, 8.985],
         "elevation_m": 355.0, "pn_bar": 1.0})
    docs["pipes"]["pipes"].append({
        "from_node": "j1", "to_node": "j8",
        "length_km": 0.05, "inner_diameter_mm": 100})
    inputs = _rebuild(docs)  # must NOT raise
    assert any(j.name == "j8" for j in inputs.structure.junctions)


def test_isolated_node_rejected(hillside_docs):
    docs = hillside_docs
    docs["network_structure"]["junctions"].append(
        {"name": "lonely", "kind": "node", "geo": [49.48, 9.0],
         "elevation_m": 360.0, "pn_bar": 1.0})
    with pytest.raises(DataContractError, match="isolated"):
        _rebuild(docs)
