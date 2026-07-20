"""``NetInputs`` — the single importer contract.

Every importer (five-file directory loader, catalog, future GIS/bundle-builder
import) converges on this one validated bundle;
:func:`rtwaterflow.network_builder.build_network` consumes nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import (
    ConsumersFile,
    EnvironmentFile,
    NetworkStructure,
    PipesFile,
    SupplyFile,
)


@dataclass(frozen=True)
class NetInputs:
    """Validated, cross-checked content of the five input files (roadmap §3)."""

    name: str
    structure: NetworkStructure
    pipes: PipesFile
    consumers: ConsumersFile
    supply: SupplyFile
    environment: EnvironmentFile

    @property
    def total_minutes(self) -> int:
        return self.environment.steps * self.environment.resolution_minutes

    @property
    def n_days(self) -> int:
        return self.total_minutes // (24 * 60)
