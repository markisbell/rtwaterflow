# rtwaterflow — Architecture

Real-time **drinking-water network** simulation platform on **pandapipes
0.14.0**. rtwaterflow is the cold-water sibling of
[rtheatflow](https://github.com/markisbell/rtheatflow) (district heating), which
was itself structurally cloned from the blueprint
[rtpowerflow](https://github.com/markisbell/rtpowerflow) (project *netzsim*,
electricity). The three share one platform skeleton — a build-once/step-cheaply
engine, a three-layer observability model, a WebSocket wire, a recorder/exporter,
and a React/Leaflet UI — and differ only in the physics domain.

This document describes the system as built through **M9** (the full M0–M9
milestone set). The binding specification lives one level up in
[`IMPLEMENTATION_ROADMAP.md`](../../IMPLEMENTATION_ROADMAP.md) (milestones + acceptance
criteria) and [`TECHNICAL_FOUNDATIONS.md`](../../TECHNICAL_FOUNDATIONS.md)
(adversarially-verified DVGW/DIN constraints and pandapipes capabilities/gaps);
the running dev log is in [`../CLAUDE.md`](../CLAUDE.md). (The roadmap and
foundations live in the parent `Wassernetze/` directory, one level above the repo.)

---

## 1. System overview

rtwaterflow is a three-tier application. A student watches a real German-style
drinking-water network run in **accelerated real time** — one simulation step is
one minute of network time — and sees pressures, flow velocities, falling tank
levels, and DVGW rule violations evolve on a map.

```
                         ┌───────────────────────────────────────────┐
   five-file bundle ───► │  Backend (FastAPI, :8002)                  │
   (JSON, pandapipes-    │                                            │
    native)             │   data_loader ─► network_builder ─► Simulator
                         │        (validate)     (pandapipes net)   │  │
                         │                                          ▼  │
   REST + WebSocket ◄────┤   API routers  ◄──  StateStore  ◄──  step()   1 min/tick
                         │                        (ring buffer)        │
                         └───────────┬──────────────────┬─────────────┘
                                     │ WS frames        │ line-protocol
                                     ▼                  ▼
                    ┌────────────────────────┐   ┌──────────────┐   ┌──────────┐
                    │  UI (React/Vite/Leaflet │   │  collector   │──►│ InfluxDB │──► Grafana
                    │       :5175 / :8082)    │   │  (telemetry) │   │  :8088   │    :3002
                    └────────────────────────┘   └──────────────┘   └──────────┘
```

**Simulation mechanics.** The engine runs a fixed number of steps per simulated
day (`STEPS_PER_DAY`, default 1440 = one step per minute) and wraps at day end,
cycling the diurnal/weekly/seasonal demand profiles. Each tick is a **stationary
hydraulic snapshot** — a full pandapipes solve — so the model is quasi-static
(no water-hammer/surge). Wall-clock pacing is one real second per simulated
minute by default (`STEP_INTERVAL_SECONDS`), giving > 12× real-time headroom on
the demo bundles.

**Sibling tools.** Two offline helpers feed the platform. The **geodata bundle
builder** (`tools/bundle_builder/`) turns a real town's OpenStreetMap streets +
DEM elevations into a five-file bundle (see
[GEODATA_BUILDER.md](GEODATA_BUILDER.md)). The in-app **NetzStudio editor**
(ported from the sibling `gridedit` interaction model) lets a user draw a network
on real streets with live DVGW load-case checking before commissioning it.

**Ports** follow the sibling scheme (netzsim 8000/5173 · rtheatflow 8001/5174 ·
**rtwaterflow 8002/5175**; compose host ports 8002/8082/8088/3002).

---

## 2. The pedagogical core: three data layers

The platform's teaching invariant is **observability**: the difference between
what is physically true, what an operator actually measures, and what can be
estimated from those measurements. Every published frame carries three strictly
separated views:

| View | German | Content |
|---|---|---|
| Reality | **Realität** | the complete physics — every junction pressure, every pipe velocity, every hidden leak |
| Measured | **Gemessen** | only what water meters (`mdot`, `p`), node pressure sensors, and station SCADA actually report — with live vs 15-minute fidelity and honest cold starts |
| Estimated | **Schätzung** | a **digital-twin observer** (M7): a *second* pandapipes net reconstructed from operator SCADA + noise-free demand priors |

The estimation layer is the heart of the observability lesson. pandapipes has no
state estimator, so the estimated view is not a filter over the truth — it is an
independent forward solve driven **only by operator knowledge**: source/tank/
pump/PRV telemetry (copied from the live dispatch) and the *expected* (noise-free)
demand for unmetered consumers. Every emitter withdrawal (leak, burst, hydrant)
is zeroed in the twin. Consequently an anomaly at an **unmetered** node stays
invisible in the estimate, while the same anomaly at a **metered** consumer
propagates — the exact teaching point. The deviation between the twin and the
measurements at sensored points is the innovation signal.

**Strict mode.** With `EXPOSE_GROUND_TRUTH=false`, the reality layer is withheld
entirely: truth-only wire keys, background leaks, and all compliance findings are
stripped from both the WebSocket frames and the recordings, leaving only the
equipment SCADA a real operator would see. This is enforced at the wire layer
(a `_TRUTH_KEYS` set), not in the UI.

---

## 3. Backend architecture (`src/rtwaterflow/`)

### 3.1 The data pipeline

A network is a **five-file bundle** of pandapipes-native JSON (see the README's
input-format section and `models.py`):

```
network_structure.json  →  junctions (name, kind, elevation_m, pn_bar, geo)
pipes.json              →  pipes (from/to, dn+material | inner_diameter_mm, k, geometry)
consumers.json          →  sinks (node, archetype kind, size, mdot, storeys)
supply.json             →  supplies (ext_grid), prvs, tanks, stations, wellfields
environment.json        →  horizon, t_air_c, day_types, dryness, season
```

The pipeline is **build once, step cheaply**:

1. **`data_loader.py`** parses + validates the bundle against the pydantic
   `models.py` schemas and runs graph-level checks: valid node references,
   exactly one pressure-fixed head per hydraulically-connected net, every
   consumer/head reachable over the pipe graph, PRV and pump-station edges are
   **cut edges** (no bypass pipe — `press_control`/constant-lift pumps produce
   fictional fields otherwise), no isolated nodes. `load_network_from_docs`
   validates an in-memory bundle (used by the editor's load-check).
2. **`pipe_catalog.py`** resolves each pipe's geometry: material → GW 303-1
   integral roughness (`k`), DN/PE-d-series → inner diameter, `length_km` from
   the polyline (haversine) when absent.
3. **`network_builder.py`** builds the pandapipes `net` **once** and a slim
   `NetIndex` (`net_inputs.py`) mapping domain names to pandapipes element rows.
   Junctions carry `height_m` (the hydrostatic term, ~0.098 bar/m); consumers are
   `create_sink`; a head source is `create_ext_grid(type="p")`; PRVs are
   `create_pressure_control`; pumps use a `StationLiftStdType` (see § 3.2); tanks
   are ext_grid + a controller.
4. **`simulator.py`** owns the realtime loop, solving one tick at a time and
   emitting a `StepResult` (the wire).
5. **`state.py`** (`StateStore`) holds the rolling history ring buffer;
   **`engine.py`** drives the tick loop; the **`api/`** routers serve REST + a
   WebSocket stream from the store.

### 3.2 The step: one honest hydraulic snapshot

`Simulator.run_step` is the core. Design choices:

- **Warm start (pn_bar only).** Each tick seeds pandapipes with the previous
  tick's pressures; `_reset_initialization` restores build-time pressures after a
  failed solve or a topology CRUD op. There is no thermal state to carry.
- **A four-tier retry ladder** (`solve_with_retry`). Non-convergence is **data,
  not an exception** — the loop never dies (never-500 discipline; a
  `PipeflowNotConverged` becomes a `converged=false` frame). The tiers escalate:
  `colebrook` at n iterations → `colebrook` at 3n → **`swamee-jain`** at 3n
  (reported `degraded`) → `nikuradse` at 3n (`degraded`). `swamee-jain` (the
  explicit Colebrook approximation, **hyphen** — the underscore silently falls
  back to nikuradse, upstream #803) rescues transitional-Reynolds ticks the
  implicit Colebrook Newton flip-flops on. Colebrook tiers pass
  `max_iter_colebrook` 100/300 (the upstream inner default of 10 fails on
  near-stagnant stubs).
- **The PDA + emitter outer fixed point.** The station loop (`_solve_hydraulic`)
  is wrapped in a Wagner **pressure-driven-demand** + emitter fixed point: each
  pass measures the consistency gap (`|factor(p) − sink.scaling|` and
  `|C·pᴺ¹ − emitter mdot|`), breaks when consistent, else applies a damped update
  and re-solves (cap 20). Undersupplied taps deliver **less** water
  (`sink.scaling`), not impossible negative pressures. A **physical-validity
  guard** downgrades a self-consistent state with negative gauge pressure to
  `degraded` — an "ok" frame never reports impossible negatives.
- **The pump station operating point.** pandapipes applies a pump-curve lift
  explicitly per Newton iteration (no `dPL/dQ` in the Jacobian) and bypasses
  reverse flow with zero resistance, so against dominant static head a running
  pump drains the tank backwards at runaway rates. The remedy (M2, runtime-pinned)
  is `StationLiftStdType` — a *constant* lift shown to the solver — plus a
  bracketed-secant operating-point search (`lift = curve(Q(lift))`) with
  EPANET-style check-valve closure when a pump reverses at shutoff head.

After the solve, **`collect_physics`** reads the pandapipes result tables into
the wire, a **compliance pass** (§ 3.5) runs in its own try/except, and the
frame is published to the store.

### 3.3 Module map

| Module | Responsibility |
|---|---|
| `models.py` | pydantic schemas for the five files (junctions, `PipeSpec`, `ConsumerSpec`, `SupplySpec`/`PrvSpec`/`TankSpec`/`StationSpec`/`WellFieldSpec`, environment) with domain validators |
| `data_loader.py` | parse + graph-validate a bundle (disk or in-memory) into `net_inputs` |
| `pipe_catalog.py` | material → `k`, DN/PE-d-series → bore, length from geometry |
| `network_builder.py` | build the pandapipes `net` + `NetIndex` once; `BAR_PER_M`, `StationLiftStdType` |
| `net_inputs.py` | `NetIndex` — the name↔pandapipes-row mapping the solver reuses each tick |
| `simulator.py` | the realtime step: retry ladder, PDA/emitter loop, station secant, validity guard, `collect_physics`, the `StepResult` wire; `solve_hydraulic` is shared with the estimator |
| `estimator.py` | the M7 `ForwardObserver` digital twin + `EstimationConfig`; `build_prior_book` (archetype/design demand priors) |
| `compliance/engine.py` | the DVGW rule pass → typed findings (see [COMPLIANCE.md](COMPLIANCE.md)) |
| `control/rules.py` | the `RuleEngine` — pump Zweipunktregelung with rule-owned running memory; operator modes auto/on/off |
| `demand/` | the M3 demand engine: `archetypes.py` (24 h shapes), `engine.py` (`build_demand_profiles`), `w410.py` (fd/fh validation) |
| `hydraulics/pda.py` | the Wagner `PDAController` (`factor(p, p_req)`) |
| `hydraulics/emitters.py` | `EmitterController` — hydrant / burst / leak (`mdot = C·max(p,0)ᴺ¹`) |
| `assets/tank.py` | `WaterTank` — ext_grid + level-integrating controller (fire reserve, overflow/empty, buffer time) |
| `assets/wellfield.py` | the raw-water side: `Aquifer`, `Well`, `WellField` (drawdown, ageing, water right, energy) |
| `loadcases.py` | the DVGW W 400-1 three load cases (LF1/LF2/LF3) — the editor's commission gate |
| `sensors.py` | the measurement layer (meters, node sensors, station SCADA; window machinery) |
| `recorder.py` / `exporter.py` | per-tick CSV packs; deterministic from-midnight offline replay (byte-compatible with live) |
| `scenarios.py` | scenario recipes (network + ops + measurements + hydraulics + clock), tolerantly replayed |
| `network_catalog.py` | the bundle library (`data/network_library.json`) + import of user bundles |
| `api/` | 13 FastAPI routers (65 routes) — see § 4 and [API.md](API.md) |
| `engine.py` / `state.py` | the tick loop + the history ring buffer / `StateStore` |
| `proc_guard.py` / `config.py` | process-identity guard for the launchers / `RTWATERFLOW_`-prefixed settings |

### 3.4 The wire and strict mode

`StepResult` (the WebSocket frame) is the single source of UI truth: `junctions`,
`pipes`, `consumers`, `producers` (ext_grid + stations), `tanks`, `wellfields`,
`emitters`, `estimated`, `findings`, and a `summary` (min-pressure worst point
over *consumer* junctions, mass balance = feed − delivered − stored − spill −
exported − emitted, deficit/emitted totals, solver status). A `_TRUTH_KEYS` set
marks the reality-only wire keys (e.g. `findings`, the full physics) that strict
mode strips wholesale; background **leak** emitters are additionally hidden — the
very minimum-night-flow an operator must *detect* — while equipment emitters
(hydrants, bursts) stay on the wire as SCADA an operator sees. The `estimated`
block **survives** strict mode — it is derived from measurements, not truth.

### 3.5 Compliance and the raw-water side

**Compliance** (`compliance/engine.py`) runs after every solve on the collected
wire and emits typed findings with a German DVGW citation
([COMPLIANCE.md](COMPLIANCE.md) maps every check): W 400-1 storey minimum
pressure, PN-10 rest pressure, velocity, hygiene stagnation (per-pipe warnings
fold into one fleet finding above a threshold so healthy nets never flood the
alarm center), tank reserve/turnover, W 405 fire flow, W 130 well ageing, WHG
water right. It runs in its own try/except so a poisoned check degrades to a
system-info finding, never discarding the converged frame.

**The raw-water side** (`assets/wellfield.py`) is pure Python — a
**Reinwasserbehälter** (break tank, `TankSpec` kind `break`) hydraulically
*decouples* wells from the pandapipes network, so wells + a linear-reservoir
aquifer + accounting are a mass balance, not a hydraulic solve. This is what
makes the Lauenau drought cascade teachable: drought → falling aquifer → capped
well yield → empty break tank → the Netzpumpe trips (low-level interlock) →
draining Hochbehälter → dry taps (via the M5 PDA).

---

## 4. Frontend architecture (`ui/`)

React + TypeScript + Vite, Leaflet/OpenStreetMap maps. `useStepStream.ts`
consumes the WebSocket frames; `views/LiveWaterFlow.tsx` splices the three views
(Realität / Gemessen / Schätzung). The map (`components/MapDiagram.tsx`) colours
nodes/consumers by pressure (DVGW traffic-light: red < 2.0 bar, green 4–6 bar,
amber ≥ 8 bar) and pipes by velocity (warn ≥ 2.0 m/s), draws flow-direction
arrows and equipment markers (⧗ PRV, ⚙ pump, 🗼 tank, 🚒/💥 emitters), and renders
the geodata attribution. Section components cover every subsystem:
`AlarmSection` (compliance), `EventSection` (hydrant/burst/leak + PDA toggle),
`TankSection`, `WellFieldSection`, `EnvironmentSection` (Hitzetag),
`ConsumerTableSection`, `DrucklinieSection` (a client-side Dijkstra HGL with the
PRV step visible), `MeasurementPanel`, `WorstPointSection`. `views/NetzStudio.tsx`
hosts the catalog + import + the **editor** (`editor/`: `model.ts`,
`streetGraph.ts` street routing, `EditorMap.tsx`, `NetzStudioEditor.tsx`).
`i18n.ts` enforces DE/EN key parity at compile time (`const en: typeof de`).

---

## 5. Persistence & artifacts

| Where | What |
|---|---|
| `data/networks/<id>/` | the seven committed teaching bundles (five JSON files each) |
| `data/network_library.json` | the catalog index (id, name, character, node/pipe stats) |
| user networks dir | bundles imported at runtime (`character` `user`) |
| `data/recordings/` | per-session CSV packs (one file per wire table) + a metadata recipe |
| in-memory ring buffer | the `StateStore` rolling history (`HISTORY_SIZE` frames) |
| InfluxDB (`:8088`) + Grafana (`:3002`) | live telemetry via the `collector` service, for time-series dashboards |
| `tools/bundle_builder/snapshots/` | pinned OSM/DEM snapshots for the geodata bundles |

Recordings and the offline exporter are **byte-compatible**: `prepare_replay`
runs a deterministic from-midnight replay (windows reset, warm state cold-init,
the non-deterministic observer force-disabled) so an exported day reproduces a
live recording of the same day file-for-file.

---

## 6. The geodata pipeline

`tools/bundle_builder/` is an offline two-step pipeline: an **online**
`make_snapshot` freezes a town's projected OSM streets + sampled DEM elevations
to a pinned JSON, and an **offline, deterministic** `build_from_snapshot`
synthesises the five-file bundle from it with only networkx + the stdlib. Three
shipped bundles use it — `alpen` (flat), `neubeuern` (hilly, PRV-zoned), and
`kevelaer` (city-scale). Full detail in [GEODATA_BUILDER.md](GEODATA_BUILDER.md).

---

## 7. Testing & CI

**268 backend (pytest) + 36 UI (vitest) tests**, plus `tsc` strict + `vite
build`. Test categories: the five-file **data contract** + loader guards; per-
milestone **acceptance** (hydraulics, tanks/pumps, demand, compliance, PDA/
emitters, wells, observer, geodata, load cases, performance); **cross-tool
oracles** (`tests/validation/` EPANET Net1/Net3, the tank + hydrant oracles, PDA
vs WNTR PDD — see [BENCHMARKS.md](BENCHMARKS.md)); **determinism/regression**
(byte-stable generators + snapshots, recorder↔export byte-compat); the API
**surface pin** (`test_api_surface.py`, 65 routes) which regenerates
[API.md](API.md) in lockstep; and the observability **tripwires** (an anomaly at
an unmetered node must not appear in the estimate).

Every milestone (and each M8/M9 sub-stage) passed through a **multi-agent
adversarial-review workflow** (2–3 independent lenses + per-finding verification)
with every confirmed finding fixed and regression-pinned before commit.

CI (GitHub Actions) installs `requirements.txt` + the `dev` extra (which carries
the test-only `wntr` oracle) and gates the three Docker image builds (backend,
ui, collector; InfluxDB/Grafana are pulled).

---

## 8. Running it

```bash
# Windows (recommended): two consoles + browser
start_rtwaterflow.bat          # backend :8002, UI :5175
stop_rtwaterflow.bat

# Local Python
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt -e .[dev]
.venv\Scripts\python -m rtwaterflow.main         # backend :8002
cd ui && npm install && npm run dev              # UI :5175 (proxies /api, /ws)

# Docker Compose: backend :8002, UI :8082, InfluxDB :8088, Grafana :3002
docker compose up -d
```

Configuration is via `RTWATERFLOW_`-prefixed environment variables (see the
README and `config.py`): `DEFAULT_NETWORK`, `STEPS_PER_DAY`,
`STEP_INTERVAL_SECONDS`, `AUTOSTART`, `EXPOSE_GROUND_TRUTH` (strict mode),
`SOLVER_ITER`, `PDA_ENABLED`, `RECORD`.
