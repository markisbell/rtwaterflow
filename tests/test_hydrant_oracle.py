"""EPANET oracle (WNTR) for the M5 hydrant emitter — roadmap M5 accept:
"hydrant delivers target flow, pass/fail vs 1.5 bar rule matches an EPANET
reference run within 0.1 bar".

The SAME single-zone net (reservoir → main → hydrant node) is built in the
platform and in EPANET, with the hydrant modelled as an emitter in both
(mine: ``mdot = C·p_bar^0.5``; EPANET: ``Q = C_e·head^0.5``, exponent 0.5).
The coefficient converts as ``C_e = (C/ρ)·(ρg/1e5)^0.5``. Compared: the
hydrant node pressure — and hence the pass/fail against the W 405 1.5 bar
flow-pressure rule.
"""
from __future__ import annotations

import pandapipes as pp
import pytest

wntr = pytest.importorskip("wntr")

from rtwaterflow.hydraulics.emitters import EmitterController  # noqa: E402
from rtwaterflow.simulator import solve_with_retry  # noqa: E402

RHO = 998.2
G = 9.81
BAR_PER_M = RHO * G / 1e5


def _mine(res_head_m: float, source_bar: float, length_km: float,
          dn_mm: float, c_mine: float) -> tuple[float, float]:
    """Reservoir at *source_bar* over a flat main to a hydrant node; run
    the emitter fixed point; return (node pressure bar, hydrant Q m³/h)."""
    net = pp.create_empty_network(fluid="water")
    a = pp.create_junction(net, pn_bar=source_bar, tfluid_k=293.15,
                           height_m=res_head_m)
    b = pp.create_junction(net, pn_bar=source_bar, tfluid_k=293.15,
                           height_m=res_head_m)
    pp.create_pipe_from_parameters(net, a, b, length_km=length_km,
                                   inner_diameter_mm=dn_mm, k_mm=0.1)
    pp.create_ext_grid(net, junction=a, p_bar=source_bar, type="p")
    em = pp.create_sink(net, junction=b, mdot_kg_per_s=c_mine)  # nonzero seed
    for _ in range(40):
        out = solve_with_retry(net, 100)
        assert out.converged
        p = float(net.res_junction.p_bar.iloc[1])
        target = c_mine * max(p, 0.0) ** 0.5
        cur = float(net.sink.at[em, "mdot_kg_per_s"])
        net.sink.at[em, "mdot_kg_per_s"] = cur + 0.4 * (target - cur)
        if abs(target - cur) < 1e-4:
            break
    p_bar = float(net.res_junction.p_bar.iloc[1])
    q_m3h = float(net.res_sink.mdot_kg_per_s.iloc[0]) / RHO * 3600.0
    return p_bar, q_m3h


def _epanet(res_head_m: float, source_bar: float, length_km: float,
            dn_mm: float, c_mine: float, prefix: str) -> float:
    wn = wntr.network.WaterNetworkModel()
    wn.options.hydraulic.headloss = "D-W"
    wn.options.hydraulic.emitter_exponent = 0.5
    wn.add_reservoir("r", base_head=res_head_m + source_bar / BAR_PER_M)
    wn.add_junction("b", base_demand=0.0, elevation=res_head_m)
    wn.add_pipe("p", "r", "b", length=length_km * 1000.0,
                diameter=dn_mm / 1000.0, roughness=0.1 / 1000.0,
                minor_loss=0.0)
    wn.get_node("b").emitter_coefficient = (c_mine / RHO) * BAR_PER_M ** 0.5
    # write EPANET's .inp/.rpt/.bin into the pytest tmp dir, never the repo
    res = wntr.sim.EpanetSimulator(wn).run_sim(file_prefix=prefix)
    return float(res.node["pressure"]["b"].iloc[0]) * BAR_PER_M


@pytest.mark.parametrize(
    "source_bar,length_km,dn_mm,c_mine",
    [
        (2.0, 0.5, 150.0, 5.0),    # comfortable — passes ≥ 1.5 bar
        (1.6, 0.8, 150.0, 8.0),    # marginal — near the 1.5 bar rule
        (1.2, 1.0, 100.0, 6.0),    # fails the rule (crater)
    ])
def test_hydrant_pressure_matches_epanet(source_bar, length_km, dn_mm, c_mine,
                                         tmp_path):
    p_mine, q_mine = _mine(100.0, source_bar, length_km, dn_mm, c_mine)
    p_epanet = _epanet(100.0, source_bar, length_km, dn_mm, c_mine,
                       str(tmp_path / "hydrant_oracle"))
    assert p_mine == pytest.approx(p_epanet, abs=0.1), (p_mine, p_epanet)
    # the pass/fail verdict against the W 405 1.5 bar rule agrees
    assert (p_mine >= 1.5) == (p_epanet >= 1.5)
    assert q_mine > 0
