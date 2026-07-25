# rtwaterflow — Validation & Benchmarks

> **Status: all validation green.** The hydraulic engine is cross-validated
> against the independent reference tool **EPANET** (via **WNTR**): the standard
> example networks **Net1** (9 junctions) and **Net3** (92) rebuilt in pandapipes
> with `swamee-jain` agree with WNTR's EpanetSimulator to **< 0.001 bar** on node
> pressures and **~0.5 %** on link flows. Tank dynamics, hydrant fire flow and
> pressure-driven demand are cross-checked against their own EPANET/WNTR oracles;
> the demand engine against the DVGW **W 410** corridor. Everything below is
> reproduced by the test suite (`tests/validation/` + the oracle tests) on the
> pinned stack (pandapipes 0.14.0, WNTR 1.5).

---

## 1. Goal and trust argument

rtwaterflow is a **teaching** platform, so a student must be able to trust that
the numbers on the map are real hydraulics, not plausible-looking noise. The
pandapipes paper leaves the EPANET comparison open; this suite closes it for the
platform's engine. The reference is **EPANET** — the de-facto standard
water-distribution solver — driven through **WNTR** (the EPA's pip-installable
Water Network Tool for Resilience, which wraps the official EPANET engine). WNTR
is an *independent* implementation: it does not share code with pandapipes, so
agreement between the two is genuine cross-validation, not a tautology.

## 2. What is compared (scope and honesty)

**Honest framing (also in the README).** rtwaterflow ships **hand-authored /
deterministically-generated teaching networks**, not converted real-utility
datasets — so, unlike some field-calibrated models, it makes **no claim of
validation against field measurements**. What is validated is the **engine**: the
hydraulic solve, the demand model, and specific runtime mechanisms.

The comparison boundary is **steady, incompressible, single-phase pressurised
flow** — the same quasi-static snapshot the live engine computes each tick.
Quantities validated: **node pressure**, **link flow**, **tank level
trajectory**, **fire-flow node pressure**, and the **pressure-driven demand**
delivered fraction. Out of scope (and never claimed): transients / water hammer
(DVGW W 303), water quality / age, and unbalanced or compressible effects.

## 3. The reference toolchain

| Tool | Version | Role |
|---|---|---|
| pandapipes | 0.14.0 (pins pandapower 3.3.3) | the engine under test |
| WNTR | ≥ 1.5 | the EPANET wrapper / oracle (installs the official EPANET engine + Net1/Net3 `.inp`) |
| Python | 3.14 (this host) | the dev venv |

`wntr` is a **test-only** dependency: it lives in the `pyproject` `dev` extra
(installed by CI via `pip install -e .[dev]`), **not** in `requirements.txt`, so
it never enters the runtime Docker image. Every oracle test is guarded by
`pytest.importorskip("wntr")` — absent WNTR skips the validation, it does not
fail the suite.

## 4. The EPANET cross-validation suite (`tests/validation/`)

### 4.1 Model mapping — where a benchmark lives or dies

pandapipes and EPANET must solve the **identical** hydraulic problem for the
comparison to mean anything. The mapping:

- **Friction model.** EPANET's Darcy-Weisbach uses the explicit Swamee-Jain
  approximation, so pandapipes is run with `friction_model="swamee-jain"` (the
  **hyphen** — see § 4.3) and WNTR with `headloss = "D-W"`. Hazen-Williams (the
  EPANET default for Net1/Net3) is **not** used — pandapipes is D-W only.
- **Roughness.** One uniform absolute roughness on both sides. WNTR stores D-W
  roughness in **metres** (written as mm to the metric `.inp`), so `roughness =
  k_mm / 1000`; pandapipes takes `k_mm` directly. The absolute value is immaterial
  to a cross-validation — only that both solve the *same* D-W network.
- **Boundaries.** Both nets are rebuilt as **gravity networks**: every reservoir
  and tank is fixed at its EPANET head, and the **pumps are omitted**. This is
  deliberate: pandapipes models a pump as a *constant-lift* std_type outside the
  Newton solve (`network_builder.StationLiftStdType`, see ARCHITECTURE § 3.2), so
  a pump is not an apples-to-apples EPANET element — a direct-pump rebuild gave a
  ~6 bar error at the pump discharge. The pump + tank **control** loop is
  cross-validated separately (§ 5.1).
- **Units, like-for-like pressure.** EPANET reports pressure as metres of head
  above the node elevation; pandapipes reports gauge pressure in bar. The two
  engines use slightly different water-property conventions (~0.0978 vs
  0.0979 bar/m), so the comparison converts EPANET's head with pandapipes' **own**
  empirical static gradient (read off a no-flow column). This makes the comparison
  the *hydraulic head*, not a ~0.1 % density offset — the residual drops from
  ~0.01 bar to ~0.0002 bar.

WNTR writes its EPANET temp files to a `TemporaryDirectory`, so the suite leaves
no artifacts in the repo.

### 4.2 Results

Both standard networks, pandapipes `swamee-jain` vs WNTR EpanetSimulator on the
identical D-W network:

| Network | Junctions | Pipes | Worst pressure error | Worst flow error (pipes > 5 m³/h) |
|---|---|---|---|---|
| **Net1** | 9 | 12 | **~0.0002 bar** | 0.03 % |
| **Net3** | 92 | 117 | **~0.0002 bar** | 0.52 % |

Both are far inside the roadmap's bar (**0.05 bar** on pressure, **~1.5 %** on
flow). The pressure test is a whole-solve check dominated by hydrostatics and is
therefore *friction-model-insensitive* by nature (even the wrong friction model
lands only ~0.004–0.006 bar off); the **flow** test is the friction-model
discriminator (§ 4.3).

### 4.3 The `swamee-jain` pitfall, demonstrated harmful

A recurring engineering trap: `friction_model="swamee_jain"` (underscore) is not
a recognised model, and pandapipes **silently falls back to nikuradse** (upstream
#803, where laminar + turbulent λ are *added* instead of regime-selected — a
low-Re bias). The suite pins this and, crucially, shows it is *materially
harmful*, not cosmetic: on Net3 the underscore fallback matches EPANET pressures
fine (~0.004 bar, still comfortably under the 0.05 bar bar — pressures can't
catch it) but its **flows are ~14 % off** — nearly 10× over the 1.5 % flow bar
the correct
hyphenated model (~0.5 %) passes. The lesson: a wrong friction model is caught on
flows, not pressures.

## 5. The mechanism oracles

Beyond the full-network solve, three specific runtime mechanisms are
cross-validated against WNTR/EPANET on purpose-built cases.

### 5.1 Tank + pump dynamics (`test_tank_oracle.py`)

The M2 tank-level trajectory + pump-switching over a 24 h extended-period run,
against WNTR's EpanetSimulator on the **same** fitted pump curve (sampled into
both engines so neither's pump fit masks the comparison). Because both engines
switch the pump on a forward-Euler tank integration but EPANET cuts sub-steps to
switch exactly at threshold crossings, the *phase* drifts over the day — so the
test compares **level rates** in state-matched ticks, not absolute levels:
**median < 1 cm/tick, p75 < 3 cm/tick** (observed ~1 mm/tick against a ~26 cm/
tick level motion); switching counts agree ±1; envelopes match.

### 5.2 Hydrant fire flow (`test_hydrant_oracle.py`)

The M5 emitter fixed point (`mdot = C·pᴺ¹`, N1 = 0.5) for a drawing hydrant,
against WNTR's EPANET emitter (D-W, `emitter_exponent = 0.5`, the emitter-
coefficient conversion `C_e = (C/ρ)·(ρg/1e5)^0.5`): hydrant node pressure within
**±0.1 bar** across comfortable/marginal/crater cases, so the W 405 ≥ 1.5 bar
pass/fail verdict agrees with EPANET.

### 5.3 Pressure-driven demand (`test_pda_curve_matches_wntr_pressure_driven_demand`)

The M5 Wagner PDA curve against WNTR's own pressure-driven-demand (PDD) model,
using WNTR's solver as the oracle: a tiny demand on a near-frictionless stub is
held at a series of node pressures across the band, and its EPANET-PDD delivered
**fraction** is compared to `PDAController.factor` at that pressure — agreement
within **0.001** across the whole Wagner band (p_min 0.5 → p_req 2.7 bar).

## 6. Physics unit checks

| Check | Reference | Result |
|---|---|---|
| Elevation head (`tutorial_hillside`) | the pandapipes height-difference tutorial | ext_grid 0.5 bar @ 400 m → **5.78 bar** at the 346 m consumer; junction pressures pinned to the baselined Colebrook solution within **±0.01 bar** (agrees with the upstream Nikuradse tutorial to ±0.02 bar); the worst point is the **highest** consumer |
| Hydrostatic gradient | 0.0981 bar/m hand-check | matches the tutorial's per-metre head to sub-centibar |

## 7. DVGW plausibility corridors

Not point-oracle comparisons but engineering *corridors* the platform must land
in (values from `../../TECHNICAL_FOUNDATIONS.md`, in the parent directory):

| Corridor | Rule | Result |
|---|---|---|
| Daily/hourly demand peak | DVGW **W 410** `f_d = 3.9·E^−0.0752`, `f_h = 18.1·E^−0.1682` | a synthetic residential year over the Musterdorf population (E = 1577) lands within **±20 %** |
| Mass balance | conservation | Musterdorf `|balance_err|/feed < 0.1 %`; demand = delivered to 1e-6 kg/s on a healthy net |
| Pump energy | TF § 5 corridor 0.3–1.0 kWh/m³ | Lauenau normal day ≈ **0.385 kWh/m³** |
| Storey service pressure | W 400-1 `2.0 + 0.35·(storeys − 1)` (ground floor 2.0 bar, +0.35 per storey above) | enforced by the compliance engine + the LF2 load case |
| Fire flow | W 405 48/96/192 m³/h by land use @ ≥ 1.5 bar | enforced by the compliance engine + the LF3 load case |

The **W 400-1 three load cases** (`loadcases.py`, the editor's commission gate)
give differentiated, honest verdicts across the bundles: `tutorial_hillside` and
`alpen` pass all three; `musterdorf` fails LF3 (its industry zone's 192 m³/h fire
is unservable at the worst point); `lauenau` fails LF2 (multi-storey taps below
their storey minimum); `neubeuern` fails LF3 (a gravity net can't push fire flow
to its highest, farthest point) — the real "fire flow / storey pressure sizes the
network" insights.

## 8. Reproduction

```bash
# from the repo root, in the dev venv (pip install -e .[dev] installs wntr)
python -m pytest tests/validation -q                 # EPANET Net1/Net3 + PDA-vs-PDD
python -m pytest tests/test_tank_oracle.py \
                 tests/test_hydrant_oracle.py -q      # the mechanism oracles
python -m pytest tests/test_musterdorf.py \
                 tests/test_demand_engine.py -q       # mass balance + W 410 corridor
```

Without WNTR installed, the EPANET/oracle tests **skip** (they do not fail), so
the core suite still runs. The full suite is **268 backend + 36 vitest** green.

## 9. Method transparency & limitations

Every model-alignment choice is stated up front (§ 4.1): D-W with a single shared
roughness, all reservoirs/tanks as fixed heads, **pumps omitted** for the
full-network solve (validated separately via the tank oracle), and pressure
compared as hydraulic head via pandapipes' own gradient. Known limitations, none
hidden:

- The full-network EPANET comparison is on the **distribution** hydraulics (the
  pump raw-water side is a constant-lift model, cross-validated only for its
  control dynamics, not as an EPANET pump element).
- The comparison is **steady** — no transients / surge, no water quality.
- The teaching bundles are **synthetic or synthesised-from-geodata**, so this is
  engine validation, not a field-calibrated model of any specific utility.
- Nikuradse is never the primary model for water (bug #803); `swamee-jain` (hyphen)
  is reserved for these EPANET cross-checks, and the live engine's primary tier is
  implicit Colebrook.
