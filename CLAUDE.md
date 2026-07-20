# CLAUDE.md — rtwaterflow development log & agent handoff

## What this repo is

Real-time **drinking-water network** simulation platform on **pandapipes
0.14.0**, forked 2026-07-20 from **[markisbell/rtheatflow](https://github.com/markisbell/rtheatflow)**
(district heating), which itself was cloned from the blueprint
**markisbell/rtpowerflow** (project "netzsim").

**The binding build specification is [`../IMPLEMENTATION_ROADMAP.md`](../IMPLEMENTATION_ROADMAP.md)**
(milestones M0–M9 with acceptance criteria) together with
**[`../TECHNICAL_FOUNDATIONS.md`](../TECHNICAL_FOUNDATIONS.md)** (adversarially
verified DVGW/DIN constraints, pandapipes capabilities & gaps, German
water-supply engineering — read §8 pandapipes gaps and §2 violation rules
before writing domain code). The in-repo `SPEC.md` and `docs/ARCHITECTURE.md`
are STALE fork-parent documents kept for platform-architecture reference only.

## Binding rules (inherited from the fork parent)

1. **Commit/push only on the user's request in interactive sessions**
   (milestone work is committed under explicit per-milestone authorization).
2. Every milestone ends with working, tested, committed code before the next
   begins. Pins are **re-derived at runtime** on the pinned dependency
   versions, never copied from docs.
3. German-first UI with domain vocabulary (Schlechtpunkt, Hochbehälter,
   Wasserzähler); `ui/src/i18n.ts` enforces DE/EN key parity via
   `const en: typeof de` (tsc TS2741).
4. Never-500 discipline: solver non-convergence is data (`converged=false`
   frames), the engine loop never dies; every route change re-pins
   `tests/test_api_surface.py` AND regenerates `docs/API.md`
   (`python scripts/gen_api_doc.py`) in the same commit.

## Non-negotiable technical pins (water platform)

- `pandapipes==0.14.0` — do **not** pin pandapower separately (0.14.0 pins
  `pandapower==3.3.3` itself).
- Solve: `pipeflow(net, mode="hydraulics", friction_model="colebrook")`.
  **Never `"nikuradse"` as the primary model** (upstream issue #803: laminar
  + turbulent λ are ADDED instead of regime-selected — low-Re bias). The
  third model's string literal is `"swamee-jain"` (**hyphen**; `swamee_jain`
  silently falls back to nikuradse) — reserved for EPANET cross-validation.
- Elevation lives in `junction.height_m` (from the bundle's `elevation_m`,
  DHHN2016 metres); `junction_geodata` is plotting-only. ~0.0981 bar per
  metre of elevation — the platform's core teaching physics.
- Exactly one pressure-fixed node (ext_grid, wire kind `"slack"`) per
  hydraulically connected net in M0; every island needs a head source or
  pipeflow CRASHES (connectivity guard before valves arrive in M1+).
- Warm start is **pn_bar only** (no thermal state); `_reset_initialization`
  restores build-time pressures.
- Demands are FIXED mass flows in M0 — undersupply produces impossible
  negative pressures, not dry taps, until the M5 PDA controller
  (`sink.scaling = clip((p−p_min)/(p_req−p_min),0,1)^0.5`, fixed-point).
- Tanks are ext_grid + level-integrating controller (M2 `WaterTank`);
  `mass_storage` does nothing hydraulically. No parallel pump branches
  (upstream issue #693). `press_control` has no PRV state machine — always
  pair with supervisory logic (M2 zones).
- Retry ladder: 3 tiers (colebrook n → colebrook 3n → nikuradse 3n =
  "degraded"); the tier count is pinned in `tests/test_retry_ladder.py` AND
  the poison count of `tests/test_nonconvergence.py` — change in lockstep.
- Sibling port scheme: netzsim 8000/5173 · rtheatflow 8001/5174 ·
  **rtwaterflow 8002/5175** (compose host ports 8002/8082/8088/3002).
  `stop_rtwaterflow.bat` matches window titles and the `rtwaterflow.main`
  cmdline marker — keep in lockstep with `proc_guard.MARKER`.

## Development log

### 2026-07-20 — Project inception + M0: fork & strip (branch `m0-fork-strip`)

**Research + roadmap** (before any code): a 20-agent workflow researched
German drinking-water engineering (DVGW W 400-1/405/410, wells, demand,
failure modes, pandapipes gaps, EPANET/WNTR parity) with adversarial
verification of every load-bearing number; synthesized into
`../TECHNICAL_FOUNDATIONS.md` + `../IMPLEMENTATION_ROADMAP.md`.

**M0 built** (roadmap §6 M0; 9-agent codebase-mapping workflow over the fork
parent preceded the refactor):

- **Fork & rename**: `src/rtheatflow` → `src/rtwaterflow`, env prefix
  `RTWATERFLOW_`, ports 8002/5175, launchers/compose/CI/collector/Grafana
  renamed; GHCR image path `ghcr.io/<owner>/rtwaterflow/*`.
- **Thermal domain deleted**: heating_curve, dp_control, storage (thermal
  buffer), producers (PlantModel), consumers (VDI/OpenDHW archetypes),
  weather (degree-hour scaling), loadgen package, api/{weather,plant,storage},
  all 7 thermal reference networks + data/profiles + data/sources +
  converter/generator scripts, 16 thermal test files, demandlib/opendhw deps.
- **Single pipe layer**: `network_builder.py` rewritten — one node = one
  junction (with `height_m`), one entry = one pipe (explicit `k_mm`),
  consumers = `create_sink`, supply = `create_ext_grid(type="p")`; NetIndex
  slimmed (junction dict, pipes array, ext_grid, producer_meta pid scheme
  kept); ProfileArrays = `mdot_kg_per_s [n_cons, T]` + `t_air_c` (M3 slot).
- **Simulator rewritten** (platform skeleton verbatim): 3-tier hydraulic
  retry ladder, `collect_physics(net, idx)` module-level (estimator seam),
  hydraulic wire — junctions `{id,name,p_bar}`, pipes
  `{id,trench,mdot,v,dp}`, consumers `{...,mdot_demand,mdot,p_bar}`, summary
  min-pressure worst point **over consumer junctions only** (the source tank
  legitimately sits at 0.5 bar and must not masquerade as the worst point);
  producers = ext_grid entry (wire kind `"slack"`); StepResult lost
  storages/weather; controls = `{blind_spot}`. Consumer CRUD adapted
  (add/remove sink, last-consumer 409 kept), hx/pump/storage/bypass CRUD
  deleted.
- **Estimator stubbed** (~50 lines): `EstimationConfig(enabled=False)`
  + no-op `ForwardObserver` — engine re-apply plumbing, `/estimation/config`
  routes and the UI Schätzung segment stay wired for the M7 water observer.
- **Sensors**: water channels — meter `(mdot_kg_per_s, p_bar)`, node
  `(p_bar,)`, source SCADA `{mdot, p}`; observed_summary = metered-only
  min-pressure worst point; 15-min window machinery byte-identical.
- **Recorder/exporter**: hydraulic column plans, storages.csv gone,
  transient replay machinery deleted; `prepare_replay` = windows reset +
  pn_bar cold init; live-vs-export byte-compat discipline kept & re-tested.
- **API**: 44 routes (was 61), API_VERSION 0.1.0; `POST /consumer` takes
  `{node, name?, mdot_kg_per_s}`; scenarios recipe = network + consumer_ops
  + measurements + clock; `/networks/import` takes the water five-file set;
  monitor tiles hydraulic; docs/API.md regenerated.
- **Contract**: five files `network_structure/pipes/consumers/supply/
  environment` (horizon moved to environment.json); loader keeps node-ref/
  exactly-one-slack/reachability/isolated checks, **drops the dead-end
  guard** (hydraulics-only dead ends are legal; stagnation is an M4
  compliance finding, not a solver singularity).
- **Bundle `tutorial_hillside`**: the pandapipes `height_difference` tutorial
  as a five-file bundle (5 junctions 346–400 m, 4 pipes, sinks 0.277+0.139
  kg/s, ext_grid 0.5 bar @ 400 m "Hochbehälter"), geo-anchored in the
  Odenwald (49.46 N, 8.98 E — hilly terrain matching the elevations).
- **UI**: `LiveHeatFlow` → `LiveWaterFlow`; layers pressure|velocity;
  `pressureColor` DVGW-anchored (red < 2.0 bar, green plateau 4–6, amber
  ≥ 8; domain 0–10 bar), velocity re-anchored warn 2.0 / max 3.5 m/s
  (W 400-1); MapDiagram single-pipe restyle + junction-pressure lookup by
  name (node markers now restyled per frame); popups demanded/delivered +
  p_bar; HeatingCurve/Weather sections deleted; ElementMenu = pin + meter/
  sensor + consumer; NetzStudio = catalog + import + hydraulic KPIs;
  i18n water vocabulary DE/EN.
- **Docs**: README + Benutzerhandbuch (served at `/manual`) rewritten for
  water; SPEC.md/ARCHITECTURE.md got stale-fork-parent banners;
  BENCHMARKS.md deleted.

**Tests: 83 backend passed** (first full run, 22.9 s; ~65 predicted) **+ 18
UI vitest**; `tsc && vite build` green.

**Acceptance evidence (roadmap M0):**

1. *Elevation regression* (the M0 bar): local colebrook baseline on this
   machine — j1 5.194293 / j2 4.607446 / j3 4.314019 / **j4 5.781010** /
   j5 0.5 bar; matches the upstream tutorial's stored nikuradse outputs
   within 0.00002 bar (friction is sub-centibar at 0.024 m/s). Pinned at
   ±0.01 in `tests/test_tutorial_hillside.py`; hydrostatic cross-check
   0.0981 bar/m; worst point = j3 (the HIGHEST consumer, 361 m) — the
   Druckzonen teaching point works on day one.
2. *Wire/balance*: feed 0.416 kg/s = demand = delivered, balance 0; tier-1
   convergence; warm start reproduces step 2 exactly.
3. *End-to-end*: engine → WS → map verified via TestClient WS stream test
   (frames converged, 5 junctions) + live browser run (below).

**Discoveries:**

1. `res_sink` rows must be read `.loc[element]` (label), not positionally —
   same index-reuse discipline as every other pandapipes result table.
2. TS 5.6 `in`-narrowing on the `ConsumerState | ConsumerMeasurement` union
   yields `{}` for the unlisted-property access pattern — look up the truth
   array separately instead of narrowing the union (EquipmentControls).
3. The nikuradse fallback tier converges on the hillside net when colebrook
   tiers are poisoned and honestly reports `status="degraded"` — the
   ok|degraded|failed vocabulary survives without the thermal sequential
   mode.

**Deviations (documented, deliberate):** `weather.py` deleted rather than
reduced (the /weather override knob returns with the M3 demand engine —
environment.json already carries the horizon + t_air_c); estimation routes
KEPT (stub) although the API map suggested deletion — engine/UI/scenario
plumbing stays coherent at the cost of 2 honest stub routes; dead-end
loader guard dropped (legal in hydraulics; M4 compliance finding);
`controls` carries only `blind_spot` (no controller exists yet).

- Next: **M1 — hydraulic core + first real bundle** (roadmap §6): full
  bundle schema (§3), pipe catalog DN/material→k defaults, demo bundle
  "Musterdorf" (~40 nodes, tank on hill, two zones via PRV, ring + branch,
  mixed consumers), pressure/velocity/direction layers, Drucklinie view.
