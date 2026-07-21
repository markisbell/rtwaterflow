"""Compliance engine — the post-solve rule pass (roadmap §4.9, TF §2/§4).

Every check emits typed findings ``{severity, rule, check, entity_kind,
entity, value, threshold, text_de, since_ticks}`` with a German citation.
Checks and their anchors (docs/COMPLIANCE.md carries the full mapping):

* ``p_min``       — DVGW W 400-1: minimum supply pressure at the house
                    connection, 2.0 bar (EG) + 0.35 bar per storey above.
                    Short-term undershoot ≤ 0.5 bar is a WARNING band;
                    deeper or SUSTAINED (≥ 1 h) undershoot is a
                    violation. Runtime-added consumers are registered via
                    :meth:`register_consumer` (EG default) — never
                    silently exempt (M4 review). TF §2.
* ``p_rest``      — max Ruhedruck 8 bar: WARNING scoped to junctions
                    that SERVE CONSUMERS (a Pumpwerk discharge or tank
                    riser legitimately runs higher while pumping — M4
                    review: flagging it was a permanent false positive);
                    10 bar = the PN 10 component limit, checked on every
                    junction (violation, with node-class wording). TF §2.
* ``v_max``       — velocity > 2.0 m/s: momentary = warning (2.5 m/s is
                    allowed during fire events — M5), sustained ≥ 1 h =
                    violation. W 400-1, TF §2.
* ``stagnation``  — hygiene: rolling-hour mean |v| < 0.005 m/s per pipe
                    (skipped for pipes on the discharge of a currently
                    OFF pump station: the riser is flushed every cycle);
                    the daily self-cleaning criterion (no excursion
                    > 0.3 m/s) is AGGREGATED into one fleet finding
                    ("N Leitungen ohne Selbstreinigung") — the M4 review
                    measured ~28 identical per-pipe ambers per tick on
                    the healthy showcase net, drowning the alarm center.
* ``tank_reserve``/``tank_empty``/``tank_overflow`` — Löschwasserreserve
                    breached (violation, W 405/W 300-1), tank at minimum
                    while supplying (violation), overflow spill (warning).
* ``tank_turnover`` — day-mean usable volume vs day-mean EXCHANGE rate
                    (mean |net flow| — a Durchlauf tank alternates sign;
                    the raw net draw made the healthy tank flap between
                    "no draw" and phantom 24 h+ turnover) > 24 h →
                    hygiene warning (W 300-1; needs 1 day of history).
* ``solver``      — degraded frames as INFO: the alarm center never
                    hides model honesty.

Statefulness: sustained/rolling checks keep per-element ring buffers at
engine tick resolution; ``reset()`` clears them (deterministic replay —
NB an export replay therefore starts its rolling windows COLD: its
stagnation/turnover findings for a given day differ from the live pack
that had warm history). The engine reads the WIRE payload, so it applies
unchanged to the estimator twin later (M7).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: W 400-1 minimum service pressure [bar]: ground floor + per-storey step
P_MIN_EG_BAR = 2.0
P_MIN_PER_STOREY_BAR = 0.35
#: permitted short-term undershoot band [bar] (warning, not violation)
P_MIN_SHORT_BAND_BAR = 0.5
#: max Ruhedruck (warning, consumer junctions) and the PN 10 hard limit
P_REST_WARN_BAR = 8.0
P_REST_MAX_BAR = 10.0
#: W 400-1 velocities [m/s]
V_MAX_M_S = 2.0
V_STAGNATION_M_S = 0.005
V_SELF_CLEAN_M_S = 0.3
#: W 405 fire flow: minimum flow pressure at a drawing hydrant [bar], and
#: the fraction of the target a hydrant must still deliver to "pass"
P_FIRE_MIN_BAR = 1.5
FIRE_DELIVERY_FRACTION = 0.9


def _de(x: float, nd: int = 2) -> str:
    """German decimal formatting for the finding texts (M4 review: mixed
    separators inside one sentence)."""
    return f"{x:.{nd}f}".replace(".", ",")


@dataclass
class Finding:
    severity: str        # "info" | "warning" | "violation"
    rule: str            # the German rule citation
    check: str           # machine key (docs/COMPLIANCE.md table)
    entity_kind: str     # "consumer" | "node" | "pipe" | "tank" | "system"
    entity: str
    text_de: str
    value: float | None = None
    threshold: float | None = None
    since_ticks: int = 0

    def as_dict(self) -> dict:
        return {
            "severity": self.severity, "rule": self.rule, "check": self.check,
            "entity_kind": self.entity_kind, "entity": self.entity,
            "value": self.value, "threshold": self.threshold,
            "since_ticks": self.since_ticks, "text_de": self.text_de,
        }


@dataclass
class _PipeState:
    """Rolling |v| history for sustained/stagnation checks."""

    v_day: list = field(default_factory=list)      # last day of |v|
    above_max: int = 0                             # consecutive ticks > 2.0
    stagnant: int = 0                              # consecutive hygiene ticks


class ComplianceEngine:
    """Evaluates the rule catalog on each collected frame."""

    def __init__(self, inputs, index, steps_per_day: int):
        self.steps_per_day = int(steps_per_day)
        # ceil: at spd 36 an "hour" is 2 ticks, never 1 (48 min — review)
        self.ticks_per_hour = max(1, -(-self.steps_per_day // 24))
        # consumer requirements from the storeys attribute (TF §2: every
        # consumer node needs one)
        self.p_req: dict[str, float] = {}
        self.consumer_node: dict[str, str] = {}
        for c in inputs.consumers.consumers:
            name = c.name or f"consumer_{c.node}"
            self.p_req[name] = (P_MIN_EG_BAR
                                + P_MIN_PER_STOREY_BAR * (c.storeys - 1))
            self.consumer_node[name] = c.node
        #: junctions that serve consumers — the Ruhedruck warning scope
        self.consumer_junctions: set[str] = set(self.consumer_node.values())
        #: discharge nodes of pump stations (riser feet): the hour-mean
        #: stagnation check skips their pipes while the station is OFF
        self.station_discharge: dict[str, str] = {
            (st.name or f"station_{st.from_node}"): st.to_node
            for st in inputs.supply.stations}
        self._below: dict[str, int] = {}           # consumer -> ticks below
        self._pipes: dict[int, _PipeState] = {}
        self._tank_flow: dict[str, list] = {}      # tank -> last day |mdot|
        self._tank_vol: dict[str, list] = {}       # tank -> last day volume
        self._pipe_nodes: dict[int, tuple[str, str]] = {
            i: (p.from_node, p.to_node)
            for i, p in enumerate(inputs.pipes.pipes)}

    # -- runtime consumer CRUD hooks (M4 review: silently unchecked) --------

    def register_consumer(self, name: str, node: str,
                          storeys: int = 1) -> None:
        self.p_req[name] = (P_MIN_EG_BAR
                            + P_MIN_PER_STOREY_BAR * (storeys - 1))
        self.consumer_node[name] = node
        self.consumer_junctions.add(node)

    def unregister_consumer(self, name: str) -> None:
        self.p_req.pop(name, None)
        self._below.pop(name, None)
        node = self.consumer_node.pop(name, None)
        if node is not None and node not in self.consumer_node.values():
            self.consumer_junctions.discard(node)

    def reset(self) -> None:
        """Clear rolling state (scenario load / bulk-export replay)."""
        self._below.clear()
        self._pipes.clear()
        self._tank_flow.clear()
        self._tank_vol.clear()

    # -- the pass -------------------------------------------------------------

    def evaluate(self, payload: dict, solver_status: str,
                 solver_error: str | None) -> list[dict]:
        out: list[Finding] = []
        self._check_consumers(payload, out)
        self._check_nodes(payload, out)
        self._check_pipes(payload, out)
        self._check_tanks(payload, out)
        self._check_fire(payload, out)
        if solver_status == "degraded":
            out.append(Finding(
                "info", "Modellhinweis", "solver", "system", "solver",
                text_de="Solver degradiert: "
                        + (solver_error or "unbekannter Grund")))
        return [f.as_dict() for f in out]

    def _check_consumers(self, payload: dict, out: list[Finding]) -> None:
        sustained = self.ticks_per_hour            # ≥ 1 h below = sustained
        for c in payload.get("consumers", []):
            name, p = c.get("name"), c.get("p_bar")
            req = self.p_req.get(name)
            if req is None or p is None:
                continue
            if p >= req:
                self._below[name] = 0
                continue
            ticks = self._below.get(name, 0) + 1
            self._below[name] = ticks
            deficit = req - p
            storeys = (req - P_MIN_EG_BAR) / P_MIN_PER_STOREY_BAR + 1
            if deficit > P_MIN_SHORT_BAND_BAR or ticks >= sustained:
                out.append(Finding(
                    "violation", "DVGW W 400-1", "p_min", "consumer", name,
                    value=round(p, 3), threshold=req, since_ticks=ticks,
                    text_de=(f"Versorgungsdruck {_de(p)} bar unter dem "
                             f"Mindestdruck {_de(req)} bar "
                             f"(W 400-1, Gebäude mit {storeys:.0f} "
                             "Geschossen)")))
            else:
                out.append(Finding(
                    "warning", "DVGW W 400-1", "p_min", "consumer", name,
                    value=round(p, 3), threshold=req, since_ticks=ticks,
                    text_de=(f"Kurzzeitige Unterschreitung: {_de(p)} bar "
                             f"(Mindestdruck {_de(req)} bar; ≤ 0,5 bar "
                             "ist in wenigen Spitzenstunden zulässig)")))

    def _check_nodes(self, payload: dict, out: list[Finding]) -> None:
        for j in payload.get("junctions", []):
            p = j.get("p_bar")
            if p is None:
                continue
            if p > P_REST_MAX_BAR:
                is_consumer = j["name"] in self.consumer_junctions
                out.append(Finding(
                    "violation", "DVGW W 400-1", "p_rest", "node", j["name"],
                    value=round(p, 3), threshold=P_REST_MAX_BAR,
                    text_de=((f"Druck {_de(p)} bar über der PN-10-"
                              "Bauteilgrenze von 10 bar")
                             if is_consumer else
                             (f"Betriebsdruck {_de(p)} bar über der "
                              "PN-10-Bauteilgrenze von 10 bar — "
                              "Druckstufe der Transportleitung prüfen"))))
            elif (p > P_REST_WARN_BAR
                  and j["name"] in self.consumer_junctions):
                # the 8-bar Ruhedruck WARNING applies where consumers hang
                # — a Pumpwerk discharge legitimately runs higher (review)
                out.append(Finding(
                    "warning", "DVGW W 400-1", "p_rest", "node", j["name"],
                    value=round(p, 3), threshold=P_REST_WARN_BAR,
                    text_de=(f"Ruhedruck {_de(p)} bar über dem Richtwert "
                             "von 8 bar — Druckminderung prüfen")))

    def _off_station_discharges(self, payload: dict) -> set[str]:
        """Discharge nodes of stations that are NOT running this frame."""
        off: set[str] = set()
        for pr in payload.get("producers", []):
            if pr.get("kind") == "station" and not pr.get("running"):
                node = self.station_discharge.get(pr.get("name", ""))
                if node:
                    off.add(node)
        return off

    def _check_pipes(self, payload: dict, out: list[Finding]) -> None:
        day = self.steps_per_day
        hour = self.ticks_per_hour
        off_discharge = self._off_station_discharges(payload)
        no_self_clean: list[int] = []
        for p in payload.get("pipes", []):
            pid = int(p["id"])
            v = abs(p.get("v_m_per_s") or 0.0)
            st = self._pipes.setdefault(pid, _PipeState())
            st.v_day.append(v)
            if len(st.v_day) > day:
                st.v_day.pop(0)
            if v > V_MAX_M_S:
                st.above_max += 1
                if st.above_max >= hour:
                    out.append(Finding(
                        "violation", "DVGW W 400-1", "v_max", "pipe",
                        str(pid), value=round(v, 3), threshold=V_MAX_M_S,
                        since_ticks=st.above_max,
                        text_de=(f"Fließgeschwindigkeit {_de(v)} m/s seit "
                                 "über einer Stunde über dem Maximum von "
                                 "2,0 m/s (2,5 m/s nur im Brandfall)")))
                else:
                    out.append(Finding(
                        "warning", "DVGW W 400-1", "v_max", "pipe",
                        str(pid), value=round(v, 3), threshold=V_MAX_M_S,
                        since_ticks=st.above_max,
                        text_de=(f"Fließgeschwindigkeit {_de(v)} m/s über "
                                 "2,0 m/s (kurzzeitig)")))
            else:
                st.above_max = 0
            # hygiene: rolling-hour mean below the minimum — skipped for
            # pipes at the discharge of a currently OFF station (the riser
            # is flushed every pump cycle; flagging every pump-off hour
            # was an engineering false positive — review)
            nodes = self._pipe_nodes.get(pid, ())
            if any(n in off_discharge for n in nodes):
                st.stagnant = 0
            elif len(st.v_day) >= hour:
                hour_mean = float(np.mean(st.v_day[-hour:]))
                if hour_mean < V_STAGNATION_M_S:
                    st.stagnant += 1
                    out.append(Finding(
                        "warning", "DVGW W 400-1", "stagnation", "pipe",
                        str(pid), value=round(hour_mean, 5),
                        threshold=V_STAGNATION_M_S,
                        since_ticks=st.stagnant,
                        text_de=("Stagnation: mittlere Geschwindigkeit "
                                 f"{_de(hour_mean * 1000, 1)} mm/s unter "
                                 "dem Hygiene-Minimum von 5 mm/s")))
                    continue
                else:
                    st.stagnant = 0
            # self-cleaning: collected per pipe, emitted as ONE fleet
            # finding (the per-pipe flood drowned the alarm center)
            if len(st.v_day) >= day and max(st.v_day) < V_SELF_CLEAN_M_S:
                no_self_clean.append(pid)
        if no_self_clean:
            out.append(Finding(
                "warning", "DVGW W 400-1", "stagnation", "system",
                "selbstreinigung",
                value=float(len(no_self_clean)),
                threshold=V_SELF_CLEAN_M_S,
                text_de=(f"{len(no_self_clean)} Leitungen seit einem Tag "
                         "ohne Selbstreinigungs-Spitze über 0,3 m/s — "
                         "Spülplan prüfen (Leitungen: "
                         + ", ".join(str(i) for i in no_self_clean[:12])
                         + ("…" if len(no_self_clean) > 12 else "") + ")")))

    def _check_fire(self, payload: dict, out: list[Finding]) -> None:
        """W 405 fire flow: a drawing hydrant must keep ≥ 1.5 bar flow
        pressure at its node and deliver ≥ 90 % of its target. During a
        hydrant draw the 2.5 m/s velocity peak is allowed (so the v_max
        violation is downgraded — not implemented as a suppression here;
        the fire finding is the operative signal)."""
        p_by_node = {j["name"]: j.get("p_bar") for j in payload.get(
            "junctions", [])}
        for em in payload.get("emitters", []):
            if em.get("kind") != "hydrant":
                continue
            p = p_by_node.get(em["node"])
            node = em["node"]
            if p is not None and p < P_FIRE_MIN_BAR:
                out.append(Finding(
                    "violation", "DVGW W 405", "fire_flow", "node", node,
                    value=round(p, 3), threshold=P_FIRE_MIN_BAR,
                    text_de=(f"Löschwasserentnahme am Knoten {node}: "
                             f"Fließdruck {_de(p)} bar unter dem Minimum "
                             "von 1,5 bar (W 405)")))
            target = em.get("target_m3_h")
            delivered = em.get("m3_per_h")
            if (target and delivered is not None
                    and delivered < FIRE_DELIVERY_FRACTION * target):
                out.append(Finding(
                    "violation", "DVGW W 405", "fire_flow", "node", node,
                    value=round(delivered, 1), threshold=round(target, 1),
                    text_de=(f"Hydrant {em.get('name', node)}: nur "
                             f"{_de(delivered, 0)} von {_de(target, 0)} m³/h "
                             "Löschwasser lieferbar (W 405)")))

    def _check_tanks(self, payload: dict, out: list[Finding]) -> None:
        day = self.steps_per_day
        for tk in payload.get("tanks", []):
            name = tk["name"]
            if tk.get("empty"):
                out.append(Finding(
                    "violation", "DVGW W 300-1", "tank_empty", "tank", name,
                    value=tk.get("level_m"), threshold=tk.get("level_min_m"),
                    text_de=(f"Behälter {name} auf Mindeststand — die Zone "
                             "hängt am Notvorrat")))
            elif tk.get("fire_reserve_breached"):
                out.append(Finding(
                    "violation", "DVGW W 405", "tank_reserve", "tank", name,
                    value=tk.get("volume_m3"),
                    threshold=tk.get("fire_reserve_m3"),
                    text_de=(f"Löschwasserreserve angebrochen: nutzbar "
                             f"{_de(tk.get('volume_m3') or 0, 0)} m³ von "
                             f"{_de(tk.get('fire_reserve_m3') or 0, 0)} m³ "
                             "vorgeschriebener Reserve")))
            if tk.get("overflow"):
                out.append(Finding(
                    "warning", "DVGW W 300-1", "tank_overflow", "tank", name,
                    value=tk.get("mdot_spill_kg_per_s"),
                    text_de=(f"Behälter {name} läuft über — Zulauf drosseln "
                             "(Pumpensteuerung prüfen)")))
            # turnover: day-mean usable volume vs day-mean EXCHANGE rate
            # (|net flow| — a Durchlauf tank alternates between charging
            # and supplying; the raw net draw flapped on the healthy
            # showcase tank — review)
            flow = abs(tk.get("mdot_kg_per_s") or 0.0) / 998.2 * 3600
            fhist = self._tank_flow.setdefault(name, [])
            vhist = self._tank_vol.setdefault(name, [])
            fhist.append(flow)
            vhist.append(float(tk.get("volume_m3") or 0.0))
            if len(fhist) > day:
                fhist.pop(0)
                vhist.pop(0)
            if len(fhist) >= day:
                mean_flow = float(np.mean(fhist))         # m³/h exchanged
                mean_vol = float(np.mean(vhist))
                if mean_flow > 1e-6:
                    turnover_h = mean_vol / mean_flow
                    if turnover_h > 24.0:
                        out.append(Finding(
                            "warning", "DVGW W 300-1", "tank_turnover",
                            "tank", name, value=round(turnover_h, 1),
                            threshold=24.0,
                            text_de=(f"Wasseraustausch {_de(turnover_h, 0)}"
                                     " h > 24 h — Stagnationsrisiko im "
                                     "Behälter (W 300-1)")))
                elif mean_vol > 1.0:
                    out.append(Finding(
                        "warning", "DVGW W 300-1", "tank_turnover",
                        "tank", name, value=None, threshold=24.0,
                        text_de=(f"Behälter {name}: seit einem Tag "
                                 "praktisch kein Wasseraustausch — "
                                 "Stagnationsrisiko (W 300-1)")))
