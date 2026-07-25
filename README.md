# rtwaterflow — a realtime drinking-water teaching platform

![license](https://img.shields.io/badge/license-MIT-blue)
![AI-generated](https://img.shields.io/badge/source-AI--generated-8A2BE2)
![engine](https://img.shields.io/badge/engine-pandapipes%200.14.0-brightgreen)
![status](https://img.shields.io/badge/milestone-M9%20complete-brightgreen)

> [!NOTE]
> **AI-generated code.** The source code, tests and documentation of this
> platform — including the German user manual — were written by an AI coding
> agent (Claude Code, Anthropic), working under human direction: a person
> specified the requirements and domain decisions, reviewed the results and
> verified every feature live against the running system. Treat it
> accordingly — read before you trust.

**rtwaterflow** executes continuous hydraulic simulations of German municipal
cold drinking-water supply networks using
[pandapipes](https://github.com/e2nIEE/pandapipes), presented as an interactive
teaching platform: one simulated minute per accelerated wall-clock tick, every
node and pipe live on a map. It traces the whole supply chain — **wells and
aquifer** → **break tank** (Reinwasserbehälter) → network pumps → elevated
tanks (Hochbehälter) → household connections — so the ~0.098 bar-per-metre
elevation physics that governs who gets water becomes visible. Constraints are
**DVGW-anchored** (W 400-1 pressures, W 405 fire flow, W 410 demand), surfaced
as traffic-light node pressures, flow velocities and a live compliance/alarm
catalog. The interface is built around three parallel views: actual physics
(Realität), measured quantities (meters and sensors only — Gemessen), and an
estimated state (Schätzung). It is the cold-water sibling of
[rtheatflow](https://github.com/markisbell/rtheatflow) (district heating) and
mirrors its architecture. [CLAUDE.md](CLAUDE.md) is the development log,
[docs/COMPLIANCE.md](docs/COMPLIANCE.md) the DVGW rule catalog,
[docs/API.md](docs/API.md) the route reference, and
[docs/Benutzerhandbuch.md](docs/Benutzerhandbuch.md) the German user manual
(served live at `GET /manual`). The binding build spec
(`IMPLEMENTATION_ROADMAP.md`, milestones M0–M9) and domain reference
(`TECHNICAL_FOUNDATIONS.md`, verified DVGW/DIN constraints) live in the parent
research project.

> **Status: M0–M9 complete** on branch `m0-fork-strip` — the full build: hydraulic
> core, tanks/pumps/zones, demand engine, compliance, PDA/emitters, wells &
> aquifer, the M7 forward-observer estimation, the M8 geodata bundle builder +
> NetzStudio editor, and M9 validation (EPANET/WNTR cross-checked) + docs. The
> estimation (Schätzung) view is a live digital-twin observer.

## The three applications

| App | What it is | Port |
|-----|-----------|------|
| **rtwaterflow** (`src/`) | the FastAPI pipeflow service (REST + WebSocket) | 8002 |
| **UI** (`ui/`) | a React + Vite + Leaflet frontend (German default, DE/EN) | 8082 (nginx) / 5175 (dev) |
| **Visualization** (`visualization/`) | a collector → InfluxDB → Grafana dashboard | 8088 / 3002 |

(Ports follow the **sibling scheme**: netzsim/rtpowerflow owns 8000/5173,
rtheatflow 8001/5174, **rtwaterflow 8002/5175** — all three platforms run in
parallel on one machine. `stop_rtwaterflow.bat` tears everything down again.)

## What it can do

- **Network catalog**: seven teaching bundles — four synthetic layouts
  (`tutorial_hillside`, the pandapipes height-difference tutorial rebuilt as a
  five-file bundle; `musterdorf`, a 35-node two-pressure-zone showcase with a
  full archetype demand mix, PRV, tank and pump; `mustertal`, a counter-tank /
  Wasserturm net whose transport segment reverses sign over the day; `lauenau`,
  a wells + aquifer + break-tank net geo-anchored to the real town and its 2020
  water emergency) plus three **real-geodata** bundles built offline from OSM +
  EU-DEM (`alpen`, a flat 182-node Niederrhein town; `neubeuern`, a hilly
  215-node Inn-valley town with genuine PRV **Druckzonen** across ~78 m of
  relief; `kevelaer`, a ~540-node city-scale performance net) — or import your
  own five-file bundle. All coordinates are real WGS84.
- **Three parallel views** on Leaflet/OSM maps with DVGW-anchored color layers:
  node pressure (traffic-light around the W 400-1 storey minimum), flow
  velocity, tank levels. The measured view (Gemessen) shows metered elements
  only; the estimation view (Schätzung) is a **digital-twin observer** — a
  second net reconstructed from operator SCADA + demand priors, so an anomaly at
  an unmetered node stays invisible in the estimate.
- **Geodata bundle builder + NetzStudio editor**: an offline `tools/bundle_builder`
  (osmnx street graph + EU-DEM elevation → a synthesised gravity network,
  reproducible from a pinned snapshot) and an in-app editor that draws a network
  on real streets with live W 400-1 load-case checking before commissioning.
- **Measurement layer**: place water meters (Wasserzähler, `mdot`+`p`) and node
  pressure sensors, with presets, live vs 15-minute fidelity and honest cold
  starts; strict mode (`EXPOSE_GROUND_TRUTH=false`) withholds the reality layer
  entirely (truth keys, background leaks and all findings are stripped from the
  wire and the recordings, leaving only the equipment SCADA an operator sees).
- **Runtime equipment**: elevated/through-flow/counter/break tanks with
  level-integrating controllers, fire reserve and overflow/empty flags; pump
  stations with a bracketed-secant operating-point solve and EPANET-style check
  valves; wells and a linear-reservoir aquifer on the raw-water side — placed
  and driven on the running network.
- **Events**: hydrants (sized to a target fire flow), pipe bursts
  (`C=C_d·A·√(2ρΔp)`) and background leaks (FAVAD) as pressure-dependent
  emitters with tick-scheduled expiry, driven through a Wagner
  **pressure-driven-demand** outer loop so under-pressure nodes deliver less
  (not negative) water.
- **Operator controls**: a live outdoor-temperature / dryness knob that reshapes
  demand by consumer identity (irrigation, livestock, pool), pump-station modes
  (auto / on / off), and a drought factor plus per-well regeneration on the
  raw-water side.
- **Demand engine**: stochastic archetype profiles (residential city/village,
  industry, school, office, hospital, dairy/pig farm, pool with DIN 19643
  backwash) normalized to a DVGW **W 410** daily/hourly peak envelope, with a
  hot-dry irrigation surge and seeded per-consumer noise.
- **Compliance / alarms**: a post-solve rule pass emits typed findings with
  German DVGW citations (W 400-1 storey pressures, PN-10 rest pressure, velocity,
  stagnation/turnover, tank reserves, W 405 fire flow, W 130 well ageing, WHG
  water right) — see [docs/COMPLIANCE.md](docs/COMPLIANCE.md).
- **Scenarios** as hand-editable JSON recipes; **session recording** to tidy
  CSVs and a deterministic offline bulk exporter (byte-compatible from-midnight
  replay).

## Run it

**Windows (double-click):**

```
start_rtwaterflow.bat
```

Starts backend (:8002) and Vite UI (:5175) in separate consoles, waits for
`/health` (the first solve pays the numba JIT warm-up, up to ~60 s) and opens
the browser. `stop_rtwaterflow.bat` stops servers, consoles and any orphaned
background processes of this repo (it leaves the netzsim/rtheatflow siblings
untouched). One-time setup:

```
py -3 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

(`ui\node_modules` installs automatically on first launch.)

**Local (manual):**

```
# backend (:8002)
set PYTHONPATH=src
.venv\Scripts\python -m rtwaterflow.main

# UI (:5175, proxies /api and /ws to 127.0.0.1:8002)
cd ui && npm run dev
```

**Docker Compose (full stack):**

```
docker compose up --build
```

backend :8002 · ui :8082 · InfluxDB :8088 · Grafana :3002 (file-provisioned
dashboard, dev credentials and an unauthenticated backend by design — for local
use only; change and lock down before exposing). The backend image bakes the
committed `data/` and runs standalone; the `./data` volume persists recordings
and imported networks.

## Architecture (app 1)

```
data/networks/<id>/*.json ─► data_loader (validate + cross-validate the five-file contract)
                                  │
                          network_builder ──► pandapipes net built ONCE (1 node = 1 junction,
                                  │           elevation → height_m) + dense demand/weather arrays
                                  │
         accelerated tick ─► RealtimeEngine (asyncio) ─► Simulator.run_step(step, day)
         (1 step / N sec)        │ wraps day, day++    apply demand/tanks/pumps/wellfields
                                 │                     → retry-ladder pipeflow (warm start),
                                 │                       PDA + emitter + station fixed points
                                 ▼
                             StateStore (latest + history + WS pub/sub + recorder sink,
                                 │        strict-mode projection)
              ┌──────────────────┴───────────────────┐
              ▼ WebSocket /ws                         ▼ REST GET /state (polled)
         browser / UI                            collector ─► InfluxDB ─► Grafana
```

Key design principles: the network is built **once** per scenario and each tick
only overwrites injection columns (`sink.mdot`, tank heads, pump `in_service`) —
never a rebuild; the solve runs **off the event loop** (`asyncio.to_thread`)
behind a **retry ladder** (colebrook n → colebrook 3n → swamee-jain 3n → nikuradse
3n) whose non-convergence is data, never a crash (a failed tick republishes the
last converged state as a `converged=false` frame); converged results
**warm-start** the next solve; there is **one wire format** (the projected
`StepResult`) serving REST, WebSocket, the recorder and the strict-observability
mode identically; controllers read **only the measured/SCADA layer** (pumps
switch on tank levels, which are always-observed station telemetry); and a
**break tank hydraulically decouples** the pure-Python raw-water side (wells,
aquifer, water-right and energy accounting) from the pandapipes network.

**Extended documentation:** the full module map, the three-view observability
model, and the frontend/persistence/testing layers are in
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**; the EPANET/WNTR validation
methodology + reproduction in **[docs/BENCHMARKS.md](docs/BENCHMARKS.md)**; the
OSM/DEM geodata bundle builder in
**[docs/GEODATA_BUILDER.md](docs/GEODATA_BUILDER.md)**; the DVGW compliance rules
in **[docs/COMPLIANCE.md](docs/COMPLIANCE.md)**; and the German user manual
(served at `/manual`) in **[docs/Benutzerhandbuch.md](docs/Benutzerhandbuch.md)**.

## Input file formats (native to pandapipes)

Five JSON documents per network (`data/networks/<id>/`, validated by pydantic
with cross-validation — node refs, whole-day horizon alignment, ≥1 head source,
reachability, no isolated nodes, PRV and pump stations as cut edges):

- `network_structure.json` — one entry per junction (`geo: [lat, lon]`,
  `elevation_m`, `kind`); elevation becomes the hydrostatic `height_m` term.
- `pipes.json` — one entry per pipe (length or street geometry; DN + material →
  GW 303-1 roughness, or an explicit inner diameter / `k_mm`).
- `consumers.json` — profile rows **are** the sinks: base `mdot_kg_per_s`, a
  demand `kind` (archetype), `storeys` (feeds the W 400-1 minimum), and an
  optional `size` (population / employees / pupils / beds / animals / visitors).
- `supply.json` — the head sources and equipment: `ext_grid` slacks, PRVs,
  tanks (durchlauf / gegen / break), pump stations (Q-H curve + hysteresis) and
  M6 well fields.
- `environment.json` — the horizon and drivers: `resolution_minutes`, `steps`,
  `t_air_c[]`, day types, per-day dryness and season anchor.

## Configuration (`.env`, see `.env.example`; prefix `RTWATERFLOW_`)

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` / `DEFAULT_NETWORK` | `./data` / `tutorial_hillside` | dataset root / network loaded at startup |
| `STEP_INTERVAL_SECONDS` | `1.0` | real seconds per simulated step (0.01…) |
| `STEPS_PER_DAY` | `1440` | simulated steps per day (one-minute steps) |
| `AUTOSTART` | `true` | start the tick loop on boot |
| `HISTORY_SIZE` | `1440` | rolling history buffer length |
| `SOLVER_ITER` | `100` | retry-ladder base iterations (tiers 2–4 use 3×) |
| `PDA_ENABLED` | `true` | Wagner pressure-driven demand (`false` = fixed-demand contrast) |
| `EXPOSE_GROUND_TRUTH` | `true` | `false` = strict mode (reality layer withheld) |
| `RECORD` / `RECORDINGS_DIR` | `false` / `./data/recordings` | continuous session recording |
| `HOST` / `PORT` | `127.0.0.1` / `8002` | backend bind address / port (UI dev is 5175) |

## Tests

```
.venv\Scripts\python -m pytest tests -q         # backend: 268 tests
cd ui && npm run build && npx vitest run        # tsc strict build + 36 unit tests
```

## Validation

rtwaterflow ships **hand-authored / deterministically-generated teaching
networks**, not converted real-utility datasets — so, unlike its heating
sibling, it makes **no claim of validation against field measurements**. What is
verified is the **engine**: specific hydraulic mechanisms are cross-checked
against independent oracles and DVGW corridors, live on the pinned stack
(pandapipes 0.14.0). Numbers below come from the test suite unless marked as
development-log evidence.

| Check | Oracle / reference | Result |
|---|---|---|
| Full network hydraulics | **EPANET/WNTR** — the standard **Net1** (9 j) + **Net3** (92 j) rebuilt with `swamee-jain` | node pressures within **< 0.001 bar**, link flows within **~0.5 %** of WNTR's EpanetSimulator on the identical D-W network (`tests/validation/`) |
| Elevation head (`tutorial_hillside`) | the pandapipes height-difference tutorial | ext-grid 0.5 bar @ 400 m → **5.78 bar** at the 346 m consumer; junction pressures pinned to the baselined Colebrook solution within **±0.01 bar** (agrees with the upstream Nikuradse tutorial to ±0.02 bar) |
| Hydrant fire flow | **EPANET/WNTR** emitter (D-W, exponent 0.5) | hydrant node pressure within **±0.1 bar** across comfortable/marginal/crater cases; the W 405 ≥ 1.5 bar verdict agrees |
| Pressure-driven demand | **WNTR** Wagner PDD | the M5 PDA curve matches WNTR's delivered fraction within **0.001** across the band |
| Tank level dynamics | **EPANET/WNTR** tank | level-rate error vs EPANET **median < 1 cm/tick, p75 < 3 cm/tick** in state-matched ticks |
| Demand envelope | DVGW **W 410** `f_d`/`f_h` | a synthetic residential year lands within **±20 %** of `f_d = 3.9·E^−0.0752` and `f_h = 18.1·E^−0.1682` |
| Mass balance | conservation | Musterdorf `|balance_err|/feed < 0.1 %`; demand = delivered to 1e-6 kg/s |
| Pumping energy (`lauenau`) | TF §5 corridor | a normal day lands in the **0.3–1.0 kWh/m³** band (≈ 0.385 kWh/m³, dev-log evidence) with no dry taps |

## License

MIT for source code and documentation — see [LICENSE](LICENSE). The hydraulic
engine [pandapipes](https://github.com/e2nIEE/pandapipes) (Fraunhofer IEE /
Uni Kassel) is an unvendored, permissively licensed (BSD-3) pip dependency. The
shipped teaching networks are original to this repo; no third-party datasets are
vendored.
