# rtwaterflow

Realtime drinking-water network simulator on **pandapipes 0.14.0** — the
cold-water sibling of [rtheatflow](https://github.com/markisbell/rtheatflow)
(district heating). A quasi-static engine solves the network hydraulics once
per simulated minute (accelerated wall-clock ticks) and streams every frame
to an interactive Leaflet map: node pressures with DVGW-anchored traffic-light
colors, flow velocities, and the three-layer observability concept
(**Realität / Gemessen / Schätzung**).

Governing documents: [`../IMPLEMENTATION_ROADMAP.md`](../IMPLEMENTATION_ROADMAP.md)
(the build plan, milestones M0-M9) and
[`../TECHNICAL_FOUNDATIONS.md`](../TECHNICAL_FOUNDATIONS.md) (verified DVGW/DIN
constraints, pandapipes gaps, German water-supply engineering).

**Status: M0** — fork-and-strip complete. Single pipe layer, hydraulics-only
solve (colebrook), elevation-aware junctions, fixed-demand consumers, one
ext_grid head source. Demo network `tutorial_hillside` reproduces the official
pandapipes height-difference tutorial: 0.5 bar at 400 m elevation -> **5.78 bar
at the 346 m consumer** (the 1-bar-per-10-m teaching point), verified by an
elevation regression test.

## Run

| Component | Port | Start |
|---|---|---|
| Backend (FastAPI + engine) | 8002 | `start_rtwaterflow.bat` or `PYTHONPATH=src python -m rtwaterflow.main` |
| UI (Vite dev) | 5175 | part of the launcher / `cd ui && npm run dev` |
| Docker Compose (backend/UI/InfluxDB/Grafana) | 8002/8082/8088/3002 | `docker compose up -d` |

Sibling port scheme: netzsim owns 8000/5173, rtheatflow 8001/5174,
**rtwaterflow 8002/5175** — all three can run side by side.
`stop_rtwaterflow.bat` tears everything down (windows, ports, orphans).

## Network bundles (five-file contract)

`data/networks/<id>/`: `network_structure.json` (nodes with **elevation_m**),
`pipes.json` (DN + integral roughness k per DVGW GW 303-1),
`consumers.json` (fixed demand in M0), `supply.json` (one ext_grid slack),
`environment.json` (horizon + drivers for the M3 demand engine).

## Tests

```
.venv\Scripts\python -m pytest -q   # backend (83 tests)
cd ui && npm run build && npx vitest run   # tsc strict + 18 UI tests
```

The M0 acceptance pin: junction pressures of `tutorial_hillside` match the
locally baselined colebrook solution within ±0.01 bar
(`tests/test_tutorial_hillside.py`).

## AI-generated

This repository is developed by AI coding agents (Claude) under human
direction; see `CLAUDE.md` for the development log. MIT license.