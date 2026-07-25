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
  deep drawdown, `regenerate` → 95 %, Sichardt mutual interference);
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

**Tests: 209 backend ×2 + 23 vitest** (+6 review regression pins); tsc +
vite build green; API.md
61 routes. Acceptance: Lauenau normal day supplied at 0.385 kWh/m³ (in the
0.3–1.0 corridor); the drought cascade leaves households unsupplied; the
seasonal aquifer sawtooth, well ageing/regeneration, filter-screen
protection (the aquifer cannot fall below screen+margin — the well stops
first), break-tank mass balance, and the water-right compliance warning
all pinned.

**Review:** the first adversarial-review workflow attempt was blocked by an
Anthropic session limit (all three find-agents errored before running), so
I did a MANUAL review pass first — which found two real defects: the
well-pump hysteresis memory (`pumps_running`) was a dynamically-added
attribute never reset (broke deterministic replay / live-vs-export
byte-compat), and `interference_fraction` was stored but never applied
(dead Sichardt physics). The automated multi-agent review (3 lenses +
per-finding verification) then re-ran successfully and surfaced **10**
confirmed findings — ALL fixed + regression-pinned in this follow-up:
(1) `regenerate` restored to exactly 90 %, re-tripping the W 130 alarm it
was meant to clear → now 95 %; (2/7) the break-suction low-level interlock
was applied inside `_step_wellfields`, so an operator forcing the pump
`on` in the manual-station loop defeated the safety trip → the trip is now
re-applied in `_apply_step` AFTER the manual loop (hardware interlock wins);
(3) the WHG annual counter never rolled over, so a multi-year fast-forward
accrued a false yearly violation → `last_year_index` rollover added;
(4) a resting (non-producing) well still aged → ageing gated on `running`;
(5) `capacity_m3_h` reported the nameplate sum, overstating yield under
drought → now the interference-aware available-yield sum; (6) two well
fields feeding ONE break tank overwrote each other's inflow → accumulated
via an `inflow_by_tank` dict; (8) the UI drought slider snapped back
mid-drag / showed a stale factor while paused → local optimistic state;
(9) the capped-production cue under-triggered (followed from #5) → fixed by
the interference-aware capacity; (10) the regenerate button appeared at
5 % aged while the label + W 130 warning used 10 % → aligned to 10 %.

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

### 2026-07-22 — M7: observability (branch `m0-fork-strip`)

**Built** (roadmap §6 M7, §4.10; TF §7):

- **ForwardObserver** (`estimator.py`, full rewrite from the M0 stub) — a
  "digital-twin estimator". pandapipes has no state estimator, so the
  `estimated` layer is a second pandapipes net driven ONLY by operator
  knowledge: source/tank/pump/PRV SCADA (copied from the live dispatch —
  tank ext_grid heads are level SCADA), metered consumer flows, and
  **demand priors** for unmetered consumers. The twin re-derives its OWN
  pump operating points (the shared `solve_hydraulic`) and its deviation
  from the MEASUREMENTS at sensored points is the `error`/innovation
  (valid in strict mode — computed against measurements, not truth).
- **Priors are the EXPECTED demand, noise-free** — `build_demand_profiles`
  gained a `noise=False` flag that drops the per-tick stochastic multiplier
  (E[·]=1), and `build_prior_book` reads only `sim.inputs` + the operator's
  weather knob + placement recipes (`archetype` basis) or the flat
  contracted base (`design` basis). Never the mutable runtime
  `sim.profiles`, so a runtime anomaly cannot leak into the prior.
- **The honesty core**: every emitter withdrawal (leak/burst/hydrant) is
  ZEROED in the twin, and a check valve the TRUTH shut this tick is re-opened
  in the twin (a CV closure is a physical response, not an operator command)
  so the twin re-derives its own — a hidden burst/leak/anomaly at an
  unmetered node stays INVISIBLE; the same anomaly at a METERED consumer
  propagates (the meter delivers it).
- **Shared solver**: extracted `solve_hydraulic(net, iter_base,
  producer_meta, cv_closed)` (the station operating-point secant +
  check-valve logic) from `Simulator._solve_hydraulic` so the truth path and
  the twin use ONE operating-point solver, never two.
- **Wire/API/UI**: `StepResult.estimated` survives strict mode (derived from
  measurements). `EstimationConfig` (`enabled` now defaults ON,
  `prior_basis`, `throttle_factor`); GET/POST `/estimation/config` with
  `prior_basis` validation; the policy is engine-held (survives grid swaps)
  and is saved/restored in scenario recipes. New **`scada`** placement preset
  (critical pressure loggers at source + net ends, no household meters). The
  UI three-view splice (Realität/Gemessen/**Schätzung**) now renders the
  estimate + an estimate-quality panel (innovation + stale age); the est view
  strips truth findings so no alarm halo/list leaks the hidden anomaly.

**Tests: 229 backend ×2 + 23 vitest**; tsc + vite build green; API.md 61
routes. Acceptance (roadmap §4.10): estimator innovation ≈0 at sensored
points on an unperturbed full-coverage run (Musterdorf: feed est==truth,
max |Δṁ| < 1e-3); the honesty tripwires — a burst at an unmetered node moves
the WHOLE estimate by ~0 (exact 0 on the fixed-boundary hillside net), an
unmetered consumer anomaly stays at the prior, a metered anomaly propagates,
the `clear` preset makes the estimate equal the priors; throttle/raster stale
attachment; twin failure (exception AND non-convergence) is data.

**Review:** the multi-agent adversarial review (3 lenses — honesty /
numerics / integration — + per-finding verification) surfaced **5** findings
(3 confirmed + 2 plausible; 6 rejected as intentional design), ALL fixed:
(1, MAJOR) the est VIEW rendered truth-derived alarm halos + the full findings
list, betraying the very unmetered anomalies the estimate hides → the est
frame now strips `findings` and the Alarmzentrale shows an est honesty note;
(2) the non-convergent-twin path and multi-day estimation were untested →
tests added; (3) stale "M0/stub" comments → refreshed; (4) the burst tripwire
asserted invariance at one node/tick → strengthened to whole-estimate
invariance + an exact-0 fixed-boundary variant; (5) the estimation policy was
not saved in scenarios → added + round-tripped. Plus a belt-and-suspenders
fix beyond the findings: the twin re-opens truth CV closures so a
burst-induced closure cannot leak.

**Deviation (documented):** the observer is deliberately NON-deterministic (a
wall-clock self-throttle) and is force-disabled on export replays
(`exporter.prepare_replay`) so byte-stable replay is unaffected; it never
touches the recorder CSVs. Enabling it by default roughly doubles the engine
step cost (a twin deepcopy + solve per sim), so the full test suite runs
~18 min — accepted (the roadmap wires the estimated view on by default). The
measured/estimated views still do not derive their OWN rule alarms (compliance
runs on truth only); that stays a future enhancement.

- Next: **M8 — geodata bundle builder + real-town bundles + editor**
  (roadmap §6): `tools/bundle_builder` (osmnx + DGM sampling → bundle),
  real-town demo bundles, the NetzStudio water editor with live W 400-1
  load-case checking.

### 2026-07-22 — M8 stage 1: geodata bundle builder + real-town bundle

M8 is large (builder + real-town bundles + the NetzStudio editor), so it is
split. **Stage 1 (this entry): the offline geodata bundle builder + one
real-town bundle.** Stage 2 (the NetzStudio water editor, porting the
`gridedit` interactive-OSM approach for water; + a hilly Bavarian Druckzonen
town) follows.

**Built** (roadmap §6 M8, TF §11) — the full geo stack installs + runs on
Windows (osmnx 2.1, rasterio/GDAL 3.12, pyproj, geopandas, pyogrio):

- **`tools/bundle_builder/`** — turns a real German town into the five-file
  bundle. `osm.py` fetches the OSM street graph (osmnx → Overpass), projects
  to UTM (EPSG:25832/25833), flattens to plain geometry (WGS84 + metres) +
  the OSM (ODbL) credit. `elevation.py` samples per-node elevation:
  `OpenTopoDataProvider` (EU-DEM 25 m over public HTTPS — the default;
  hoehendaten.de's German DGM1 API sits on a firewalled port here) or
  `RasterDGMProvider` (a local DGM GeoTIFF via rasterio, the roadmap's DGM1/
  DGM200 path). `synthesize.py` turns the streets into a **branched gravity
  network**: largest component → minimum spanning tree = the mains (so every
  pipe is a cut edge → the loader's PRV/pump-cut-edge + reachability rules
  hold), an **ext_grid source** on the high point sized so the whole zone
  stays in the DVGW band, consumers on a node subset, PE d-series diameters.
- **Two-step pipeline** (`snapshot.py`/`pipeline.py`/CLI): an ONLINE
  `make_snapshot` freezes the projected streets + sampled elevations +
  attribution to a pinned JSON; an OFFLINE, deterministic `build_from_snapshot`
  rebuilds the bundle byte-for-byte with only networkx + the stdlib — the M8
  acceptance ("builds offline-reproducibly from a pinned data snapshot"). The
  heavy geo deps are ONLY the snapshot step (`tools/bundle_builder/
  requirements.txt`); the build + tests need none, so CI rebuilds from the
  committed snapshot without them.
- **NEW `alpen` bundle** — the real Niederrhein town of **Alpen** (Ortskern):
  182 junctions, 181 pipes, 126 consumers from real OSM streets + real EU-DEM
  elevations (21–53 m). Solves clean (p_min 2.9 / p_max 5.9 bar, v_max 0.28
  m/s, 0 violations). Registered (`character: "real"`). `scripts/build_alpen.py`
  rebuilds it from the snapshot. `NetworkStructure` gained an optional
  `attribution` field (TF §11); the map's Leaflet attribution control now
  renders the OSM + Copernicus/EU-DEM credit ("attribution renders on map").

**Tests: 237 backend + 23 vitest** (a clean confirmation run; the one earlier
failure was the documented ambient-load perf flake — green in isolation);
tsc + vite build green. 7 new builder
tests (OFFLINE only — no osmnx needed): the snapshot is self-consistent + byte-
stable, the build is deterministic + reproduces the committed files, the
synthesised structure is a valid gravity tree with one ext_grid source, and
the Alpen bundle loads/validates/solves in-band with **no alarm flood** (7
builder tests + the Alpen geo-bbox pin).

**Review:** the multi-agent adversarial review (3 lenses — pipeline /
synthesis / integration — + per-finding verification) surfaced **9** confirmed
findings, ALL fixed: (1, MAJOR) the flat Alpen village mesh is near-uniformly
stagnant and re-flooded the Alarmzentrale with ~82 per-tick hygiene warnings —
regressing the M4 alarm-flood fix on a shipped bundle → the per-pipe hygiene
stagnation warning now folds into ONE fleet finding above a threshold (like
self-cleaning; Alpen now peaks at 5 findings/tick), pinned by a new alarm-
volume assertion; (2) `StreetNode.degree` was ~2× inflated (osmnx MultiDiGraph
half-edges) so dead-ends were unrepresentable → degree from an undirected view
(+ the committed snapshot's degrees recomputed offline); (3/6) the
`RasterDGMProvider` no-data guard was a no-op for NaN / absent-nodata GeoTIFFs
→ a finite + declared-nodata check; (4) the geodata attribution was parsed but
never shown → surfaced on the `/network` wire + rendered on the map;
(5) the Alpen test asserted zero violations but not warning volume → added;
(7) a degenerate edge-less snapshot crashed with an opaque `max()` error →
guarded; (8/9) two stale synthesiser comments corrected.

**Deviation (documented):** elevation uses EU-DEM 25 m via OpenTopoData (the
`RasterDGMProvider` DGM1 path is provided for local Länder tiles) because
hoehendaten.de's DGM1 API is on a non-standard, firewalled port here — coarser
(±2 m) but plenty to show terrain; the credit is honest (Copernicus/EU-DEM).
The osmnx Overpass cache is gitignored. Alpen is a single gravity zone; the
hilly Druckzonen town (which needs PRV zone-splitting — Neubeuern's 114 m
relief demonstrates exactly why) comes with stage 2.

- Next: **M8 stage 2** — the NetzStudio water **editor** (port `gridedit`'s
  interactive-OSM editing for water: click-to-place junctions with
  hoehendaten.de elevation, street-snapped pipe drawing, equipment, auto-
  hydrants, live W 400-1 load-case checking, commission-to-bundle) + a hilly
  Bavarian real-town bundle with real Druckzonen.

### 2026-07-22 — M8 stage 2a: NetzStudio editor backend + W 400-1 load cases

Stage 2 (the editor) is itself split. **Stage 2a (this entry): the editor
BACKEND** — the DVGW W 400-1 three-load-case check (the "commission a
*passing* network" gate) + the thin OSM/DEM proxy endpoints the interactive
frontend will call. Stage 2b (the frontend map editor, ported from
`gridedit`) + stage 2c (a hilly Druckzonen bundle) follow.

**Built** (roadmap §5, TF §2):

- **`loadcases.py`** — `run_load_cases` runs the three W 400-1 sizing load
  cases and returns pass/fail + the binding quantity per case, evaluated from
  a warm, steady solved frame's RAW values (deterministic, history-free):
  **LF1 Maximale Förderung** (peak throughput, stations forced on → velocity
  ≤ 2,0 m/s), **LF2 Spitzenstunde Maximaltag** (every tap ≥ its storey minimum
  2,0 + 0,35 bar/Geschoss, ≤ 8 bar rest), **LF3 Löschfall** (a W 405 fire draw
  — 48/96/192 m³/h by land use — at the hydraulically worst node, ≥ 1,5 bar,
  v ≤ 2,5). Each verdict requires network-wide physical validity (no node
  below ~0 bar). Honest, differentiated results: `tutorial_hillside` + `alpen`
  pass all three; `musterdorf` fails LF3 (its industry zone's 192 m³/h fire is
  unservable at the worst point) and `lauenau` fails LF2 (two multi-storey
  taps below their storey requirement) — the real "fire flow / storey pressure
  sizes the network" insights.
- **`api/editor.py`** — the editor proxies (raw Overpass / Nominatim /
  OpenTopoData via httpx, no heavy geo stack in the runtime, disk-cached):
  `GET /editor/streets` (streets + buildings for a village bbox), `GET
  /editor/geocode`, `POST /editor/elevation` (frozen DEM per clicked point),
  `POST /editor/loadcheck` (the load-case gate on the bundle being drawn;
  422 on an invalid bundle). Commission reuses the existing `POST
  /networks/import`.
- **`load_network_from_docs`** — an in-memory five-file validator refactored
  out of `load_network` (which now delegates to it) so the load-case check
  validates the edited bundle without disk I/O.

**Tests: 249 backend + 23 vitest**; API.md 65 routes. 12 editor tests (the
three cases on healthy + stressed nets, the fire/storey fail insights, the
proxy guards, the 422 path) — the live OSM/DEM proxies are exercised only on
their offline guards (never the network in CI). httpx added to requirements.

**Review:** the multi-agent adversarial review (2 lenses + verification)
surfaced **8** findings (6 confirmed + 2 plausible), all fixed: (1, MAJOR) LF2
looked up the storey `p_req` by NODE while the table is keyed by NAME → it
silently applied a flat 2,0 bar to every multi-storey tap → keyed by name (and
this immediately caught lauenau's real LF2 failure); (2, MAJOR) LF3 read its
verdict off a `converged` frame that could be `degraded`/unphysical and
inspected only the hydrant node → every case now requires network-wide
physical validity (no node < ~0 bar); (3, MAJOR) `/editor/loadcheck` 500'd on
a per-document schema error → now 422 with a problems list (pydantic
`ValidationError` caught); (4, MAJOR) the global peak tick was clamped into
day 0 on a multi-day horizon → decomposed via `divmod`; (5) the check ran at
the app's 1440 resolution (5–14 s) → fixed 96 + fewer warm steps; (6) LF1
reduces to LF2 on demand-driven/gravity nets → documented in the case detail;
(7) `/editor/elevation` silently truncated > 100 points → explicit 422;
(8) the bbox guard gained absolute lat/lon bounds. All pinned by regression
tests.

- Next: **M8 stage 2b** — the interactive NetzStudio frontend (the map editor
  itself: place/draw on real streets, live load-check panel, commission) and
  **stage 2c** — the hilly Bavarian Druckzonen bundle (PRV zone-splitting in
  the synthesiser).

### 2026-07-22 — M8 stage 2b: NetzStudio interactive map editor

The editor **frontend** — draw a water network on real OSM streets, verify it
against the W 400-1 load cases (stage 2a), commission it. Ported from the
sibling `gridedit` tool's interaction model, reimplemented for water in the
MIT repo. Closes teaching goal 1 ("how networks are built").

**Built** (roadmap §5):

- **`ui/src/editor/model.ts`** — the editor model (source / junction /
  consumer nodes + street-routed pipes), `makeNode` (the pure, tested
  placement logic + collision-proof naming), `validateModel` (structural +
  connectivity + duplicate-name checks), and `toBundle` (serialise to the
  five-file bundle: one ext_grid source, head-derived `pn_bar`, consumers from
  population, standard environment, OSM + EU-DEM attribution) — mirroring the
  offline builder so a hand-drawn net loads/validates/solves identically.
- **`ui/src/editor/streetGraph.ts`** — client street routing: snap-to-street +
  Dijkstra in a local metric projection, so drawn pipes follow the streets.
- **`ui/src/editor/EditorMap.tsx`** — the Leaflet surface: renders streets +
  the model, tool-based clicks (place source/junction/consumer with the DEM
  elevation frozen on; draw a street-snapped pipe by picking two nodes;
  delete). **`NetzStudioEditor.tsx`** — the container + side panel (toolbox,
  pipe catalog, the live W 400-1 load-case results, commission). A
  **Katalog/Editor** toggle in NetzStudio hosts it; the drawn net is held in
  NetzStudio so it survives the toggle.
- **Backend**: `/editor/streets` now tries several Overpass **mirrors** (the
  main instance 504s on village bboxes) with a bounded timeout, preferring a
  non-empty result. `load_network_from_docs` (stage 2a) validates the commit.

**Tests: 250 backend + 36 vitest**; tsc + vite build green. 13 editor vitest
(model/validate/toBundle, the placement wiring, street routing) + the backend
`draw→loadcheck→commission` integration test. Verified **live**: the editor
renders, streets load (mirror fallback confirmed against a transient 504), and
placing a source (◆) + a consumer (▲) creates the right node kinds with the
DEM elevation fetched.

**Review:** the multi-agent adversarial review (2 lenses + verification)
surfaced **6** findings, all fixed — including a **critical** one: the map
click handler was registered once and called a `placeNode` that closed over
the initial `tool='pan'`, so EVERY map-placed node became a generic junction —
sources/consumers were unplaceable and commission was permanently blocked. Fix:
the handler now passes the LIVE tool (`cb.current.tool`) into a stable
`placeNode`; the placement logic moved into the pure, unit-tested `makeNode`
(the tests now cover exactly this wiring — the bug slipped past the earlier
tests, which only exercised `toBundle` on hand-built models). Also: (major)
delete-then-add reused a consumer name → duplicate names the loader rejects
with a raw 422 → collision-proof naming + an in-panel duplicate-name check;
(major) test-adequacy gaps closed; (minor) the DEM fetch moved out of the
`setModel` updater (StrictMode double-fire); (minor) the drawn net survives
the Katalog↔Editor toggle (state lifted); (minor) the Overpass loop hardened.

- Next: **M8 stage 2c** — the hilly Bavarian Druckzonen bundle (PRV
  zone-splitting in the synthesiser). That closes M8.

### 2026-07-23 — M8 stage 2c: hilly Druckzonen bundle (PRV zone-splitting)

Stage 2c closes M8: the offline geodata synthesiser gains **PRV zone-splitting**,
and a second real-town bundle — the hilly Upper-Bavarian **Neubeuern** — ships as
the Druckzonen showcase (Alpen was the flat single-zone one).

**Built** (roadmap §6 M8, TF §2/§11):

- **`synthesize.py` zone-splitting** (`SynthConfig.enable_prv_zoning`, OFF by
  default so the flat path — Alpen — stays byte-identical): on a hilly town one
  gravity zone would drive the deep streets far above PN 10, so the synthesiser
  splits the tree into **Druckzonen** with Druckminderer (`press_control` cut
  edges). Three pieces:
  - **`_descent_tree`** — when zoning, the mains are grown as a *gravity descent
    tree* from the source (attach the HIGHEST-elevation frontier node next, via
    its shortest street edge), so nodes are added top-down and a zone is a
    coherent elevation band, NOT the wandering cut a length-MST makes across a
    flat valley mesh (the length-MST + zoning first gave 25 spurious valves; the
    descent tree gives 6 clean ones). The flat path keeps the length-MST.
  - **`_assign_zones`** — walk the tree from the source; where a child's static
    pressure `(head−elev)·BAR_PER_M` would exceed `pn_max_bar` (7 bar, under the
    8 bar Ruhedruck warning / PN 10), insert a Druckminderer and reset the
    child's zone head to serve the TALLEST node of that subtree (+ the head
    reserve) — the no-starvation guarantee: even though the mains tree follows
    street length, a branch that dips below a valve then climbs back up still
    gets pressure. The outlet pressure is CAPPED at `pn_max_bar` so no node is
    ever *designed* above the band (review fix, below), floored at
    `zone_reset_bar`; a reducer never raises head.
  - **`max_elevation_m`** drops street nodes above the distribution area — a
    municipality boundary sweeps in forested-summit / Schloss access roads
    (Neubeuern's Gemeinde reaches a 563 m castle) that are not real mains.
- **NEW `neubeuern` bundle** — the real Markt Neubeuern (Inn valley, Lkr.
  Rosenheim): 215 junctions, 208 pipes, **6 Druckminderer**, 143 consumers, from
  real OSM streets + real EU-DEM elevations capped at 530 m (449–527 m, ~78 m of
  relief). Source (Hochbehälter) on the 527 m high point; the upper town
  (472–527 m) is the source zone, the valley (458–465 m) is fed through 6 PRV
  stations reducing to 3.0–4.55 bar — a textbook two-tier Druckzonen layout.
  Solves clean (p_min 2.5 / p_max 6.8 bar, 0 violations, ≤2 findings/tick).
  Registered (`character: "real"`); `scripts/build_neubeuern.py` rebuilds it
  offline from the pinned snapshot. UTM33 (12.14 °E is just into zone 33). The
  snapshot was frozen online once from the whole Gemeinde boundary (which
  carries the 114 m castle relief); the committed build is offline +
  deterministic from it, like Alpen.

**Tests: 258 backend + 36 vitest** (+7 stage-2c builder tests); tsc + vite build
green; Alpen rebuilds byte-identically (the flag-off path is untouched). The new
tests pin: the zoned build is deterministic + reproduces the committed files;
it is a zoned gravity tree (pipes + PRVs = n−1, so every valve is a cut edge;
each PRV feeds downhill and reduces to a 2–8 bar band); it solves in-band with
no alarm flood; it meets the peak load cases (LF1/LF2) but not the W 405 fire
case at its worst, highest, farthest point (LF3 — like musterdorf/lauenau, the
teaching insight); zoning is a no-op on a flat town; a synthetic 120 m-relief
tree splits into multiple zones and never starves a node it sits above; and the
trapped-high-node case (below) holds the band ceiling without over-pressurising.

**Review:** the multi-agent adversarial review (2 lenses — numerics/determinism,
integration/domain — + per-finding verification) surfaced **2** real findings,
both fixed (the automated confirmed/refuted tally was muddied because the fix
landed WHILE the review ran — the refutations read the already-capped code and
independently validated it). (1, MAJOR) the subtree-serving zone reset could
DESIGN a Druckminderer outlet ABOVE PN 10 — a "trapped" high node (one the
descent tree can only reach through a lower node, e.g. a dell that then climbs
to a knoll) made `max_sub[c]` far above `c`, so the un-capped `p_out` for the
dell ran to ~10.3 bar (repro: source 545 → dell 440 → knoll 515), and the
"split again deeper" claim was false because a reducer can never raise head →
the outlet pressure is now CAPPED at `pn_max_bar`, which (with the pn_max split)
bounds EVERY zone at ≤ 7 bar for all towns while still serving any trapped node
within one PN-band of relief above its feed; a knoll further above than
single-source gravity can physically reach is now honestly UNDER-pressured (a
truthful compliance finding — it needs a booster) instead of fake-over-PN.
(2, MAJOR) the synthetic zoning tests used only strictly-monotone-descending
streets, so they never exercised the over-serve path and gave false confidence
in the PN bound → a new trapped-node test (a dell feeding a 47 m-taller knoll)
now pins that the cap engages (two PRVs capped to exactly 7.0) and no node is
designed over the band. Neubeuern is byte-identical after the cap fix (its p_out
values were already ≤ 4.55 bar).

- Next: **M9 — validation hardening + docs** (roadmap §6), the final milestone.

### 2026-07-23 — M9 stage 1: EPANET/WNTR cross-validation suite

M9 (the final milestone) is staged: **stage 1 (this entry) — the EPANET/WNTR
cross-validation suite** (roadmap §7.2, the publishable claim the pandapipes
paper leaves open); stage 2 — the city-scale performance pass; stage 3 — docs +
scenario walkthroughs.

**Built** — `tests/validation/test_epanet_crossvalidation.py` (6 tests, new
`tests/validation/` package): rebuilds the two canonical EPANET example networks
WNTR ships — **Net1** (9 junctions) and **Net3** (92) — as gravity nets (every
reservoir + tank fixed at its EPANET head, pumps omitted — pandapipes models a
pump as a constant-lift std_type OUTSIDE the Newton solve, `StationLiftStdType`,
so it is not an apples-to-apples EPANET element; a direct-pump rebuild gave ~6 bar
error at the discharge; the pump+tank CONTROL loop is cross-validated by the M2
`test_tank_oracle`), solves them with pandapipes `friction_model="swamee-jain"`
(Darcy-Weisbach), and compares node pressures + link flows against WNTR's
EpanetSimulator on the IDENTICAL network. Results: **pressures within 0.0002 bar,
flows within 0.5 %** — both far inside the roadmap bar (0.05 bar / ~1.5 %). Plus:
a §8-pitfall pin (the `swamee_jain` underscore typo silently falls back to
nikuradse) and a PDA-curve-vs-WNTR-PDD check (Wagner within 0.001).

- **wntr** was undeclared (the existing oracles would silently skip in CI); added
  to the pyproject `dev` extra (test-only, out of the runtime Docker image).

**Tests: 264 backend + 36 vitest** (+6 validation); the suite writes no repo
artifacts (EPANET temp files routed to a TemporaryDirectory, not the CWD).

**Review:** the multi-agent adversarial review (2 lenses — scientific validity /
correctness — + per-finding verification) surfaced **3** confirmed findings, all
fixed. (1, MAJOR) the pitfall test proved the silent fallback HAPPENS but not that
it is HARMFUL — the fallback (nikuradse) matches EPANET *pressures* within 0.05 bar
too (the pressure field is hydrostatics-dominated, friction-insensitive), so the
material harm shows only in FLOWS → the test now asserts the fallback's Net3 flow
error (~14 %) blows the 1.5 % bar the correct model (~0.5 %) passes. (2, MINOR) the
pressure tests are friction-model-insensitive by nature → docstrings now state the
pressure field validates the D-W solve + hydrostatics generically while the FLOW
test carries friction-model fidelity. (3, MINOR) the pressure comparison used the
nominal ρ·g/1e5 (0.09792 bar/m) to convert EPANET head, ~0.1 % off pandapipes'
own gradient (0.09780), a water-property convention offset → now converts with
pandapipes' EMPIRICAL static gradient, so the comparison is the hydraulic head
(residual 0.0002 bar, not 0.01). The wntr-in-requirements placement (review-refuted
as out-of-scope) was fixed anyway — the Dockerfile installs only requirements.txt.

- Next: **M9 stage 2** — the city-scale performance pass (a ≥ 500-junction bundle
  warm-solving < 100 ms/tick).

### 2026-07-23 — M9 stage 2: city-scale performance bundle

**Built** — the roadmap's performance bar (≥ 500 junctions warm-solving < 100 ms/
tick) as a shipped bundle + a regression test. NO synthesiser code changed —
only existing `SynthConfig` knobs were set.

- **NEW `kevelaer` bundle** — the compact, flat centre of the Niederrhein
  pilgrimage town of **Kevelaer** from real OSM + EU-DEM: **544 junctions**, 543
  pipes, 367 consumers, single gravity zone. A flat town has ~no gravity relief,
  so a village's PE DN110 branches cannot carry a town's throughput in-band over
  the longer 544-node tree — the bundle uses city-grade **GGG DN200/300** mains +
  a 45 m head reserve, landing p_min 4.30 / p_max 5.24 bar (the 4–6 band),
  0 violations, ≤ 2 findings/tick. `scripts/build_kevelaer.py` rebuilds it
  offline-deterministically from the pinned `--bbox` snapshot; registered
  (`character: "real"`).
- **NEW `tests/test_performance.py`** — warm run_step median-of-20 (after 5 warm-
  up steps) **< 100 ms with the M7 observer OFF** (the observer deep-copies +
  re-solves a twin, an optional estimation overlay, not part of the hydraulic-
  solve budget). Measured on this host: **est-off median ~37 ms, max ~59 ms** over
  a day — comfortably inside the bar. The DEFAULT engine (observer ON) medians
  ~73 ms but is heavy-tailed (deep-copy GC), occasionally exceeding 100 ms — the
  observer self-throttles and is a separate concern from the hydraulic-engine bar.
  Per-tick cost is dominated by the pipeflow-call count (retry-ladder tiers), not
  junction count, so a clean tier-1 net stays fast at scale.

**Tests: 268 backend + 36 vitest** (+4: perf, kevelaer byte-stability + city-
scale structure, kevelaer geo-placement); Alpen + Neubeuern still byte-identical
(the config knobs are per-bundle).

**Review:** the multi-agent adversarial review (2 lenses — perf-test honesty /
bundle validity — + verification) surfaced **1** confirmed finding (+1 refuted),
fixed. (MAJOR) the perf test's docstring claimed it "fails on a real regression
(a second solver tier per tick)" — but the verifier reproduced that exact
regression (forcing a second tier every tick) and the median rose only 35 → 51 ms
(≈60 % of the est-off step is fixed overhead — collect_physics, compliance, wire
build — so doubling only the ~14 ms solve is a ~1.5× step change), still passing
< 100 ms → the docstring is now honest: this is an ACCEPTANCE test of the roadmap
bar (a regression large enough to BREACH 100 ms fails; smaller ones are within
spec), not a tight micro-benchmark. The refuted finding claimed the default
(observer-on) tick breaches 100 ms and so the est-off framing is dishonest —
refuted because the observer is a legitimately separate optional overlay and its
own tail is not the hydraulic-engine bar; the docstring now states the est-on
median (~73 ms) and heavy tail honestly regardless.

- Next: **M9 stage 3** — docs + scenario walkthroughs (bringing the Benutzerhand-
  buch/README up to M8/M9 + German scenario walkthroughs with expected
  observations), which closes M9 and the build.

### 2026-07-23 — M9 stage 3: docs + scenario walkthroughs (closes M9)

The final stage: bring the user-facing docs from their stale M0 state to M9 and
add the roadmap's scenario walkthroughs. **This closes M9 and the M0–M9 build.**

- **`docs/Benutzerhandbuch.md`** (served in-app at `/manual`) — full German
  rewrite, 7 → 15 sections. The five files now describe the whole feature set;
  a network-library table (the 7 bundles); the Schätzung view is the M7 digital-
  twin observer (with its honesty limits spelled out); new sections for the
  Alarmzentrale (M4), events + PDA (M5), tanks/pumps/Brunnen (M2/M6), the demand
  engine (M3) and the NetzStudio editor (M8); a **Szenarien-Rundgänge** section
  — a per-bundle walkthrough (what to do / what you see / why) for all seven
  bundles, the roadmap's M9 didactic deliverable; a validation section; and the
  Grenzen section corrected — the M0 limits (fixed demand, no tank dynamics) are
  LIFTED (PDA, tanks/pumps, wells, the observer are all active), leaving the
  genuine ones (quasi-static, no PRV state machine, no water quality, non-
  deterministic observer). The strings the `/manual` test pins are preserved.
- **`README.md`** — catalog 4 → 7 bundles (3 real-geodata), the estimation view
  is the digital-twin observer (was "wired but disabled"), the geodata builder +
  editor listed, EPANET Net1/Net3 + WNTR-PDD rows added to the Validation table,
  test counts 209 → 268 / 23 → 36, status badge M6 → M9 complete.

Every walkthrough number was verified against the shipped bundles / dev-log
before writing (tutorial 5.78 bar + highest-node Schlechtpunkt; musterdorf PRV
2.8 bar; mustertal control band 1.2/3.4; neubeuern 6 Druckminderer; kevelaer 544
nodes / 4.3–5.2 bar / ~37 ms; EPANET < 0.001 bar).

**Tests: 268 backend + 36 vitest** (docs-only stage — the `/manual` test passes;
no other test reads these files).

**Review:** the multi-agent adversarial review (2 lenses — factual accuracy /
consistency + honesty — + verification) surfaced **3** confirmed findings, all
fixed. (MAJOR) the README's front-page STATUS blockquote (separate from the
badge I'd updated) still read "Status: M6 complete … M7 is next … the Schätzung
view is stubbed, produces no estimate" → rewritten to M0–M9 complete with the
observer live. (MAJOR) the Alpen walkthrough cited "v_max 0,28 m/s, 2,9–5,9 bar"
— which is the midnight tick-0 (min-demand) snapshot, NOT the day: the true
daily peak velocity is ~0,87 m/s (3× higher, still well under 2,0 m/s) and
p_min dips to 2,78 bar; the two sibling geodata walkthroughs correctly use their
daily bands → Alpen now cites the daily band + morning-peak velocity to match
(the tick-0 mislabel traces back to the M8-stage-1 dev-log's Alpen line). (MINOR)
the Benutzerhandbuch attributed the nightly pool backwash to DVGW W 410 → it is
DIN 19643 (W 410 is the demand envelope) → corrected. The other walkthrough
numbers verified correct (tutorial 5.78 bar + highest-node Schlechtpunkt,
musterdorf 2.8 bar PRV, neubeuern 6 Druckminderer, kevelaer 544/4.3–5.2 bar daily,
EPANET < 0.001 bar).

**M9 — and the M0–M9 build — complete.** Optional roadmap stretch items not
built (documented as deferred): a live water-age post-processing layer, a STANET
importer, and pySIMDEUM showcase profiles (§6 M9 "optional").

### 2026-07-23 — Extensive documentation (post-M9, matching rtpowerflow)

On request ("write extensive documentation like in the rtpowerflow repo"), three
substantial docs were added to match the blueprint repo's documentation depth
(rtpowerflow ships ARCHITECTURE.md, BENCHMARKS.md, a geodata-extraction doc,
etc.). Wired into the README via a new "Extended documentation" links paragraph.

- **`docs/ARCHITECTURE.md`** — REPLACES the stale rtheatflow fork-parent doc with
  a current water-specific architecture (through M9): the three-tier system +
  data-flow diagram, the three-layer observability model, the backend data
  pipeline, the step (4-tier retry ladder / PDA+emitter fixed point /
  StationLiftStdType secant / validity guard), a full module-map table, the wire
  + strict mode, compliance + the raw-water side, the frontend, persistence,
  testing/CI, running.
- **`docs/BENCHMARKS.md`** — NEW (the fork-parent BENCHMARKS.md was deleted at M0):
  the EPANET/WNTR cross-validation methodology + the delivered M9 results — model
  mapping (gravity rebuild, D-W roughness, pump omission, pandapipes-own-gradient
  pressure conversion), Net1/Net3 (< 0.001 bar / ~0.5 %), the swamee-jain pitfall
  shown harmful on flows, the tank/hydrant/PDA-vs-PDD oracles, physics unit
  checks, the DVGW corridors, one-command reproduction, honest limitations.
- **`docs/GEODATA_BUILDER.md`** — NEW (the water analogue of rtpowerflow's
  GRIDGEN_EXTRACTION): the `tools/bundle_builder` online-snapshot/offline-build
  pipeline, OSM UTM projection + EU-DEM/DGM elevation, the pinned byte-stable
  snapshot, the synthesis (MST vs gravity descent tree, PRV zone-splitting), the
  three shipped bundles + configs, and the CLI.

**Review:** a 2-lens accuracy review (architecture vs code / benchmarks vs the
validation suite, each verified by running the code) surfaced **6** confirmed
findings, all fixed: broken `../` cross-refs to the parent-dir roadmap/foundations
(need `../../` from `docs/`); router count 14 → **13** (`runtime.py` is the app
singleton, not a router); `_TRUTH_KEYS` conflated with the separate background-
leak stripping; a reproduction command naming a nonexistent `test_m3_demand.py`
(→ `test_demand_engine.py`); the storey formula `2.0 + 0.35·storeys` →
`2.0 + 0.35·(storeys − 1)` (per storey ABOVE ground floor); and the wrong-model
pressure error cited as ~0.009 bar (the old constant-gradient value) → ~0.004 bar
under the pandapipes-own-gradient conversion the doc now uses.
