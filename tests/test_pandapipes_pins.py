"""Regression pins on pandapipes 0.14.0 behavior the water platform depends
on. Every pin was runtime-verified on the pinned version (project convention:
pins are re-derived, never copied from docs)."""
from __future__ import annotations

import numpy as np
import pandapipes as pp
import pytest

from rtwaterflow.network_builder import build_network


def _mini_net():
    net = pp.create_empty_network(fluid="water")
    a = pp.create_junction(net, pn_bar=3, tfluid_k=293.15, height_m=100.0)
    b = pp.create_junction(net, pn_bar=3, tfluid_k=293.15, height_m=90.0)
    pp.create_pipe_from_parameters(net, a, b, length_km=0.1,
                                   inner_diameter_mm=100, k_mm=0.1)
    pp.create_ext_grid(net, junction=a, p_bar=3.0, type="p")
    pp.create_sink(net, junction=b, mdot_kg_per_s=1.0)
    return net


def test_hydraulics_mode_with_colebrook_accepted():
    """mode='hydraulics' + friction_model='colebrook' is the platform solve;
    it must converge and populate res_junction/res_pipe/res_sink/res_ext_grid."""
    net = _mini_net()
    pp.pipeflow(net, mode="hydraulics", friction_model="colebrook")
    assert net.converged
    for table in ("res_junction", "res_pipe", "res_sink", "res_ext_grid"):
        assert len(net[table])


def test_height_m_drives_elevation_pressure():
    """junction.height_m feeds the hydrostatic term: 10 m of drop adds
    ~0.98 bar at near-zero flow. junction_geodata has NO hydraulic effect."""
    net = _mini_net()
    net.sink.at[0, "mdot_kg_per_s"] = 0.001  # ~zero friction
    pp.pipeflow(net, mode="hydraulics", friction_model="colebrook")
    dp = net.res_junction.p_bar.iloc[1] - net.res_junction.p_bar.iloc[0]
    assert dp == pytest.approx(0.0981 * 10.0, abs=0.01)


def test_ext_grid_reports_withdrawal_negative():
    """Sign convention pin: an ext_grid FEEDING the net reports negative
    mdot_kg_per_s (flow out of the grid). The wire carries the magnitude."""
    net = _mini_net()
    pp.pipeflow(net, mode="hydraulics", friction_model="colebrook")
    assert net.res_ext_grid.mdot_kg_per_s.iloc[0] == pytest.approx(-1.0,
                                                                   rel=1e-3)


def test_swamee_jain_spelling_is_hyphenated():
    """The third friction model's string literal is 'swamee-jain' (HYPHEN).
    'swamee_jain' silently falls through to nikuradse in 0.14.0 — pin the
    accepted spelling so the future EPANET cross-validation uses it right."""
    net = _mini_net()
    pp.pipeflow(net, mode="hydraulics", friction_model="swamee-jain")
    assert net.converged


def test_builder_single_layer_no_thermal_columns(hillside_inputs):
    """The water builder creates ONE junction per node with height_m set and
    passes no thermal pipe parameters (text_k stays at the signature default
    — it is inert in hydraulics mode)."""
    net, profiles = build_network(hillside_inputs)
    assert len(net.junction) == 5          # no _s/_r pair doubling
    assert len(net.pipe) == 4
    assert np.allclose(
        sorted(net.junction.height_m.to_numpy()),
        sorted([352.0, 358.0, 361.0, 346.0, 400.0]))
    assert len(net.ext_grid) == 1
    assert net.ext_grid.at[0, "p_bar"] == pytest.approx(0.5)
    assert len(net.sink) == 2
    # roughness explicit per pipe (never the library default)
    assert np.allclose(sorted(net.pipe.k_mm.to_numpy()),
                       sorted([0.2, 0.2, 0.2, 0.5]))
