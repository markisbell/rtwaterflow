"""EmitterController — pressure-dependent orifices (roadmap §4.4, TF §7/§8).

One mechanism, three uses: ``mdot = C·max(p, 0)^N1`` [kg/s], evaluated
inside the PDA fixed point each tick (so a burst genuinely draws down its
own node). Each emitter is an extra pandapipes ``sink`` at a junction; the
controller owns their lifecycle (create/expire/clear) and re-evaluates
their withdrawal from the solved pressure.

* **Hydrant** (fire test): specified by a TARGET flow [m³/h] at the node's
  current pressure → ``C = target_mdot / p0^N1``; thereafter the emitter
  follows the pressure, so if the zone collapses the hydrant delivers LESS
  than target (the physical reality the W 405 ≥ 1.5 bar check judges).
  Default N1 = 0.5 (a fixed orifice — EPANET hydrant emitter).
* **Burst**: a large orifice, ``C = Cd·A·√(2ρ)`` (Cd 0.75, area a knob) →
  local pressure crater + upstream flow spike.
* **Leak**: small orifices, FAVAD ``N1 = 1.15`` (field median); many of
  them, distributed per pipe, calibrate the network's night flow (MNF) —
  lowering pressure then measurably cuts the loss (pressure management).

Determinism: emitters carry no RNG; a stochastic burst generator (off by
default) would seed from the bundle, not wall-clock.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandapipes as pp

#: burst discharge: C = Cd·A·√(2ρ), Cd 0.75, ρ 998.2 (SI → kg/s at p in Pa;
#: our p is bar, folded into the coefficient at creation)
RHO_KG_M3 = 998.2
BURST_CD = 0.75
#: FAVAD leak exponent (field median, TF §7)
LEAK_N1 = 1.15
#: fixed-orifice exponent (hydrant / burst)
ORIFICE_N1 = 0.5
#: bar → Pa
BAR_TO_PA = 1e5


@dataclass
class Emitter:
    """One pressure-dependent withdrawal (a pandapipes sink element)."""

    name: str
    node: str
    element: int              # pandapipes sink index
    kind: str                 # "hydrant" | "burst" | "leak"
    coefficient: float        # C in mdot = C·p^N1 (p in bar)
    exponent: float           # N1
    start_tick: int
    expires_tick: int | None  # None = permanent
    target_m3_h: float | None = None  # hydrant reporting only
    #: cap on the withdrawal [kg/s] — a hydrant of a given nozzle cannot
    #: pull MORE than its rated target even at high pressure (None = uncapped)
    max_mdot_kg_per_s: float | None = None
    mdot_kg_per_s: float = 0.0        # last evaluated withdrawal

    def _raw(self, p_bar: float) -> float:
        m = self.coefficient * max(p_bar, 0.0) ** self.exponent
        if self.max_mdot_kg_per_s is not None:
            m = min(m, self.max_mdot_kg_per_s)
        return m

    def evaluate(self, p_bar: float) -> float:
        self.mdot_kg_per_s = self._raw(p_bar)
        return self.mdot_kg_per_s

    def active_at(self, tick: int) -> bool:
        return self.expires_tick is None or tick < self.expires_tick

    @property
    def duration_ticks(self) -> int | None:
        """Original duration (expiry − start); None for a permanent emitter."""
        return (None if self.expires_tick is None
                else self.expires_tick - self.start_tick)

    def payload(self) -> dict:
        return {
            "name": self.name, "node": self.node, "kind": self.kind,
            "coefficient": round(self.coefficient, 6),
            "exponent": self.exponent,
            "target_m3_h": self.target_m3_h,
            "expires_tick": self.expires_tick,
            "duration_ticks": self.duration_ticks,
            "mdot_kg_per_s": round(self.mdot_kg_per_s, 6),
            "m3_per_h": round(self.mdot_kg_per_s / RHO_KG_M3 * 3600.0, 3),
        }


class EmitterController:
    """Registry of emitters + their pandapipes sinks."""

    def __init__(self, net, junction: dict[str, int]):
        self.net = net
        self.junction = junction
        self.emitters: dict[str, Emitter] = {}

    # -- coefficient helpers --------------------------------------------------

    #: minimum reference pressure for sizing a hydrant's coefficient [bar].
    #: A hydrant opened at a collapsed/near-zero node would otherwise get a
    #: huge C and over-deliver once the node recovers — size against a
    #: plausible service pressure instead (M5 review). The per-emitter
    #: ``max_mdot`` cap is the second guard.
    HYDRANT_SIZING_FLOOR_BAR = 2.0

    @staticmethod
    def hydrant_coefficient(target_m3_h: float, p0_bar: float,
                            exponent: float = ORIFICE_N1) -> float:
        """C such that mdot = C·p0^N1 delivers *target_m3_h* at *p0_bar*
        (or at the sizing floor if the node is currently collapsed)."""
        target_mdot = target_m3_h / 3600.0 * RHO_KG_M3
        p0 = max(p0_bar, EmitterController.HYDRANT_SIZING_FLOOR_BAR)
        return target_mdot / p0 ** exponent

    @staticmethod
    def burst_coefficient(area_m2: float) -> float:
        """C = Cd·A·√(2ρ) folded to p in BAR (mdot = C·p^0.5, p in bar)."""
        return BURST_CD * area_m2 * (2.0 * RHO_KG_M3 * BAR_TO_PA) ** 0.5

    # -- lifecycle ------------------------------------------------------------

    def add(self, name: str, node: str, kind: str, coefficient: float,
            exponent: float, start_tick: int,
            duration_ticks: int | None = None,
            target_m3_h: float | None = None,
            max_mdot_kg_per_s: float | None = None) -> Emitter:
        if name in self.emitters:
            raise KeyError(f"emitter {name!r} already exists")
        if node not in self.junction:
            raise KeyError(f"unknown node {node!r}")
        el = pp.create_sink(self.net, junction=self.junction[node],
                            mdot_kg_per_s=0.0, name=f"emitter:{name}")
        expires = None if duration_ticks is None else start_tick + duration_ticks
        em = Emitter(name=name, node=node, element=int(el), kind=kind,
                     coefficient=float(coefficient), exponent=float(exponent),
                     start_tick=int(start_tick), expires_tick=expires,
                     target_m3_h=target_m3_h,
                     max_mdot_kg_per_s=max_mdot_kg_per_s)
        self.emitters[name] = em
        return em

    def remove(self, name: str) -> bool:
        em = self.emitters.pop(name, None)
        if em is None:
            return False
        self.net.sink.drop(index=em.element, inplace=True, errors="ignore")
        if "res_sink" in self.net and len(self.net.res_sink):
            self.net.res_sink.drop(index=em.element, inplace=True,
                                   errors="ignore")
        return True

    def clear(self) -> None:
        for name in list(self.emitters):
            self.remove(name)

    def expire(self, tick: int) -> list[str]:
        """Remove emitters whose duration has elapsed; return their names."""
        gone = [n for n, e in self.emitters.items() if not e.active_at(tick)]
        for n in gone:
            self.remove(n)
        return gone

    # -- per-tick evaluation (inside the PDA fixed point) ---------------------

    def consistency_gap(self, res_junction,
                        idx_junction: dict[str, int]) -> float:
        """Max |target − current mdot| over all emitters at the solved
        pressures — the fixed-point convergence signal (undamped gap).
        ``target`` is the capped ``C·p^N1`` (hydrant nozzle limit)."""
        gap = 0.0
        for em in self.emitters.values():
            p = float(res_junction.p_bar.iloc[idx_junction[em.node]])
            gap = max(gap, abs(em._raw(p) - em.mdot_kg_per_s))
        return gap

    def damped_update(self, res_junction, idx_junction: dict[str, int],
                      damp: float) -> None:
        """Move each emitter's sink mdot toward the (capped) target
        ``C·p^N1`` (damped)."""
        for em in self.emitters.values():
            p = float(res_junction.p_bar.iloc[idx_junction[em.node]])
            target = em._raw(p)
            em.mdot_kg_per_s = em.mdot_kg_per_s + damp * (
                target - em.mdot_kg_per_s)
            self.net.sink.at[em.element, "mdot_kg_per_s"] = em.mdot_kg_per_s

    def zero_withdrawals(self) -> None:
        """Cold state (before the first solve of a tick)."""
        for em in self.emitters.values():
            em.mdot_kg_per_s = 0.0
            self.net.sink.at[em.element, "mdot_kg_per_s"] = 0.0
