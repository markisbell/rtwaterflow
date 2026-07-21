"""WellField + Aquifer — the raw-water side (roadmap §4.5, TF §5).

The Reinwasserbehälter (break tank) hydraulically DECOUPLES the raw side
from the network (TF §5): the tank level is the only shared state, so the
wells, the aquifer and the abstraction accounting are a pure-Python mass
balance — no pandapipes. Each engine tick:

1. the network draws from the break tank (its head node), lowering the
   level (the pandapipes solve);
2. the **well pumps** run on the break-tank level (two-point hysteresis,
   like the Hochbehälter pumps) and lift raw water into it — but only up to
   what the **aquifer** currently permits (drawdown must not pull the
   dynamic water level below the filter-screen top; W 118/W 123);
3. the **aquifer** (a single linear reservoir — the standard teaching
   model, TF §5) loses the abstracted volume and gains seasonal recharge,
   its regional level rising in the winter half-year and falling under a
   drought;
4. the abstraction is booked against the **water right** (WHG §§8–10 —
   exceeding it is a COMPLIANCE event, not a hydraulic failure) and the
   pumping **energy** KPI (kWh/m³) is accumulated.

The Lauenau (2020) mechanism falls out: a drought lowers the aquifer →
well drawdown protection caps production below the hot-weekend peak → the
break tank drains → the network pump's low-level protection trips → the
Hochbehälter is no longer refilled → households run dry (PDA).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

RHO_KG_M3 = 998.2
G_M_S2 = 9.81
#: ideal pump energy per m of lift: ρg·(1 m³)·(1 m) = ρg J → /3.6e6 kWh,
#: i.e. ≈ 0.00272 kWh/(m³·m) (TF §5 "≈0.0027 kWh/m³ per m lift ideal");
#: a wire-to-water efficiency divides it (median ~0.58 kWh/m³ at
#: municipal total heads)
KWH_PER_M3_PER_M_IDEAL = RHO_KG_M3 * G_M_S2 / 3.6e6
#: seconds per hour (well rates are stated in m³/h)
S_PER_H = 3600.0


@dataclass
class Aquifer:
    """Single linear reservoir (Einzellinearspeicher, TF §5).

    ``level_m`` is the regional groundwater level [m a.s.l.]. It falls with
    abstraction and rises with recharge; the drought factor scales the
    recharge (1.0 = normal, 0 = no recharge)."""

    storativity_area_m2: float          # S_y · A  [m²] (drop per m³ removed)
    level_initial_m: float
    recharge_m3_per_d_mean: float       # annual-mean natural recharge
    level_m: float = 0.0
    drought_factor: float = 1.0

    def __post_init__(self) -> None:
        self.level_m = float(self.level_initial_m)

    def recharge_m3_per_s(self, day_of_year: int) -> float:
        """Seasonal recharge: a cosine peaking in the winter half-year
        (recharge mainly Nov–Mar, TF §5), scaled by the drought factor."""
        # peak ~ 1 Feb (doy 32), trough ~ 1 Aug; swing ±80 % of the mean
        seasonal = 1.0 + 0.8 * math.cos(2 * math.pi * (day_of_year - 32) / 365.0)
        return (self.recharge_m3_per_d_mean * max(0.0, seasonal)
                * self.drought_factor / 86400.0)

    def step(self, abstracted_m3_per_s: float, day_of_year: int,
             dt_s: float) -> None:
        net_m3 = (self.recharge_m3_per_s(day_of_year) - abstracted_m3_per_s) * dt_s
        self.level_m += net_m3 / self.storativity_area_m2

    def reset(self) -> None:
        self.level_m = float(self.level_initial_m)
        self.drought_factor = 1.0


@dataclass
class Well:
    """A vertical filter well (Vertikalfilterbrunnen, W 118/W 123).

    Drawdown is linear in flow via the specific capacity ``Q/s`` (the
    Dupuit-Thiem confined ideal, TF §5); the dynamic (pumping) level must
    stay above the filter-screen top. Ageing slowly erodes ``Q/s``
    (Verockerung, W 130); ``regenerate`` restores it."""

    name: str
    static_level_m: float               # Ruhewasserspiegel [m a.s.l.]
    spec_capacity_m3h_per_m: float      # Q/s [m³/h per m of drawdown]
    screen_top_m: float                 # Filteroberkante [m a.s.l.]
    rated_m3_h: float                   # nameplate max pump rate
    #: per-YEAR fractional decay of Q/s (accelerated by deep drawdown)
    q_s_decay_per_a: float = 0.03
    protection_margin_m: float = 1.0    # keep the dynamic level this far up
    #: live state
    spec_capacity_now: float = 0.0
    running: bool = False

    def __post_init__(self) -> None:
        self.spec_capacity_now = float(self.spec_capacity_m3h_per_m)

    def max_yield_m3_h(self, regional_level_m: float,
                       interference_m: float = 0.0) -> float:
        """Largest Q [m³/h] that keeps the dynamic level above the screen
        top + margin, at the current regional level and neighbour
        interference. Zero if the well is already below protection."""
        # available drawdown before hitting the protection limit
        headroom = (regional_level_m - interference_m
                    - (self.screen_top_m + self.protection_margin_m))
        if headroom <= 0:
            return 0.0
        return min(self.rated_m3_h, headroom * self.spec_capacity_now)

    def dynamic_level_m(self, q_m3_h: float, regional_level_m: float,
                        interference_m: float = 0.0) -> float:
        s = q_m3_h / self.spec_capacity_now if self.spec_capacity_now > 0 else 0.0
        return regional_level_m - interference_m - s

    def age(self, dt_s: float, drawdown_m: float) -> None:
        """Erode Q/s over time — faster when drawn down deep (TF §5)."""
        years = dt_s / (365.0 * 86400.0)
        stress = 1.0 + 0.5 * max(0.0, drawdown_m) / 10.0   # +50 % at 10 m
        self.spec_capacity_now *= (1.0 - self.q_s_decay_per_a * stress) ** years

    def regenerate(self) -> None:
        """Well regeneration (W 130): restore the specific capacity. 95 %
        of nameplate — clear of the 10 %-aged compliance threshold, so the
        maintenance action actually CLEARS the ageing alarm (M6 review)."""
        self.spec_capacity_now = 0.95 * self.spec_capacity_m3h_per_m

    def reset(self) -> None:
        self.spec_capacity_now = float(self.spec_capacity_m3h_per_m)
        self.running = False


@dataclass
class WellField:
    """Aggregates the wells + aquifer + break tank coupling + accounting.

    Produces the raw-water inflow to the break tank each tick and tracks
    the water-right abstraction and the pumping energy."""

    name: str
    wells: list[Well]
    aquifer: Aquifer
    break_tank_name: str                # the Reinwasserbehälter (a WaterTank)
    pump_head_m: float                  # lift from well to the break tank
    efficiency: float = 0.62            # wire-to-water (TF §5)
    #: water right (WHG §§8–10) — caps, not physical limits
    right_m3_per_a: float | None = None
    right_m3_per_d: float | None = None
    #: Sichardt-style mutual interference: a well sees this fraction of the
    #: OTHER running wells' drawdown (crude superposition, TF §5)
    interference_fraction: float = 0.15
    #: live accumulators (reset each replay)
    energy_kwh: float = field(default=0.0)
    volume_today_m3: float = field(default=0.0)
    volume_year_m3: float = field(default=0.0)
    last_production_m3_h: float = field(default=0.0)
    last_tick_day: int = field(default=-1)
    last_year_index: int = field(default=-1)   # for the annual rollover
    #: well-pump hysteresis memory (fill the break tank below on, stop
    #: above off) — MUST reset for deterministic replay (M6 self-review)
    pumps_running: bool = field(default=True)

    # -- per-tick production --------------------------------------------------

    def _available_yields(self) -> tuple[list[float], list[float]]:
        """Per-well interference-aware available yield [m³/h] + the
        interference drawdown [m] used. Mutual interference (Sichardt cone
        superposition, TF §5): each well sees a fraction of its neighbours'
        drawdown, so N wells yield LESS than N× a lone well. One-pass
        estimate from the standalone drawdowns. At a high aquifer the wells
        are rated-capped so interference is inert; it bites only once the
        falling level makes drawdown the limit."""
        lvl = self.aquifer.level_m
        base = [w.max_yield_m3_h(lvl) for w in self.wells]
        base_dd = [b / w.spec_capacity_now if w.spec_capacity_now > 0 else 0.0
                   for w, b in zip(self.wells, base)]
        total_dd = sum(base_dd)
        interf = [self.interference_fraction * (total_dd - base_dd[i])
                  for i in range(len(self.wells))]
        avail = [w.max_yield_m3_h(lvl, interference_m=interf[i])
                 for i, w in enumerate(self.wells)]
        return avail, interf

    def produce(self, demand_m3_h: float, day: int, day_of_year: int,
                dt_s: float) -> float:
        """Run the wells to meet *demand_m3_h* (the break-tank refill rate
        the hysteresis rule asks for), capped by the aquifer. Returns the
        raw-water inflow to the break tank [kg/s]. Steps the aquifer,
        ageing, water-right and energy accounting."""
        # roll the daily counter at each day change and the ANNUAL counter
        # when the year advances — otherwise a multi-year fast-forward
        # accumulates a false WHG annual violation (M6 review)
        if day != self.last_tick_day:
            if self.last_tick_day >= 0:          # not the very first tick
                self.volume_today_m3 = 0.0
            self.last_tick_day = day
        year_index = day // 365
        if year_index != self.last_year_index:
            if self.last_year_index >= 0:
                self.volume_year_m3 = 0.0
            self.last_year_index = year_index

        avail, interf = self._available_yields()
        total_avail = sum(avail)
        want = max(0.0, demand_m3_h)
        produced_m3_h = min(want, total_avail)

        # distribute the produced flow across wells proportional to yield
        drawdowns: list[float] = []
        running_now: list[bool] = []
        if total_avail > 0 and produced_m3_h > 0:
            for i, (w, a) in enumerate(zip(self.wells, avail)):
                q = produced_m3_h * (a / total_avail) if a > 0 else 0.0
                w.running = q > 1e-6
                running_now.append(w.running)
                dd = (w.static_level_m
                      - w.dynamic_level_m(q, self.aquifer.level_m, interf[i]))
                drawdowns.append(dd)
                # only a PRODUCING well ages (a resting well sees no
                # drawdown, no Verockerung stress — M6 review)
                if w.running:
                    w.age(dt_s, dd)
        else:
            for w in self.wells:
                w.running = False

        # aquifer loses the abstracted volume; recharge added inside step
        abstracted_m3_s = produced_m3_h / S_PER_H
        self.aquifer.step(abstracted_m3_s, day_of_year, dt_s)

        # accounting
        vol_m3 = abstracted_m3_s * dt_s
        self.volume_today_m3 += vol_m3
        self.volume_year_m3 += vol_m3
        self.energy_kwh += (vol_m3 * self.pump_head_m
                            * KWH_PER_M3_PER_M_IDEAL / max(self.efficiency, 0.1))
        self.last_production_m3_h = produced_m3_h
        return produced_m3_h / S_PER_H * RHO_KG_M3   # kg/s into the break tank

    # -- KPIs / status --------------------------------------------------------

    def energy_kwh_per_m3(self) -> float | None:
        if self.volume_year_m3 <= 1e-6:
            return None
        return self.energy_kwh / self.volume_year_m3

    def water_right_status(self) -> dict:
        return {
            "day_m3": round(self.volume_today_m3, 1),
            "day_limit_m3": self.right_m3_per_d,
            "year_m3": round(self.volume_year_m3, 1),
            "year_limit_m3": self.right_m3_per_a,
            "day_exceeded": bool(
                self.right_m3_per_d is not None
                and self.volume_today_m3 > self.right_m3_per_d),
            "year_exceeded": bool(
                self.right_m3_per_a is not None
                and self.volume_year_m3 > self.right_m3_per_a),
        }

    def payload(self) -> dict:
        return {
            "name": self.name,
            "aquifer_level_m": round(self.aquifer.level_m, 3),
            "aquifer_drought_factor": round(self.aquifer.drought_factor, 3),
            "production_m3_h": round(self.last_production_m3_h, 2),
            # interference-aware — the flow production can actually reach
            # (M6 review: the nameplate sum overstated it under drought)
            "capacity_m3_h": round(sum(self._available_yields()[0]), 2),
            "energy_kwh_per_m3": (None if self.energy_kwh_per_m3() is None
                                  else round(self.energy_kwh_per_m3(), 3)),
            "n_wells_running": sum(1 for w in self.wells if w.running),
            "wells": [{
                "name": w.name, "running": w.running,
                "spec_capacity_now": round(w.spec_capacity_now, 3),
                "spec_capacity_rated": round(w.spec_capacity_m3h_per_m, 3),
                "aged_fraction": round(
                    1.0 - w.spec_capacity_now / w.spec_capacity_m3h_per_m, 3),
            } for w in self.wells],
            "water_right": self.water_right_status(),
            # flat mirror of the water right for the recorder CSV
            "wr_year_m3": round(self.volume_year_m3, 1),
            "wr_year_limit_m3": self.right_m3_per_a,
            "wr_day_m3": round(self.volume_today_m3, 1),
            "wr_day_limit_m3": self.right_m3_per_d,
        }

    def reset(self) -> None:
        for w in self.wells:
            w.reset()
        self.aquifer.reset()
        self.energy_kwh = 0.0
        self.volume_today_m3 = 0.0
        self.volume_year_m3 = 0.0
        self.last_production_m3_h = 0.0
        self.last_tick_day = -1
        self.last_year_index = -1
        self.pumps_running = True
