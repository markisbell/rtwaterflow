"""Collector: poll rtwaterflow's REST API and write each solved step into InfluxDB.

Reads ``GET /state`` on the realtime drinking-water service, deduplicates
by ``(day, step)`` so every simulated step is written exactly once, and
stores summary / junctions / pipes / consumers / producers as InfluxDB
measurements. The point timestamp is the wall-clock time the step was
solved, so a Grafana "last 5 minutes" view follows the accelerated realtime
simulation live.
"""
from __future__ import annotations

import logging
import os
import time

import requests
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

log = logging.getLogger("collector")

RTWATERFLOW_URL = os.getenv("RTWATERFLOW_URL", "http://backend:8000").rstrip("/")
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "rtwaterflow-dev-token")
INFLUX_ORG = os.getenv("INFLUX_ORG", "rtwaterflow")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "waterflow")
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL_SECONDS", "0.5"))

#: solver verdict as a plottable field: 2 ok · 1 degraded · 0 failed
_SOLVER_STATE = {"ok": 2, "degraded": 1, "failed": 0}


def wait_for(url: str, label: str, timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=3).status_code < 500:
                log.info("%s is up.", label)
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {label} at {url}")


def _fields(point: Point, row: dict, names: tuple[str, ...]) -> Point:
    for f in names:
        v = row.get(f)
        if v is not None:
            point = point.field(f, float(v))
    return point


def build_points(state: dict) -> list[Point]:
    ts = int(float(state["timestamp"]) * 1e9)  # unix seconds -> ns
    day = int(state["day"])
    step = int(state["step"])
    tod = state["time_of_day"]
    pts: list[Point] = []

    def base(measurement: str) -> Point:
        return (
            Point(measurement)
            .field("day", day)
            .field("step", step)
            .tag("time_of_day", tod)
            .time(ts, WritePrecision.NS)
        )

    # -- summary: hydraulic KPIs + solver verdict ---
    summary = base("summary").field("converged", int(bool(state["converged"])))
    summary = summary.field("solve_ms", float(state.get("solve_ms") or 0.0))
    summary = summary.field(
        "solver_state", _SOLVER_STATE.get(state.get("solver_status"), 0))
    for k, v in (state.get("summary") or {}).items():
        if v is not None and not isinstance(v, str):
            summary = summary.field(k, float(v))
    worst = (state.get("summary") or {}).get("worst_consumer")
    if worst:
        summary = summary.tag("worst_consumer", str(worst))
    pts.append(summary)

    for j in state.get("junctions", []):
        p = (base("junction").tag("junction", str(j["id"]))
             .tag("name", j["name"]))
        pts.append(_fields(p, j, ("p_bar",)))

    for pipe in state.get("pipes", []):
        p = base("pipe").tag("pipe", str(pipe["id"]))
        pts.append(_fields(p, pipe, ("mdot_kg_per_s", "v_m_per_s", "dp_bar")))

    for c in state.get("consumers", []):
        p = base("consumer").tag("consumer", str(c["id"])).tag("name", c["name"])
        pts.append(_fields(p, c, ("mdot_demand_kg_per_s", "mdot_kg_per_s",
                                  "p_bar")))

    for pr in state.get("producers", []):
        p = (base("producer").tag("producer", str(pr["id"]))
             .tag("kind", pr["kind"]).tag("name", pr["name"]))
        if pr.get("running") is not None:
            p = p.field("running", int(bool(pr["running"])))
        if pr.get("cv_closed") is not None:
            p = p.field("cv_closed", int(bool(pr["cv_closed"])))
        pts.append(_fields(p, pr, ("p_bar", "mdot_kg_per_s", "p_set_bar",
                                   "p_out_bar", "p_in_bar", "level_m")))

    for tk in state.get("tanks", []):
        p = base("tank").tag("tank", str(tk["id"])).tag("name", tk["name"])
        for flag in ("overflow", "empty", "fire_reserve_breached"):
            if tk.get(flag) is not None:
                p = p.field(flag, int(bool(tk[flag])))
        pts.append(_fields(p, tk, ("level_m", "volume_m3", "p_bar",
                                   "mdot_kg_per_s", "mdot_spill_kg_per_s",
                                   "buffer_time_h")))

    return pts


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    wait_for(f"{INFLUX_URL}/health", "InfluxDB")
    wait_for(f"{RTWATERFLOW_URL}/health", "rtwaterflow")

    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    log.info("Collecting %s/state -> InfluxDB bucket '%s' every %.2fs",
             RTWATERFLOW_URL, INFLUX_BUCKET, POLL_INTERVAL)

    last_key: tuple[int, int] | None = None
    while True:
        try:
            resp = requests.get(f"{RTWATERFLOW_URL}/state", timeout=5)
            if resp.status_code == 404:
                time.sleep(POLL_INTERVAL)  # no step solved yet
                continue
            resp.raise_for_status()
            state = resp.json()
            key = (int(state["day"]), int(state["step"]))
            if key != last_key:            # dedupe on (day, step) — SPEC §9.3
                write_api.write(bucket=INFLUX_BUCKET, record=build_points(state))
                last_key = key
                log.debug("wrote day=%s step=%s (%s)", key[0], key[1],
                          state["time_of_day"])
        except Exception as exc:  # noqa: BLE001 — keep the collector resilient
            log.warning("poll/write failed: %s", exc)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
