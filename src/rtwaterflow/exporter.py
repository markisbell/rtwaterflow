"""Bulk export: simulate whole days as fast as possible into a recording pack.

Blueprint ``exporter.py`` port. The live recorder (recorder.py) captures what
happens while the accelerated clock ticks — fine for interactive sessions,
but waiting wall-clock minutes per simulated day is pointless when one just
wants "3 days of data for the exercise group". The bulk exporter REPLAYS the
current setup offline: a deep copy of the live Simulator (net, profiles,
runtime consumers, sensor placement) is driven through ``run_step`` for every
step of the requested days, back to back, and every projected frame is fed
into a private ``Recorder`` — so the output pack is **byte-compatible** with
a live recording (same CSVs, same columns, same ``_r()`` rounding, same row
order, same metadata.json recipe, same /recordings listing and ZIP download).
Frames pass through the same strict-mode projection as the live wire
(``StateStore.frame``): in strict mode an export carries no truth either.

Replay semantics (deliberately the LIVE physics, not a day-graph sweep): the
copy is normalized to a **from-midnight replay** by
:meth:`BulkExporter.prepare_replay` — the same convention a fresh scenario
load produces (cold initialization, fresh measurement windows).

Cold-start alarm caveat (M4): ``prepare_replay`` resets the compliance
engine's rolling windows too, so an export of a MID-SESSION day starts its
sustained/stagnation/turnover clocks from zero — its ``findings.csv`` for
that day therefore differs from a live pack that entered the day with warm
history. This is deliberate (the export is a deterministic from-midnight
replay, not a snapshot of a warm live state); it is called out in
``docs/COMPLIANCE.md``.

Emitter caveat (M5): ``reset_operations`` also clears live emitters
(hydrants/bursts/leakage are run-state, not the bundle). So a session with
a live hydrant exports WITHOUT it — ``emitters.csv`` is empty and the
``summary`` ``mdot_emitted``/``mdot_deficit`` columns differ from the live
pack — UNLESS the emitters were saved in a scenario recipe (which the
replay restores). Same from-midnight-replay doctrine; live-vs-export
byte-compat holds only for a run with no live emitters or one driven
entirely from a scenario recipe.

One export at a time (the API maps a second start to 409); progress is
polled via ``status()`` (steps done/total, ETA) and a run can be cancelled
between steps (the partial pack is finalized and marked).
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from .recorder import Recorder
from .state import StateStore

log = logging.getLogger(__name__)


class BulkExporter:
    """Replays whole days of the CURRENT configuration into a recording pack."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._state: dict[str, Any] = {"active": False}

    # -- control -------------------------------------------------------------- #

    def start(self, sim_copy, meta: dict[str, Any], days: list[int],
              name: str | None = None) -> dict:
        """Start the replay on an already ISOLATED simulator copy (the caller
        deep-copies while the engine is briefly parked, so the copy is clean)."""
        if self._state.get("active"):
            raise RuntimeError("a bulk export is already running")
        rec = Recorder(self.root)
        meta = {**meta, "export": {"days": days}}
        rec.start(meta, name=name or f"export-{len(days)}-tage")
        spd = int(sim_copy.settings.steps_per_day)
        self._state = {
            "active": True,
            "id": rec.status()["id"],
            "days": days,
            "steps_total": len(days) * spd,
            "steps_done": 0,
            "day": days[0] if days else None,
            "started": time.time(),
            "error": None,
            "cancelled": False,
        }
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run, args=(sim_copy, rec, days),
            name="rtwaterflow-exporter", daemon=True)
        self._thread.start()
        return self.status()

    def cancel(self) -> dict:
        """Request a stop between steps; the partial pack is kept + finalized."""
        if not self._state.get("active"):
            raise RuntimeError("no bulk export is running")
        self._cancel.set()
        if self._thread is not None:
            self._thread.join(timeout=60)
        return self.status()

    def status(self) -> dict:
        s = dict(self._state)
        if s.get("active") and s.get("steps_done"):
            rate = s["steps_done"] / max(time.time() - s["started"], 1e-6)
            s["eta_seconds"] = round(
                (s["steps_total"] - s["steps_done"]) / max(rate, 1e-6))
        return s

    @property
    def active_id(self) -> str | None:
        """The pack currently being written (guards download/delete)."""
        return self._state.get("id") if self._state.get("active") else None

    # -- replay thread ----------------------------------------------------------- #

    def _run(self, sim, rec: Recorder, days: list[int]) -> None:
        t0 = time.time()
        # frames go through the SAME projection path as the live wire — in
        # strict mode the export pack carries no ground truth either (this is
        # what makes live vs export byte-compatibility hold in both modes)
        store = StateStore(sim.settings)
        try:
            self.prepare_replay(sim, first_day=days[0] if days else 0)
            spd = int(sim.settings.steps_per_day)
            for d in days:
                self._state["day"] = d
                for t in range(spd):
                    if self._cancel.is_set():
                        self._state["cancelled"] = True
                        raise _Cancelled()
                    rec.record(store.frame(sim.run_step(t, d)))
                    self._state["steps_done"] += 1
        except _Cancelled:
            log.info("bulk export cancelled after %d steps",
                     self._state["steps_done"])
        except Exception as exc:  # noqa: BLE001 — surface via status, keep partial
            log.exception("bulk export failed")
            self._state["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            export_meta = {**rec._meta.get("export", {}),
                           "cancelled": self._state.get("cancelled", False),
                           "error": self._state.get("error"),
                           "duration_seconds": round(time.time() - t0, 1)}
            rec._meta = {**rec._meta, "export": export_meta}
            rec.stop()
            self._state["active"] = False
            log.info("bulk export finished: %d steps in %.1f s",
                     self._state["steps_done"], time.time() - t0)

    @staticmethod
    def prepare_replay(sim, first_day: int = 0) -> None:
        """Normalize *sim* for a deterministic from-midnight replay.

        The state reset here is exactly the run-state a fresh scenario load
        discards too — configuration (consumers, sensor placement, station
        control setup) is kept:

        * operations back to the bundle's initial point: tank levels to
          level_initial (heads re-written), station modes to their
          configured state;
        * cold initialization (build-time pressures, pn_bar only) —
          replacing the live warm-start state;
        * measurement windows fresh (standard-mode meters cold-start
          honestly), last-payload/blind-spot cleared;
        * estimation disabled on the replay copy (stubbed in M0 anyway).

        Public on purpose: the live-vs-export byte-compatibility test starts
        its live recording from this same normalized state.
        """
        from dataclasses import replace
        sim.reset_operations()
        sim.measurements._reset_windows()
        sim._last_payload = None
        sim._blind_spot = None
        sim.set_est_config(replace(sim.est_config, enabled=False))
        sim._reset_initialization()


class _Cancelled(Exception):
    pass
