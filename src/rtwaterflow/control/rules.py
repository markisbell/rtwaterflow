"""RuleEngine — pre-solve operating rules (M2: tank-level pump hysteresis).

The canonical German waterworks pattern (TF §5): well/network pumps start
when the Hochbehälter falls below a level and stop above another
(Zweipunktregelung; the band prevents rapid cycling). Rules run BEFORE the
solve each tick and write only element ``in_service`` states.

Observability discipline: tank levels are station SCADA — always measured
in reality (TF §7) — so reading the tank objects directly IS reading the
observed layer. The rule engine never touches solved truth values.

Operator overrides: a station in mode ``on``/``off`` bypasses its rule
(forced state); ``auto`` re-enables it. Held per station on the Simulator
(config, saved in scenario recipes).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

STATION_MODES = ("auto", "on", "off")


@dataclass
class HysteresisRule:
    """Two-point tank-level control of one pump station.

    ``running`` is the rule's OWN memory of the hysteresis state — never read
    back from ``net.pump.in_service``: the simulator's check-valve layer
    closes a reversing pump post-solve, and reading the element back would
    latch that transient closure into the band-hold ("pump tripped once,
    never retries").
    """

    station_name: str
    pump_element: int
    tank_name: str
    on_below_m: float
    off_above_m: float
    running: bool = True
    initial_running: bool = True

    def reset(self) -> None:
        self.running = self.initial_running


class RuleEngine:
    """Evaluates the bundle's rules each tick (pre-solve)."""

    def __init__(self, rules: list[HysteresisRule]):
        self.rules = rules

    def reset(self) -> None:
        for rule in self.rules:
            rule.reset()

    def evaluate(self, net, tanks_by_name: dict, modes: dict[str, str]) -> None:
        """Write pump ``in_service`` per rule; ``modes`` (station name →
        auto|on|off) lets the operator force a state past the rule."""
        for rule in self.rules:
            mode = modes.get(rule.station_name, "auto")
            if mode == "on":
                net.pump.at[rule.pump_element, "in_service"] = True
                continue
            if mode == "off":
                net.pump.at[rule.pump_element, "in_service"] = False
                continue
            tank = tanks_by_name.get(rule.tank_name)
            if tank is None:  # tank vanished (should be load-time impossible)
                continue
            if tank.level_m < rule.on_below_m:
                rule.running = True
            elif tank.level_m > rule.off_above_m:
                rule.running = False
            # inside the band: rule.running holds (the hysteresis)
            net.pump.at[rule.pump_element, "in_service"] = rule.running
