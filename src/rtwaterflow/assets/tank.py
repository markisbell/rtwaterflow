"""WaterTank — the Hochbehälter as a level-integrating head node.

pandapipes has no head-coupled tank (``mass_storage`` is bookkeeping only,
TF §8 gap 2), so the platform models a tank as an ``ext_grid`` whose
pressure a controller re-writes every tick from the integrated water level:

* **write** (pre-solve): ``p_bar = level · ρg`` — the tank's water column
  above its junction (the junction sits at the tank BOTTOM elevation, so
  pandapipes' native ``height_m`` handling does the rest of the zone).
* **integrate** (post-solve, explicit Euler — EPANET EPS semantics):
  ``level += (mdot / ρ) · dt / area`` with the pandapipes sign convention
  (``res_ext_grid.mdot_kg_per_s`` NEGATIVE = supplying the net → level
  falls; positive = receiving pump inflow → level rises).
* **clamps**: at ``level_max`` the surplus spills (``overflow`` flag — the
  EPANET overflow=YES semantic); at ``level_min`` the level holds and the
  ``empty`` flag raises. True starvation of the zone (demand > available)
  becomes physical with the M5 pressure-driven demand; until then an empty
  tank keeps its minimum head and the alarm is the teaching signal.

Reserves (TF §4): the usable volume spans ``level_min..level_max``; the
Löschwasserreserve (``fire_reserve_m3``) sits directly above ``level_min``
— ``fire_reserve_breached`` warns when the remaining usable volume dips
into it (a distinct, more serious alarm than plain low level).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..network_builder import BAR_PER_M, RHO_KG_M3
from ..sensors import _r


@dataclass
class WaterTank:
    """Level state + head writeback for one tank (ext_grid element)."""

    name: str
    node: str
    element: int              # ext_grid element index
    area_m2: float
    level_min_m: float
    level_max_m: float
    fire_reserve_m3: float
    kind: str                 # "durchlauf" | "gegen"
    level_m: float = 0.0
    level_initial_m: float = 0.0
    overflow: bool = False
    empty: bool = False
    #: platform-unique producer pid (the wire id — NEVER the per-kind
    #: list index, which collides with other producer kinds)
    pid: int = 0
    #: M6: external raw-water inflow [kg/s] a well field lifts INTO this
    #: tank (the Reinwasserbehälter break tank); 0 for a normal Hochbehälter
    external_inflow_kg_per_s: float = 0.0
    #: net mass flow of the last integration step (+ = filling) [kg/s]
    mdot_kg_per_s: float = field(default=0.0, repr=False)
    #: overflow spill of the last step [kg/s]: inflow the clamped level
    #: could not keep (EPANET overflow=YES semantics) — split out so the
    #: summary's "stored" stays level-effective (M2 review finding)
    mdot_spill_kg_per_s: float = field(default=0.0, repr=False)
    #: OPT-IN dead-head override (gamebridge water_tower semantics): the
    #: boundary pressure written while the tank sits AT ``level_min`` —
    #: an empty tower must stop supplying (the platform default keeps the
    #: minimum head and raises the ``empty`` alarm instead; None = default).
    #: Recovers automatically once net inflow lifts the level off the floor.
    empty_head_p_bar: float | None = None

    # -- head writeback (pre-solve) -----------------------------------------

    def p_bar(self) -> float:
        return self.level_m * BAR_PER_M

    def write_p(self, net) -> None:
        p = self.p_bar()
        if (self.empty_head_p_bar is not None
                and self.level_m <= self.level_min_m + 1e-9):
            p = self.empty_head_p_bar
        net.ext_grid.at[self.element, "p_bar"] = p

    # -- level integration (post-solve) --------------------------------------

    def integrate(self, net, dt_s: float) -> None:
        """Advance the level from the SOLVED ext_grid balance plus any
        external raw-water inflow (M6 break tank: the well production)."""
        # res_ext_grid.mdot is negative when the tank SUPPLIES the network;
        # the external inflow is positive (a well field FILLS the tank)
        mdot = (float(net.res_ext_grid.mdot_kg_per_s.loc[self.element])
                + self.external_inflow_kg_per_s)
        self.mdot_kg_per_s = mdot
        d_level = (mdot / RHO_KG_M3) * dt_s / self.area_m2
        level = self.level_m + d_level
        self.overflow = level > self.level_max_m
        self.empty = level <= self.level_min_m and mdot < 0
        clamped = min(self.level_max_m, max(self.level_min_m, level))
        # overflow spill = inflow the clamp rejected, as a mean rate over
        # this step (the level-effective part is what "stored" may claim)
        self.mdot_spill_kg_per_s = (
            (level - clamped) * self.area_m2 * RHO_KG_M3 / dt_s
            if self.overflow else 0.0)
        self.level_m = clamped

    # -- reset (scenario load / bulk-export replay) ---------------------------

    def reset(self) -> None:
        self.level_m = self.level_initial_m
        self.overflow = False
        self.empty = False
        self.mdot_kg_per_s = 0.0
        self.mdot_spill_kg_per_s = 0.0
        self.external_inflow_kg_per_s = 0.0

    # -- KPIs -----------------------------------------------------------------

    @property
    def usable_m3(self) -> float:
        return (self.level_m - self.level_min_m) * self.area_m2

    @property
    def capacity_m3(self) -> float:
        return (self.level_max_m - self.level_min_m) * self.area_m2

    @property
    def fire_reserve_breached(self) -> bool:
        return self.usable_m3 < self.fire_reserve_m3

    def buffer_time_h(self) -> float | None:
        """Hours until the usable volume is gone at the CURRENT draw
        (None while the tank is filling or balanced — no countdown)."""
        if self.mdot_kg_per_s >= -1e-9:
            return None
        draw_m3_h = -self.mdot_kg_per_s / RHO_KG_M3 * 3600.0
        return self.usable_m3 / draw_m3_h if draw_m3_h > 0 else None

    def payload(self) -> dict:
        return {
            "id": int(self.pid),   # platform pid — joinable with producers
            "name": self.name,
            "node": self.node,
            "kind": self.kind,
            "level_m": _r(self.level_m, 4),
            "level_min_m": _r(self.level_min_m),
            "level_max_m": _r(self.level_max_m),
            "volume_m3": _r(self.usable_m3, 2),
            "capacity_m3": _r(self.capacity_m3, 2),
            "fire_reserve_m3": _r(self.fire_reserve_m3, 2),
            "p_bar": _r(self.p_bar()),
            "mdot_kg_per_s": _r(self.mdot_kg_per_s),
            "mdot_spill_kg_per_s": _r(self.mdot_spill_kg_per_s),
            "buffer_time_h": _r(self.buffer_time_h(), 2),
            "overflow": self.overflow,
            "empty": self.empty,
            "fire_reserve_breached": self.fire_reserve_breached,
        }
