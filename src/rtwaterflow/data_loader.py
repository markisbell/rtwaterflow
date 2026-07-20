"""Load and cross-validate the five-file water data contract (roadmap §3).

Per-document schema validation lives in :mod:`rtwaterflow.models`; this module
adds the **cross-document** checks:

* node references valid (pipes, consumers, supplies),
* exactly one slack (already enforced per-file, re-checked here),
* every consumer and supply node reachable from the slack node over the
  pipe graph,
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
)
from .net_inputs import NetInputs

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


def load_network(directory: str | Path) -> NetInputs:
    """Load the five files from *directory*, validate, cross-validate."""
    d = Path(directory)
    missing = [n for n in FILE_NAMES.values() if not (d / n).is_file()]
    if missing:
        raise DataContractError([f"missing file(s) in {d}: {missing}"])

    structure = NetworkStructure.model_validate(_read_json(d / FILE_NAMES["structure"]))
    pipes = PipesFile.model_validate(_read_json(d / FILE_NAMES["pipes"]))
    consumers = ConsumersFile.model_validate(_read_json(d / FILE_NAMES["consumers"]))
    supply = SupplyFile.model_validate(_read_json(d / FILE_NAMES["supply"]))
    environment = EnvironmentFile.model_validate(_read_json(d / FILE_NAMES["environment"]))

    inputs = NetInputs(
        name=structure.name,
        structure=structure,
        pipes=pipes,
        consumers=consumers,
        supply=supply,
        environment=environment,
    )
    cross_validate(inputs)
    return inputs


def cross_validate(inputs: NetInputs) -> None:
    """Raise :class:`DataContractError` on any cross-document violation."""
    errors: list[str] = []
    nodes = {j.name for j in inputs.structure.junctions}

    # --- node references ---
    for p in inputs.pipes.pipes:
        for ref in (p.from_node, p.to_node):
            if ref not in nodes:
                errors.append(f"pipe {p.from_node}->{p.to_node}: unknown node {ref!r}")
    for c in inputs.consumers.consumers:
        if c.node not in nodes:
            errors.append(f"consumer {c.name or c.node!r}: unknown node {c.node!r}")
    for s in inputs.supply.supplies:
        if s.node not in nodes:
            errors.append(f"supply {s.name or s.kind}: unknown node {s.node!r}")

    # --- horizon: whole days ---
    total_min = inputs.environment.steps * inputs.environment.resolution_minutes
    if total_min % (24 * 60) != 0:
        errors.append(f"environment horizon {total_min} min is not a whole number of days")

    # --- exactly one slack (belt and braces; SupplyFile enforces too) ---
    slacks = [s for s in inputs.supply.supplies if s.kind == "ext_grid"]
    if len(slacks) != 1:
        errors.append(f"exactly one slack (ext_grid) required, got {len(slacks)}")

    # --- reachability over the pipe graph ---
    adjacency: dict[str, set[str]] = defaultdict(set)
    degree: dict[str, int] = defaultdict(int)
    for p in inputs.pipes.pipes:
        adjacency[p.from_node].add(p.to_node)
        adjacency[p.to_node].add(p.from_node)
        degree[p.from_node] += 1
        degree[p.to_node] += 1

    if slacks:
        reachable = _bfs(adjacency, slacks[0].node)
        for c in inputs.consumers.consumers:
            if c.node not in reachable:
                errors.append(
                    f"consumer {c.name or c.node!r} at {c.node!r} not reachable "
                    f"from the slack at {slacks[0].node!r}"
                )
        for s in inputs.supply.supplies:
            if s.node not in reachable:
                errors.append(f"supply at {s.node!r} not reachable from the slack")

    # Isolated nodes (no pipe at all) cannot participate in the solve.
    # Dead ends WITHOUT a consumer are fine in hydraulics mode (stagnant
    # stubs are an M4 compliance finding, not an error).
    for j in inputs.structure.junctions:
        if degree.get(j.name, 0) == 0:
            errors.append(f"node {j.name!r} has no pipes attached (isolated)")

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
