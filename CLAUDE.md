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
- Retry ladder: 4 tiers since M3 (colebrook n → colebrook 3n →
  swamee-jain 3n = "degraded" → nikuradse 3n = "degraded"); swamee-jain is
  the explicit Colebrook approximation and rescues transitional-Reynolds
  ticks the implicit model's Newton cannot solve. Tier count pinned in
  `tests/test_retry_ladder.py` AND the poison count of
  `tests/test_nonconvergence.py` — change in lockstep. Colebrook tiers pass
  `max_iter_colebrook` 100/300 (upstream inner default of 10 fails on
  near-stagnant stubs).
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

### 2026-07-20 — M1: hydraulic core + Musterdorf (branch `m0-fork-strip`)

**Built** (roadmap §6 M1):

- **Pipe catalog** (`pipe_catalog.py`): material → GW 303-1 integral
  roughness defaults (PE/PVC 0.1, GGG/St/AZ 0.4, GG 1.0 mm); plastics are
  d-series OUTER diameters with SDR 17 / PN 10 bore tables (PE d110 →
  96.8 mm), metallic ID ≈ DN. `PipeSpec` resolves dn+material XOR explicit
  inner_diameter_mm at validation; explicit k always wins; `length_km`
  derives from the geometry polyline (haversine) when absent.
- **PRVs** (`PrvSpec` in supply.json → `create_pressure_control` holding
  p_out at the outlet): static Durchlauf zone boundaries; producer_meta
  kind `"prv"`; the wire carries honest STATION-SCADA telemetry —
  `p_set_bar` (config) vs SOLVED `p_out_bar`/`p_in_bar`, SIGNED
  `mdot_kg_per_s`, and `reducing: false` flags the press_control failure
  modes (boosting/back-feed) M2 supervision will act on. Loader: PRV edges
  count for reachability, PRV must be a CUT edge (bypass pipes rejected),
  geometry ends must anchor to their nodes (≤ 50 m).
- **Consumer metadata**: `kind` (residential/industry/farm/school/pool/…)
  + `storeys` ship now — M3 archetypes and M4 per-storey minimum pressure
  need no bundle rewrite.
- **Musterdorf** (`scripts/generate_musterdorf.py`, deterministic, output
  committed + byte-stability-tested): 33 nodes / 32 pipes / 26 consumers /
  3.885 km in the Odenwald. Hochbehälter Musterberg 420 m (0.4 bar) →
  Hochzone 363–385 m (branched) → Druckminderer Talstraße (345 m,
  p_out 2.8 bar) → Tiefzone 303–332 m (ring core incl. legacy GG k=1.0
  segments + branched fringes). Mixed stock PE/GGG/GG, 1965–2018.
- **UI**: flow-direction arrows (create-once midpoint glyphs, rotated by
  polyline bearing ± flow sign, hidden when unknown/measured-view), PRV
  station marker + popup (In→Out (Soll), ⚠ when not reducing; PE pipes
  labeled "PE d110", never "DN 97"), **DrucklinieSection** — client-side
  Dijkstra (trenches + PRV pseudo-edges) → SVG terrain + HGL
  (elevation + p·10.197 m); the PRV head drop renders as a visible step.
  `shortestPath` exported + vitest-covered.

**Tests: 105 backend** (x2 back-to-back, zero strays) **+ 22 UI vitest**;
tsc strict green; API.md regenerated (44 routes unchanged).

**Acceptance evidence (roadmap M1):** Musterdorf converges tier 1, warm
solves median ~31 ms (< 50 ms bar, pinned as median-of-10); mass balance
0.0 % (< 0.1 % bar); zone pressures high 3.82–5.94 / low 4.07–6.90 bar with
both zone MEDIANS inside the 4–6 bar band (pinned); every consumer ≥ 2 and
≤ 8 bar; v_max 0.39 m/s; PRV holds 2.8 bar passing 2.44 kg/s (pinned incl.
recorder round-trip); generator byte-stable. Verified live end-to-end:
Musterdorf applied via /config/apply, 32 arrows rendering, PRV popup with
honest telemetry, Drucklinie to the farm shows the 47-px PRV step
(303–424 m over 1.55 km), zero console errors.

**Adversarial review** (3 lenses + per-finding verification, 16 agents):
13 findings confirmed, 0 refuted — all fixed before commit: signed PRV mdot
+ `reducing` flag + solved p_out (wire honesty), geometry anchoring +
PRV-cut-edge loader checks, producers.csv PRV columns, PE d-series popup
labels, NetIndex.prvs default_factory, GET /producers PRV enrichment,
ring-test ids resolved from the contract (never hardcoded), topology
prvs/dn/material pinned, musterdorf catalog stats pinned, Dijkstra
exported + unit-tested, strict-mode station-SCADA doctrine documented +
pinned.

**Discoveries:**

1. press_control failure modes are REACHABLE on Musterdorf (review-verified
   empirically): a ~20-25 kg/s fire-flow-scale consumer in the low zone
   collapses the inlet head and the PRV silently boosts (deltap +2.55 bar,
   frame "ok"); a bypass pipe yields −121 kg/s reverse valve flow. The M1
   wire now exposes both (`reducing`/signed mdot); M2 supervision closes
   the valve.
2. Wire pipe id == pipes.json row index is the load-bearing invariant for
   tests — resolve ids from the contract (`pos[(from,to)]`), never
   hardcode.

**Deviation (documented):** the roadmap's M2 "worst-point service-pressure
controller" is deferred until a pressure-setpoint actuator exists (DEA,
M5/M6) — a gravity+PRV net has nothing to actuate; M2 ships tank-level
hysteresis instead (the canonical German pattern per TF §5).

### 2026-07-21 — M2: tanks, pumps, rules (branch `m0-fork-strip`)

**Built** (roadmap §6 M2):

- **Contract**: supply.json gains `tanks` (TankSpec: area, level band, fire
  reserve, kind durchlauf|gegen) and `stations` (StationSpec: ≥ 3-point
  Q-H curve, control hysteresis|manual); environment.json gains
  `demand_factor` (len == steps, > 0 — the interim diurnal profile until
  M3). Multi-source nets legal since M2 (≥ 1 ext_grid or tank; node
  collisions, duplicate station names, parallel station branches (#693),
  pipes bypassing a station (cut-edge, like the M1 PRV rule), consumers on
  head-source nodes, and non-monotone/unfaithful curve FITS all rejected
  loudly at load).
- **WaterTank** (`assets/tank.py`): ext_grid + level-integrating controller
  (write_p pre-solve = level·BAR_PER_M; explicit-Euler integrate post-solve
  from res_ext_grid, EPANET EPS semantics; clamps with overflow/empty
  flags; spill split out as `mdot_spill_kg_per_s`); KPIs usable/capacity,
  fire_reserve_breached, buffer_time_h (None while filling).
- **Pump stations — the M2 numerics discovery** (runtime-pinned in
  `test_pandapipes_pins.py`): pandapipes pump std_types assume zero-lift
  ZERO-RESISTANCE bypass for reverse flow AND apply the curve lift
  explicitly per Newton iteration (no dPL/dQ in the Jacobian). Against
  dominant static head that fixed-point diverges into the reverse-bypass
  sink: the Hochbehälter drains BACKWARDS through the running pump at
  −51 kg/s (runtime-verified on Musterdorf). Remedy:
  `StationLiftStdType` shows the solver a CONSTANT lift;
  `Simulator._solve_step` finds the honest operating point
  `lift = curve(Q(lift))` by a bracketed secant on [0, shutoff]
  (forward-seen tracking, ONE max-effort try per tick, reverse iterates
  narrow the bracket only; ≤ MAX_STATION_SOLVES=16, exhaustion honestly
  "degraded"), with EPANET-style check-valve closure (`cv_closed`) when a
  pump reverses at shutoff head. Curve VALIDATION checks the fitted
  polynomial, not the points (monotone on [0, 1.2·Qmax], residual bounded)
  — pandapipes runs on the fit.
- **RuleEngine** (`control/rules.py`): Zweipunktregelung with rule-OWNED
  running memory (never read back from in_service — CV closures would
  latch); operator modes auto|on|off per station (manual stations: auto =
  configured state, so a closure can never latch), scenario-saved and
  tolerantly replayed; `reset_operations` restores levels, modes, rule
  memory AND the cold-start lift seed (deterministic replay).
- **Wire honesty**: StepResult gains `tanks` (station SCADA — survives
  strict mode; id = platform pid); station entries running/mode/cv_closed/
  p_in/p_out/mdot; slack mdot SIGNED (positive = supplying — abs() hid
  absorbing slacks on multi-source nets); summary splits positive ext_grid
  flows by element kind: `mdot_stored` (level-effective tank charge),
  `mdot_spill` (overflow clamp), `mdot_exported` (absorbing boundaries);
  balance = feed − delivered − stored − spill − exported. producers.csv
  + new tanks.csv mirror the wire 1:1.
- **API**: 47 routes (+GET /tanks, +GET /stations incl. hysteresis band +
  curve, +POST /station/{name} auto|on|off with 404/422); topology gains
  `stations`; tank-only bundles preview/import cleanly (first-head-source
  fallback).
- **Bundles**: Musterdorf grown to 35 nodes (Wasserwerk 336 m → Pumpwerk
  [[0,10],[15,9.4],[30,8.4],[45,6.8]] → Steigleitung DN150 → Hochbehälter
  Musterberg 60 m², band 2.4/4.2, 48 m³ Löschreserve → the M1 zones) +
  24-value diurnal factor; NEW bundle **Mustertal** (8 nodes, GEGEN: pump
  west, Wasserturm 330 m east, 20 m², band 1.2/3.4 — the reversal segment
  wt5→twr flips sign over the day). Both deterministic + byte-stable.
- **UI**: TankSection (SVG level gauge with dead band, Löschreserve band,
  hysteresis switch marks from GET /stations, live fill; buffer countdown,
  alarm lines; station rows with running lamp, CV alarm, Auto/Ein/Aus
  segment — mode reads controls.stations + optimistic overlay, so paused/
  failed states still confirm operator input); MapDiagram station ⚙️
  markers (green/grey/red-CV) + tank 🗼 markers (alarm ring) with live
  popups; Drucklinie crosses station pseudo-edges (tank fallback source);
  shared density-true constants M_PER_BAR/M3H_PER_KG_S (rho 998.2, not
  1000); i18n tank.* DE/EN.

**Tests: 140 backend ×2 + 23 vitest**; tsc strict + vite build green;
API.md regenerated (47 routes).

**Acceptance evidence (roadmap M2):** Musterdorf 24 h: sawtooth 2.39–4.24 m
in the 2.4/4.2 band, duty 36/96, 3 switches, mass balance exact, every
frame "ok"; Mustertal: reversal segment +5.7/−3.2 kg/s over one day, band
1.2/3.4 held; tank mass balance ∫mdot·dt = ΔV·ρ < 0.5 % (both bundles);
**EPANET/WNTR oracle** (same fitted curve sampled into EPANET, D-W, rule
controls): level RATES match to ~1 mm/tick in state-matched ticks (pinned
1 cm median), switching counts ±1, envelopes equal — remaining divergence
is EPANET's sub-step event timing (documented in test_tank_oracle.py).
Live E2E verified: tank widget with band marks, mode override round-trip
(incl. while paused), CV/runaway never on the wire, zero console errors.

**Adversarial review** (3 lenses + per-finding adversarial verification,
21 agents): 18 confirmed, 0 refuted — all fixed + regression-pinned before
commit: the shutoff ping-pong (cold starts on marginal pumps exhausted the
solve cap), unvalidated curve fits, the manual-station auto latch, slack
abs()/stored dishonesty on multi-source nets, duplicate station names,
station bypass + parallel-branch + consumer-on-source loader gaps, spill
booked as stored, tank wire id ≠ pid, tank-only preview 500, stale mode
display, rho-1000 UI drift, dead /stations surface (now feeds the gauge
band marks), tanks.csv column gaps, tautological envelope asserts.

**Deviation (documented):** stations must be CUT edges (no parallel pipe
around a Pumpwerk) until check-valved bypass piping exists (M5 emitters/
valves); EPANET-style sub-tick rule timing is deliberately NOT emulated
(15-min SCADA switching is the teaching model).

### 2026-07-21 — M3: demand engine (branch `m0-fork-strip`)

**Built** (roadmap §6 M3, §4.8; every number from TF §6):

- **Contract**: ConsumerSpec gains archetype kinds (residential_city/
  _village, farm_dairy/_pigs; M1 generics alias residential→village,
  farm→dairy) + `size` (population/employees/pupils/beds/animals/
  visitors_design — ≥ 1 field required, an empty object is rejected);
  EnvironmentFile gains `day_types` (per horizon day, default
  Monday-anchored week), `dryness` (0..1 per day) and
  `season_day_of_year` (1..365 — the engine runs an idealized 365-day
  year). `demand_factor` is the legacy fallback.
- **demand/ package**: archetypes.py (hand-tuned 24 h shapes normalized
  to mean 1.0: village/city residential incl. weekend behavior, industry
  shift block, school pulses, office, hospital, dairy milking pulses
  05–07/16–18 with temperature coupling capped 2× at ~29 °C, pigs, pool
  with May–Sept season, visitor-weather coupling and the nightly 02:00
  DIN 19643 backwash pulse) + engine.py (`build_demand_profiles`:
  [n_cons, T] = base × shape (hourly → tick linear interp, wrap-around)
  × day factor (day type × season ± 8 % × temperature) + the 2018 hot-dry
  irrigation surge (Tmax ≥ 28 °C AND dryness ≥ 0.5: evening block 19–21 h
  + elevated night flows, roughly doubling extreme-day volume) × seeded
  noise (crc32(name) — stable across processes; hash() is salted)).
  Consumers WITHOUT size take the legacy path bit-exactly.
- **W 410 validation** (demand/w410.py + tests): a synthetic year over
  the Musterdorf population (E = 1577) lands within ±20 % of
  fd = 3.9·E^−0.0752 and fh = 18.1·E^−0.1682 — validation TARGETS, never
  inputs (the small-area overestimation is the documented teaching note).
- **Retry ladder → 4 tiers**: colebrook n (max_iter_colebrook=100) →
  colebrook 3n (300) → **swamee-jain 3n** ("degraded", explicit Colebrook
  approximation) → nikuradse 3n ("degraded", #803). Discovery: the noisy
  M3 demands park several pipes at transitional Re (1700–3800) on ~2 % of
  ticks — the implicit Colebrook Newton flip-flops across the laminar/
  turbulent switch there while swamee-jain (no inner Newton) converges;
  the upstream inner-lambda default of 10 iterations also failed
  near-stagnant stubs (fixed via max_iter_colebrook). The swamee-jain
  divide-by-zero at Re = 0 is silenced via np.errstate (benign λ→0).
- **Weather knob**: `Simulator.set_environment` (t_offset_c,
  dryness_override) rebuilds profiles BY CONSUMER IDENTITY (build-name
  matching — positional writes after runtime CRUD corrupted rows;
  review-critical) + GET/POST /environment (49 routes; never-500
  guarded); scenario recipes save/replay the overrides;
  `reset_operations` normalizes them like station modes (one doctrine);
  recording metadata.json records them (reproducibility recipe).
- **Bundles**: musterdorf regenerated as the archetype showcase (sizes on
  all 26 consumers, mid-July summer day 12–26 °C, dryness 0.3,
  demand_factor dropped; day volume ≈ 288 m³); mustertal +
  tutorial_hillside deliberately stay legacy-path bundles.
- **Wire/topology honesty**: GET /network Anschlusswert = the spec's MEAN
  base demand (matched by build name), never the tick-0 engine value.
- **UI**: EnvironmentSection (live t_air, Normal/Hitzetag segment with
  three-way state incl. "custom override" honesty, write-sequence guard
  against the 5 s poll race, 🔥 badge, legacy note), ConsumerTableSection
  (archetype icons, Soll/Ist m³/h via M3H_PER_KG_S, pressure-colored,
  measured-view = metered-only with honest "—" demanded), all seven
  Leaflet tooltips esc()'d (stored-XSS via imported bundle names —
  popups already escaped since M0), remaining ×3.6 conversions replaced
  by M3H_PER_KG_S (consumer popup, Overview).

**Tests: 155 backend ×2 + 23 vitest**; tsc strict + vite build green;
API.md regenerated (49 routes). Warm-solve bar consciously re-pinned
50 → 80 ms (review-verified: the pristine M2 commit also medians ~50 ms
on this host today — ambient machine load, not a regression; 80 ms keeps
> 12× real-time headroom).

**Acceptance evidence (roadmap M3):** synthetic year fd 1.9–2.1 /
fh in-corridor vs W 410 for E = 1577 (±20 % pinned); Hitzetag
(t_offset +6, dryness 0.9) shifts the aggregate peak to 19:45 at > 1.5×
the normal peak with elevated night flows; the pool backwash pulse is
visible in the Hochbehälter drawdown (pinned on the tank wire mdot);
profiles deterministic; legacy bundles bit-exact. Live E2E: Umwelt
section round-trips Hitzetag (incl. while paused), 26-row consumer table
with archetype icons, zero console errors.

**Adversarial review** (3 lenses + per-finding verification, 20 agents):
15 confirmed / 2 refuted — all fixed + regression-pinned before commit:
the identity-rebuild critical (consumer CRUD + POST /environment = 500 or
silently cross-wired demands), metadata.json environment block, replay
normalization doctrine, topology Anschlusswert, tooltip XSS, empty-size
and doy-366 contract holes, dryness-null docstring, hot-state derivation,
poll race, ρ-1000 stragglers, errstate, warm-solve re-pin.

### 2026-07-21 — M4: compliance engine + alarm center (branch `m0-fork-strip`)

**Built** (roadmap §6 M4, §4.9; rule values from TF §2/§4):

- **compliance/ package**: post-solve rule pass on the collected wire
  payload emitting typed findings `{severity, rule, check, entity_kind,
  entity, value, threshold, since_ticks, text_de}` — each with a German
  DVGW citation (`docs/COMPLIANCE.md` maps every check). Checks: `p_min`
  (W 400-1 2.0 + 0.35/storey; ≤ 0.5 bar AND < 1 h = warning band, else
  violation; per-consumer sustained counters), `p_rest` (8 bar Ruhedruck
  warning SCOPED to consumer junctions, 10 bar PN-10 violation on all
  junctions), `v_max` (> 2.0: warning momentary / violation ≥ 1 h),
  `stagnation` (per-pipe hour-mean < 5 mm/s hygiene warning, skipped on
  off-station riser feet; daily self-cleaning AGGREGATED into one fleet
  finding), `tank_reserve`/`tank_empty`/`tank_overflow`, `tank_turnover`
  (day-mean volume / day-mean |exchange| > 24 h), `solver` (degraded =
  info). Rolling state (per-pipe |v| ring + counters, per-consumer below
  counters, per-tank flow/volume rings); `reset()` on replay.
- **Wire**: `StepResult.findings` is a TRUTH key (`_TRUTH_KEYS` → 5;
  strict mode strips it). `GET /findings` (50 routes) serves findings +
  severity counts + `truth_hidden` + `converged`/`solver_status`
  staleness. Compliance runs in its OWN try/except after `_collect` (a
  poisoned check degrades to a system info finding, never discards the
  converged frame). `findings.csv` in every recording.
- **UI**: AlarmSection (grouped by severity, rule citations, 🔴/🟡
  badges; measured-view + strict-mode + non-converged honesty — never a
  fake ✅); MapDiagram red/amber alarm halos (diffed decorative rings,
  suppressed in the measured view); i18n `alarm.*` DE/EN.

**Tests: 171 backend ×2 + 23 vitest**; tsc strict + vite build green;
API.md regenerated (50 routes). Seeded fixtures each produce exactly
their finding: undersized DN50 branch + 4.5 kg/s → one `v_max` violation;
source at 4.0/5.5 bar → `p_rest` warning/violation; dead-end stub →
`stagnation`; 8-storey consumers → warning band then sustained violation;
drained Hochbehälter → `tank_reserve` + `tank_empty`.

**Adversarial review** (3 lenses + per-finding verification, 26 agents):
22 confirmed / 1 refuted — all fixed + regression-pinned before commit.
The two headline fixes: (1) **alarm flood** — the healthy showcase net
emitted ~28 identical per-pipe stagnation ambers per tick (fire-capable
rural sizing → most branches under 0.3 m/s daily); now ONE aggregated
self-cleaning finding + off-station-riser exemption → ≤ 6 findings/tick,
so seeded anomalies stand out. (2) **p_rest false positive** — the
Pumpwerk discharge node `ws` (~8.6 bar while pumping, by construction)
carried a permanent uncancellable Ruhedruck warning; the 8-bar band is
now scoped to consumer junctions (a riser is transport infrastructure, not
a house connection). Plus: runtime-added consumers now registered for the
p_min check (were silently exempt), duplicate consumer names rejected
(merged sustained counters), tank_turnover on |exchange| not net draw (a
Durchlauf tank flapped), ceil ticks-per-hour, compliance-exception guard,
/findings staleness, measured-view honesty, aggregated-vs-per-pipe pins,
German decimal separators, language-toggle halo restyle.

**Deviation (documented):** an export replay of a mid-session day starts
its rolling windows COLD, so its `findings.csv` differs from a warm live
pack of the same day (the export is a deterministic from-midnight replay —
noted in `docs/COMPLIANCE.md` + the exporter docstring). Fire-flow checks
at hydrants, loss KPIs, water-right and surge advisories are deferred to
their milestones (M5/M6/M9).

### 2026-07-21 — M5: PDA, emitters, scenario library (branch `m0-fork-strip`)

**Built** (roadmap §6 M5, §4.3/§4.4; TF §7/§8):

- **PDAController** (`hydraulics/pda.py`): Wagner pressure-driven demand.
  `factor(p) = 0` at `p ≤ p_min` (0.5 bar), `((p−p_min)/(p_req−p_min))^0.5`,
  `1` at `p ≥ p_req` (the W 400-1 storey requirement, read live from the
  M4 compliance table). Undersupplied taps deliver less (`sink.scaling`)
  instead of the M0 fixed-demand negative-pressure artefact; a
  `pda_enabled` toggle keeps the "why PDA" contrast teachable.
- **EmitterController** (`hydraulics/emitters.py`): one
  `mdot = C·max(p,0)^N1` mechanism — hydrant (C from a target flow at the
  node's pressure, floored at a plausible service head, capped at the
  rated target; N1 0.5), burst (`C = Cd·A·√(2ρ)`, Cd 0.75), leak (FAVAD
  N1 1.15, one per NETWORK junction ∝ incident pipe length; head sources
  excluded). add/remove/clear + absolute-tick expiry.
- **The M5 solve strategy** (`Simulator._solve_step`): the M2 station loop
  is now `_solve_hydraulic`, wrapped in the PDA+emitter outer fixed point —
  each pass measures the Wagner CONSISTENCY GAP
  (`max|factor(p)−scaling|` + `max|C·p^N1 − emitter mdot|`), breaks when
  consistent (`TOL`), else a DAMPED update (`DAMP` 0.4) + re-solve, cap 20.
  A **physical-validity guard** (M5 review): a self-consistent state with
  negative gauge pressure (an emitter cratering OTHER junctions) is
  downgraded to `degraded` — an "ok" frame never reports impossible
  negatives. Healthy nets are a NO-OP (all factors 1, no emitters).
- **Wire**: summary gains `mdot_deficit` (unmet demand) + `mdot_emitted`;
  balance = feed − delivered − stored − spill − exported − emitted.
  `StepResult.emitters` — hydrants/bursts are equipment SCADA (on the wire
  in strict mode), background LEAKS are hidden reality (stripped in strict
  mode — the MNF the operator must DETECT). `emitters.csv` in recordings.
- **API** (58 routes): GET /emitters, POST /hydrant|/burst|/leakage,
  DELETE /leakage|/emitter/{name}, GET|POST /pda. Scenario recipes carry a
  `hydraulics` block (PDA toggle, leak coefficient, hydrants with a fresh
  duration, bursts), applied per-entry-tolerantly on load.
- **W 405 fire-flow compliance** (the M4 catalog's deferred check): a
  drawing hydrant node < 1.5 bar or delivering < 90 % of target → violation.
- **UI**: EventSection (PDA toggle + Hitze-warning, hydrant/burst/leakage
  buttons, node picker, deficit + withdrawal read-outs, live emitter list
  with remove), MapDiagram 🚒/💥 emitter markers, i18n `event.*` DE/EN.

**Tests: 190 backend ×2 + 23 vitest**; tsc strict + vite build green;
API.md 58 routes. **Hydrant EPANET oracle** (`test_hydrant_oracle.py`):
3 cases match WNTR node pressure within 0.1 bar (emitter coefficient
`C_e = (C/ρ)·(ρg/1e5)^0.5`), so the fire-flow pass/fail vs the 1.5 bar
rule agrees with EPANET. PDA verified: no-op on healthy nets, graceful
starvation (14 kg/s overload → 2.72 kg/s deficit at a positive 1.5 bar),
negative-pressure contrast with PDA off; leakage raises the leak rate,
"repair" halves it.

**Adversarial review** (3 lenses + per-finding verification, 15 agents):
12 confirmed / 0 refuted — all fixed + regression-pinned. Headline: the
PDA/emitter fixed point checked self-consistency but not pressure
VALIDITY, so a burst could report negative pressures in an "ok" frame →
added the validity guard (now honestly "degraded"). Plus: emitter expiry
now uses an absolute (unwrapped) tick — a timed hydrant expired never /
wrongly across day boundaries; leaks no longer seeded on head-source
nodes; hydrant coefficient sized off a service-pressure floor + capped at
target; strict mode hides leak emitters; scenario hydraulics block
per-entry tolerant with a fresh hydrant duration; emitter tooltip XSS
escaped; EventSection busy-guard thunked; export/emitter byte-compat
caveat documented; EPANET oracle artifacts written to tmp not the repo.

**Deviation (documented):** very stiff undersupply (a huge single-node
draw, or a burst that craters the zone) that the damped fixed point cannot
settle in 20 iterations is honestly reported `degraded` (never a false
"ok" / never a 500); the warm-started next tick usually settles.

### 2026-07-21 — M6: wells & aquifer (branch `m0-fork-strip`)

**Built** (roadmap §6 M6, §4.5; TF §5):

- **Raw-water side is pure Python** (`assets/wellfield.py`) — the
  Reinwasserbehälter (break tank) hydraulically DECOUPLES it from the
  pandapipes network (TF §5), so wells + aquifer + accounting are a mass
  balance, no pandapipes. `Aquifer` (single linear reservoir:
  `dh/dt = (recharge·drought − ΣQ)/(S_y·A)`, seasonal recharge cosine
  peaking mid-winter, drought factor); `Well` (drawdown `Q/(Q/s)`,
  filter-screen protection caps the yield, ageing erodes `Q/s` faster at
  deep drawdown, `regenerate` → 90 %, Sichardt mutual interference);
  `WellField` (aggregates, produces the break-tank inflow, books the
  water right (WHG §§8–10) + energy `kWh/m³`).
- **Coupling**: a `TankSpec` kind `"break"` is the Reinwasserbehälter;
  `WaterTank.integrate` adds `external_inflow_kg_per_s` (well production)
  to the network draw. `Simulator._step_wellfields` (pre-solve): the well
  pumps fill the break tank on its own hysteresis (capped by the aquifer),
  the aquifer steps, and a network pump whose SUCTION is a break tank
  TRIPS when it runs empty (low-level protection — recovers on refill, no
  latch). **The Lauenau cascade**: drought → falling aquifer → well
  capacity caps below the peak → break tank empties → Netzpumpe trips →
  Hochbehälter drains → households run dry (M5 PDA).
- **Wire/API**: `StepResult.wellfields` (raw SCADA, survives strict mode);
  recorder `wellfields.csv`; GET /wellfields, POST /wellfield/drought,
  POST /wellfield/{name}/well/{well}/regenerate (61 routes). Compliance:
  `water_right` (day warn / year violation — COMPLIANCE, wells keep
  pumping), `well_ageing` (W 130 > 10 %). Scenario recipes carry the
  drought factor. UI WellFieldSection (aquifer, production/capacity,
  energy, water right, per-well ageing + regenerate, drought slider).
- **Bundle**: NEW **lauenau** (8 nodes, modelled on the real 2020 water
  emergency — Reinwasserbehälter → Netzpumpe → Hochbehälter → village; two
  14 m³/h wells; a deliberately small "shallow teaching aquifer" so the
  drought decline is visible over the sim's fast-forward days, TF §5).

**Tests: 203 backend ×2 + 23 vitest**; tsc + vite build green; API.md
61 routes. Acceptance: Lauenau normal day supplied at 0.385 kWh/m³ (in the
0.3–1.0 corridor); the drought cascade leaves households unsupplied; the
seasonal aquifer sawtooth, well ageing/regeneration, filter-screen
protection (the aquifer cannot fall below screen+margin — the well stops
first), break-tank mass balance, and the water-right compliance warning
all pinned.

**Review note:** the adversarial-review workflow was blocked by an
Anthropic session limit (all three find-agents errored before running), so
I did a rigorous MANUAL review pass via direct probes instead — which
found two real defects, both fixed + regression-pinned: (1) the well-pump
hysteresis memory (`pumps_running`) was a dynamically-added attribute never
reset, breaking deterministic replay / live-vs-export byte-compat; (2) the
`interference_fraction` parameter was stored but never applied (dead
Sichardt physics). The automated multi-agent review is queued to re-run
once the limit resets; any further findings will land as a follow-up.

**Deviation (documented):** an export replay resets the raw side (aquifer
level, ageing, counters) — a mid-session day exports from the full
aquifer, so its `wellfields.csv` differs from a warm live pack (the
from-midnight-replay doctrine, like the M4/M5 caveats; noted in
`exporter.py` + `docs/COMPLIANCE.md`). The storativity is a small teaching
value (real aquifers respond over months, TF §5).

- Next: **M7 — observability** (roadmap §6, §4.10): SCADA-realistic sensor
  presets, the measured/estimated views wired to water channels, the
  ForwardObserver water twin with archetype demand priors, honesty
  tripwire tests (a burst at an unmetered node must NOT appear in the
  estimate).
