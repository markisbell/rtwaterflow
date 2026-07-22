"""Load and cross-validate the five-file water data contract (roadmap §3).

Per-document schema validation lives in :mod:`rtwaterflow.models`; this module
adds the **cross-document** checks:

* node references valid (pipes, consumers, supplies, prvs, tanks, stations),
* at least one head source (already enforced per-file, re-checked here),
* every consumer and head source reachable over the pipe graph (PRV and
  pump-station branches count as edges),
* no isolated nodes (degree 0 — a junction without any pipe cannot solve),
* the environment horizon covers a whole number of days.

Dead ends (degree 1 without a consumer) are **legal** in hydraulics-only
water networks — stagnation is an operational finding (M4 compliance
engine), not a solver singularity like in the thermal fork parent.

All violations raise :class:`DataContractError` with every finding listed.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

from .models import (
    ConsumersFile,
    EnvironmentFile,
    NetworkStructure,
    PipesFile,
    SupplyFile,
    _haversine_km,
)
from .net_inputs import NetInputs

#: geometry endpoints must anchor to their node coordinates within this
#: distance — the polyline is load-bearing (derived pipe length + flow-arrow
#: orientation), so a reversed or mis-anchored digitization must fail loudly
GEOMETRY_ANCHOR_KM = 0.05

log = logging.getLogger(__name__)

FILE_NAMES = {
    "structure": "network_structure.json",
    "pipes": "pipes.json",
    "consumers": "consumers.json",
    "supply": "supply.json",
    "environment": "environment.json",
}


class DataContractError(ValueError):
    """A five-file bundle violated the data contract."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("data contract violated:\n- " + "\n- ".join(errors))


def _read_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_network_from_docs(docs: dict) -> NetInputs:
    """Validate + cross-validate an in-memory five-file bundle (the same
    contract as :func:`load_network`, no disk I/O). Keys: ``network_structure
    / pipes / consumers / supply / environment``. Used by the M8 editor's
    load-case check on the network it is building."""
    try:
        structure = NetworkStructure.model_validate(docs["network_structure"])
        pipes = PipesFile.model_validate(docs["pipes"])
        consumers = ConsumersFile.model_validate(docs["consumers"])
        supply = SupplyFile.model_validate(docs["supply"])
        environment = EnvironmentFile.model_validate(docs["environment"])
    except KeyError as exc:
        raise DataContractError([f"missing bundle document: {exc.args[0]}"])
    inputs = NetInputs(
        name=structure.name, structure=structure, pipes=pipes,
        consumers=consumers, supply=supply, environment=environment)
    cross_validate(inputs)
    return inputs


def load_network(directory: str | Path) -> NetInputs:
    """Load the five files from *directory*, validate, cross-validate."""
    d = Path(directory)
    missing = [n for n in FILE_NAMES.values() if not (d / n).is_file()]
    if missing:
        raise DataContractError([f"missing file(s) in {d}: {missing}"])
    return load_network_from_docs({
        "network_structure": _read_json(d / FILE_NAMES["structure"]),
        "pipes": _read_json(d / FILE_NAMES["pipes"]),
        "consumers": _read_json(d / FILE_NAMES["consumers"]),
        "supply": _read_json(d / FILE_NAMES["supply"]),
        "environment": _read_json(d / FILE_NAMES["environment"]),
    })


def cross_validate(inputs: NetInputs) -> None:
    """Raise :class:`DataContractError` on any cross-document violation."""
    errors: list[str] = []
    nodes = {j.name for j in inputs.structure.junctions}

    # --- node references ---
    for p in inputs.pipes.pipes:
        for ref in (p.from_node, p.to_node):
            if ref not in nodes:
                errors.append(f"pipe {p.from_node}->{p.to_node}: unknown node {ref!r}")
    head_source_nodes = ({s.node for s in inputs.supply.supplies}
                         | {t.node for t in inputs.supply.tanks})
    # consumer names are load-bearing keys (compliance counters, profile
    # identity, meter replay) — duplicates silently merged state (M4 review)
    cnames = [c.name or f"consumer_{c.node}" for c in inputs.consumers.consumers]
    cdupes = {n for n in cnames if cnames.count(n) > 1}
    if cdupes:
        errors.append(
            f"duplicate consumer name(s) {sorted(cdupes)} — names must be "
            "unique (they key compliance counters and meter replay)")
    for c in inputs.consumers.consumers:
        if c.node not in nodes:
            errors.append(f"consumer {c.name or c.node!r}: unknown node {c.node!r}")
        elif c.node in head_source_nodes:
            # a sink ON the fixed-pressure junction is served straight from
            # the boundary and pins the Schlechtpunkt KPI to the source's
            # low gauge pressure forever (M2 review finding)
            errors.append(
                f"consumer {c.name or c.node!r} sits on head-source node "
                f"{c.node!r} — demands must attach to network nodes, not "
                "to the fixed-pressure boundary itself")
    for s in inputs.supply.supplies:
        if s.node not in nodes:
            errors.append(f"supply {s.name or s.kind}: unknown node {s.node!r}")
    for v in inputs.supply.prvs:
        for ref in (v.from_node, v.to_node):
            if ref not in nodes:
                errors.append(f"prv {v.from_node}->{v.to_node}: unknown node {ref!r}")
    tank_names = set()
    for tk in inputs.supply.tanks:
        if tk.node not in nodes:
            errors.append(f"tank {tk.name or tk.node!r}: unknown node {tk.node!r}")
        tank_names.add(tk.name or f"tank_{tk.node}")
    for st in inputs.supply.stations:
        for ref in (st.from_node, st.to_node):
            if ref not in nodes:
                errors.append(
                    f"station {st.from_node}->{st.to_node}: unknown node {ref!r}")
        if st.control.mode == "hysteresis" and st.control.tank not in tank_names:
            errors.append(
                f"station {st.name or st.from_node}: hysteresis control "
                f"references unknown tank {st.control.tank!r}")

    # --- geometry anchoring: the polyline is physics input (derived length,
    # arrow orientation) — its ends must sit on the from/to nodes ---
    node_geo = {j.name: j.geo for j in inputs.structure.junctions}
    for p in inputs.pipes.pipes:
        if not p.geometry or p.from_node not in node_geo \
                or p.to_node not in node_geo:
            continue
        d_from = _haversine_km(tuple(p.geometry[0]), node_geo[p.from_node])
        d_to = _haversine_km(tuple(p.geometry[-1]), node_geo[p.to_node])
        if d_from > GEOMETRY_ANCHOR_KM or d_to > GEOMETRY_ANCHOR_KM:
            errors.append(
                f"pipe {p.from_node}->{p.to_node}: geometry ends do not "
                f"anchor to the from/to nodes (from {d_from * 1000:.0f} m, "
                f"to {d_to * 1000:.0f} m away; limit "
                f"{GEOMETRY_ANCHOR_KM * 1000:.0f} m) — reversed or "
                "mis-digitized polyline?")

    # --- horizon: whole days ---
    total_min = inputs.environment.steps * inputs.environment.resolution_minutes
    if total_min % (24 * 60) != 0:
        errors.append(f"environment horizon {total_min} min is not a whole number of days")

    # --- at least one head source (belt and braces; SupplyFile enforces) ---
    slacks = [s for s in inputs.supply.supplies if s.kind == "ext_grid"]
    if not slacks and not inputs.supply.tanks:
        errors.append("at least one head source (ext_grid or tank) required")

    # --- reachability over the pipe graph (PRVs and pump stations are
    # branch elements and count as edges — a zone fed only through them is
    # reachable) ---
    adjacency: dict[str, set[str]] = defaultdict(set)
    degree: dict[str, int] = defaultdict(int)
    for p in inputs.pipes.pipes:
        adjacency[p.from_node].add(p.to_node)
        adjacency[p.to_node].add(p.from_node)
        degree[p.from_node] += 1
        degree[p.to_node] += 1
    for v in inputs.supply.prvs:
        adjacency[v.from_node].add(v.to_node)
        adjacency[v.to_node].add(v.from_node)
        degree[v.from_node] += 1
        degree[v.to_node] += 1
    for st in inputs.supply.stations:
        adjacency[st.from_node].add(st.to_node)
        adjacency[st.to_node].add(st.from_node)
        degree[st.from_node] += 1
        degree[st.to_node] += 1

    head_nodes = ([s.node for s in slacks]
                  + [t.node for t in inputs.supply.tanks])
    if head_nodes:
        reachable = _bfs(adjacency, head_nodes[0])
        for c in inputs.consumers.consumers:
            if c.node not in reachable:
                errors.append(
                    f"consumer {c.name or c.node!r} at {c.node!r} not reachable "
                    f"from the head source at {head_nodes[0]!r}"
                )
        for node in head_nodes:
            if node not in reachable:
                errors.append(
                    f"head source at {node!r} not reachable from the "
                    f"head source at {head_nodes[0]!r} (split network?)")

    # Isolated nodes (no pipe at all) cannot participate in the solve.
    # Dead ends WITHOUT a consumer are fine in hydraulics mode (stagnant
    # stubs are an M4 compliance finding, not an error).
    for j in inputs.structure.junctions:
        if degree.get(j.name, 0) == 0:
            errors.append(f"node {j.name!r} has no pipes attached (isolated)")

    # A PRV must be a CUT edge: a pipe path bypassing it would let the
    # low zone see two contradictory heads (press_control has no state
    # machine — the solve produces fictional fields with reverse valve
    # flow instead of failing). Pump-station branches count like pipes here.
    if head_nodes and inputs.supply.prvs:
        no_prv: dict[str, set[str]] = defaultdict(set)
        for p in inputs.pipes.pipes:
            no_prv[p.from_node].add(p.to_node)
            no_prv[p.to_node].add(p.from_node)
        for st in inputs.supply.stations:
            no_prv[st.from_node].add(st.to_node)
            no_prv[st.to_node].add(st.from_node)
        reachable_wo_prv = _bfs(no_prv, head_nodes[0])
        for v in inputs.supply.prvs:
            if v.to_node in reachable_wo_prv:
                errors.append(
                    f"prv {v.from_node}->{v.to_node}: a pipe path bypasses "
                    "the valve — the zone boundary must be a cut (no "
                    "parallel pipes around a Druckminderer)")

    # A pump station must be a CUT edge too: a pipe path around the pump
    # short-circuits the constant-lift branch into a recirculation loop the
    # operating-point iteration settles on far beyond the curve's domain —
    # fictional flows in "ok" frames (M2 review finding; same failure class
    # as the PRV bypass). Real pump bypasses have check valves and are not
    # modeled before M5.
    for st in inputs.supply.stations:
        others: dict[str, set[str]] = defaultdict(set)
        for p in inputs.pipes.pipes:
            others[p.from_node].add(p.to_node)
            others[p.to_node].add(p.from_node)
        for v in inputs.supply.prvs:
            others[v.from_node].add(v.to_node)
            others[v.to_node].add(v.from_node)
        for st2 in inputs.supply.stations:
            if st2 is st:
                continue
            others[st2.from_node].add(st2.to_node)
            others[st2.to_node].add(st2.from_node)
        if st.to_node in _bfs(others, st.from_node):
            errors.append(
                f"station {st.name or st.from_node}: a pipe path bypasses "
                "the pump — the station edge must be a cut (no parallel "
                "pipes around a Pumpwerk)")

    if errors:
        raise DataContractError(errors)


def _bfs(adjacency: dict[str, set[str]], start: str) -> set[str]:
    seen = {start}
    frontier = [start]
    while frontier:
        nxt: list[str] = []
        for node in frontier:
            for nb in adjacency.get(node, ()):
                if nb not in seen:
                    seen.add(nb)
                    nxt.append(nb)
        frontier = nxt
    return seen
