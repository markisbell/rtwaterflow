"""EPANET cross-validation of the pandapipes hydraulic core (M9, roadmap §7.2,
TF §10).

The pandapipes paper leaves the EPANET comparison open; this suite closes it for
rtwaterflow's engine. We rebuild the two canonical EPANET example networks —
**Net1** (9 junctions) and **Net3** (92 junctions) — that WNTR ships, solve them
with pandapipes' Darcy-Weisbach ``friction_model="swamee-jain"`` (the explicit
Colebrook approximation EPANET itself uses, and the platform's degraded tier),
and compare node pressures + link flows against WNTR's EpanetSimulator on the
IDENTICAL network. Agreement is well inside the roadmap bar (pressures within
0.05 bar; flows within ~1 %).

Scope — the FRICTION/PRESSURE field. Both example nets are rebuilt as GRAVITY
networks: every reservoir and tank is fixed at its EPANET head, and the pumps
are omitted (pandapipes models a pump as a constant-lift std_type outside the
Newton solve — a deliberate workaround, see network_builder.StationLiftStdType —
so a pump curve is not an apples-to-apples EPANET element; the pump+tank control
loop is cross-validated separately by tests/test_tank_oracle.py). What is
compared here is exactly what the roadmap asks for: the Darcy-Weisbach solve on a
real meshed topology, node for node, against the reference tool.
"""
from __future__ import annotations

import glob
import os
import tempfile

import pytest

wntr = pytest.importorskip("wntr")            # dev/CI dep; absent => skip, not fail
import pandapipes as pp                        # noqa: E402

from rtwaterflow.network_builder import BAR_PER_M, RHO_KG_M3   # noqa: E402

#: one uniform Darcy-Weisbach roughness on BOTH engines (mm). The absolute
#: value is immaterial to a cross-validation — only that both solve the SAME
#: D-W network; a mildly rough main keeps the friction gradient non-trivial.
K_MM = 1.0


def _pandapipes_bar_per_m() -> float:
    """pandapipes' OWN static pressure gradient (bar per metre of head) — read
    off a no-flow column. Used to convert EPANET's head-metres into bar in the
    SAME convention pandapipes reports, so the pressure comparison is the
    hydraulic head, not a ~0.1 % water-property offset between the two engines
    (pandapipes ~0.09780 vs the nominal RHO·g/1e5 = 0.09792 bar/m; M9 review)."""
    net = pp.create_empty_network(fluid="water")
    a = pp.create_junction(net, pn_bar=1.0, tfluid_k=293.15, height_m=100.0)
    b = pp.create_junction(net, pn_bar=1.0, tfluid_k=293.15, height_m=0.0)
    pp.create_ext_grid(net, junction=a, p_bar=0.0, type="p")
    pp.create_pipe_from_parameters(net, a, b, length_km=0.01,
                                   inner_diameter_mm=300.0, k_mm=0.1)
    pp.pipeflow(net, mode="hydraulics", friction_model="swamee-jain")
    return float(net.res_junction.p_bar.loc[b]) / 100.0


#: pandapipes' convention, computed once (≈ 0.09780 bar/m)
PP_BAR_PER_M = _pandapipes_bar_per_m()


def _inp(name: str) -> str:
    hits = glob.glob(os.path.join(os.path.dirname(wntr.__file__), "**",
                                  f"{name}.inp"), recursive=True)
    if not hits:
        pytest.skip(f"WNTR did not ship {name}.inp")
    return hits[0]


def _load_spec(name: str):
    """(junctions{name:(elev,demand)}, sources{name:head_m}, pipes[...]) from
    the EPANET .inp — reservoirs + tanks become fixed heads, pumps dropped."""
    wn = wntr.network.WaterNetworkModel(_inp(name))
    junctions = {n: (wn.get_node(n).elevation, wn.get_node(n).base_demand or 0.0)
                 for n in wn.junction_name_list}
    sources: dict[str, float] = {}
    for r in wn.reservoir_name_list:
        sources[r] = wn.get_node(r).base_head
    for t in wn.tank_name_list:
        tk = wn.get_node(t)
        sources[t] = tk.elevation + tk.init_level      # fixed water surface
    pipes = [(p, wn.get_link(p).start_node_name, wn.get_link(p).end_node_name,
              wn.get_link(p).length, wn.get_link(p).diameter)
             for p in wn.pipe_name_list]
    return junctions, sources, pipes


def _wntr_reference(spec, k_mm=K_MM):
    junctions, sources, pipes = spec
    wn = wntr.network.WaterNetworkModel()
    wn.options.hydraulic.headloss = "D-W"              # NOT Hazen-Williams
    wn.options.time.duration = 0                       # steady snapshot
    for s, head in sources.items():
        wn.add_reservoir(s, base_head=head)
    for n, (elev, dem) in junctions.items():
        wn.add_junction(n, base_demand=dem, elevation=elev)
    for name, a, b, length, diam in pipes:
        # WNTR stores D-W roughness in METRES (written as mm to the metric INP)
        wn.add_pipe(name, a, b, length=length, diameter=diam,
                    roughness=k_mm / 1000.0, minor_loss=0.0)
    # file_prefix into a throwaway dir — else EPANET writes temp.inp/rpt/bin
    # into the CWD (the repo root); auto-cleaned here
    with tempfile.TemporaryDirectory() as td:
        res = wntr.sim.EpanetSimulator(wn).run_sim(
            file_prefix=os.path.join(td, "epanet"))
        pressures = {n: float(res.node["pressure"].loc[0, n]) for n in junctions}
        flows = {p: float(res.link["flowrate"].loc[0, p])
                 for p in wn.pipe_name_list}
    return pressures, flows


def _pandapipes_solution(spec, k_mm=K_MM, friction="swamee-jain"):
    junctions, sources, pipes = spec
    net = pp.create_empty_network(fluid="water")
    j = {}
    for n, (elev, dem) in junctions.items():
        j[n] = pp.create_junction(net, pn_bar=5.0, tfluid_k=293.15,
                                  height_m=elev, name=n)
    for s, head in sources.items():
        # a fixed head is a junction AT that head with 0 bar gauge (ext_grid p)
        j[s] = pp.create_junction(net, pn_bar=3.0, tfluid_k=293.15,
                                  height_m=head, name=s)
        pp.create_ext_grid(net, junction=j[s], p_bar=0.0, type="p")
    for name, a, b, length, diam in pipes:
        pp.create_pipe_from_parameters(net, j[a], j[b], length_km=length / 1000.0,
                                       inner_diameter_mm=diam * 1000.0,
                                       k_mm=k_mm, name=name)
    for n, (elev, dem) in junctions.items():
        if dem > 0:
            pp.create_sink(net, j[n], mdot_kg_per_s=dem * RHO_KG_M3, name=n)
    pp.pipeflow(net, mode="hydraulics", friction_model=friction, max_iter_hyd=400)
    pressures = {n: float(net.res_junction.p_bar.loc[j[n]]) for n in junctions}
    flows = {}
    for i, (name, a, b, length, diam) in enumerate(pipes):
        flows[name] = float(net.res_pipe.mdot_from_kg_per_s.loc[i]) / RHO_KG_M3
    return net, pressures, flows


NETS = [("Net1", 9, 12), ("Net3", 92, 117)]


@pytest.mark.parametrize("name,n_junc,n_pipe", NETS)
def test_pandapipes_pressures_match_epanet(name, n_junc, n_pipe):
    """Every junction pressure within 0.05 bar of EPANET (roadmap §7.2). This is
    a whole-solve check — hydrostatics + the D-W pressure field — and it is
    friction-model-INSENSITIVE by nature (on these high-Re nets even nikuradse
    lands ~0.009 bar off), so it proves the solve is right but does NOT by
    itself pin the friction model; that is what the FLOW test below discriminates
    (see it and test_swamee_jain_… for the friction-model fidelity)."""
    spec = _load_spec(name)
    assert len(spec[0]) == n_junc and len(spec[2]) == n_pipe   # right net loaded
    p_ep, _ = _wntr_reference(spec)
    net, p_pp, _ = _pandapipes_solution(spec)
    assert bool(net.converged)
    # convert EPANET head-metres with pandapipes' OWN gradient → like-for-like
    worst = max(abs(p_pp[n] - p_ep[n] * PP_BAR_PER_M) for n in spec[0])
    assert worst < 0.05, f"{name}: worst |Δp| = {worst:.4f} bar vs EPANET"


@pytest.mark.parametrize("name,n_junc,n_pipe", NETS)
def test_pandapipes_flows_match_epanet(name, n_junc, n_pipe):
    """Every meaningfully-loaded pipe's flow within 1.5 % of EPANET. Flows are
    the FRICTION-MODEL discriminator (the pressure field is hydrostatics-
    dominated): a wrong friction model shows here first — on Net3 swamee-jain is
    ~0.5 % but nikuradse is ~14 % (asserted in test_swamee_jain_…)."""
    spec = _load_spec(name)
    f_ep = _wntr_reference(spec)[1]
    f_pp = _pandapipes_solution(spec)[2]
    rels = [abs(abs(f_pp[p]) - abs(f_ep[p])) / abs(f_ep[p])
            for p in f_ep if abs(f_ep[p]) * 3600 > 5.0]      # > 5 m³/h pipes
    assert rels, f"{name}: no meaningfully-loaded pipes to compare"
    assert max(rels) < 0.015, f"{name}: worst flow error {max(rels)*100:.2f} %"


def _worst_flow_error(spec, friction):
    """Worst relative flow error vs EPANET on >5 m³/h pipes, for *friction*."""
    f_ep = _wntr_reference(spec)[1]
    f_pp = _pandapipes_solution(spec, friction=friction)[2]
    return max(abs(abs(f_pp[p]) - abs(f_ep[p])) / abs(f_ep[p])
               for p in f_ep if abs(f_ep[p]) * 3600 > 5.0)


def test_swamee_jain_underscore_silently_falls_back_and_botches_flows():
    """The §8 pitfall, shown to be MATERIALLY harmful, not cosmetic:
    ``friction_model="swamee-jain"`` (HYPHEN) is the explicit Colebrook
    approximation EPANET uses; the common typo ``"swamee_jain"`` (underscore) is
    not a recognised model and pandapipes SILENTLY falls back to nikuradse
    (upstream #803, laminar + turbulent λ added instead of regime-selected).

    Two things are proven on Net3: (1) the silent FALLBACK — the underscore
    solve is bit-identical to the nikuradse solve; (2) the HARM — the fallback
    reproduces EPANET *pressures* just fine (hydrostatics-dominated, so the
    pressure test cannot catch it), but its *flows* are ~14 % off, blowing the
    1.5 % flow bar the correct hyphen model (~0.5 %) passes. So the typo yields a
    quietly wrong hydraulic answer that only the flow metric exposes."""
    spec = _load_spec("Net3")
    _, p_hyphen, _ = _pandapipes_solution(spec, friction="swamee-jain")
    _, p_under, _ = _pandapipes_solution(spec, friction="swamee_jain")
    _, p_nik, _ = _pandapipes_solution(spec, friction="nikuradse")
    # (1) the underscore typo IS nikuradse — bit-identical (the silent fallback)
    assert max(abs(p_under[n] - p_nik[n]) for n in spec[0]) < 1e-9
    assert max(abs(p_hyphen[n] - p_nik[n]) for n in spec[0]) > 1e-6   # ≠ hyphen
    # (2) the harm is in the FLOWS, not the pressures:
    assert _worst_flow_error(spec, "swamee-jain") < 0.015     # correct: passes
    assert _worst_flow_error(spec, "swamee_jain") > 0.05      # fallback: fails


def test_pda_curve_matches_wntr_pressure_driven_demand():
    """The M5 pressure-driven-demand (Wagner) curve reproduces WNTR's own PDD
    model (roadmap §7.2 'PDA behavior vs WNTR's Wagner PDD'). WNTR is the oracle:
    a tiny demand on a near-frictionless stub is held at a series of node
    pressures across the band, and its EPANET-PDD delivered FRACTION is compared
    to PDAController.factor at that pressure. The coupled starved-net fixed point
    is separately cross-validated by the emitter oracle (test_hydrant_oracle)."""
    from rtwaterflow.hydraulics.pda import PDAController

    p_min, p_req, base = 0.5, 2.7, 5e-4
    pda = PDAController(p_min_bar=p_min)
    worst = 0.0
    for p_target_bar in (0.6, 1.0, 1.5, 2.0, 2.5, 2.7, 3.5):
        wn = wntr.network.WaterNetworkModel()
        wn.options.hydraulic.headloss = "D-W"
        wn.options.hydraulic.demand_model = "PDD"
        wn.options.hydraulic.required_pressure = p_req / BAR_PER_M
        wn.options.hydraulic.minimum_pressure = p_min / BAR_PER_M
        wn.options.time.duration = 0
        wn.add_reservoir("r", base_head=p_target_bar / BAR_PER_M)
        wn.add_junction("b", base_demand=base, elevation=0.0)
        wn.add_pipe("p", "r", "b", length=1.0, diameter=1.0,   # ~frictionless
                    roughness=1e-6, minor_loss=0.0)
        with tempfile.TemporaryDirectory() as td:
            res = wntr.sim.EpanetSimulator(wn).run_sim(
                file_prefix=os.path.join(td, "epanet"))
            frac_wntr = float(res.node["demand"].loc[0, "b"]) / base
            p_node = float(res.node["pressure"].loc[0, "b"]) * BAR_PER_M
        worst = max(worst, abs(frac_wntr - pda.factor(p_node, p_req)))
    assert worst < 0.01, f"PDA vs WNTR PDD worst fraction gap = {worst:.4f}"
