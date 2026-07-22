# rtwaterflow API reference

> **Generated** by `scripts/gen_api_doc.py` — do not edit by hand.
> API version **0.1.0** · interactive docs at `/docs` (Swagger) when the
> backend runs · default bind `127.0.0.1:8002` (sibling scheme: rtheatflow owns 8001, netzsim 8000), no auth (teaching tool).

The single wire format is the projected hydraulic `StepResult`: `/state`,
`/history` items and every `WS /ws` message share one `asdict()` + projection
path. In strict mode (`RTWATERFLOW_EXPOSE_GROUND_TRUTH=false`) the
ground-truth keys (`junctions`, `pipes`, `consumers`, `summary`) are stripped
and the free-text `error` detail is blanked; `measurements` /
`observed_summary` (the operator view) always remain.

**Error-code conventions**: `400` validation/import rejection ·
`404` missing resource or `/state` before the first solve · `409` conflicts
(last-consumer removal, recording busy, export running) · `422` semantic
limits · `500` internal failures only —
**solver non-convergence is data (`converged=false` frames), never a 500.**


## core

| Method | Path | Summary |
|---|---|---|
| `GET` | `/` | Built-in HTML live monitor |
| `GET` | `/health` | Liveness probe |
| `GET` | `/history` | Recent frames |
| `GET` | `/manual` | Benutzerhandbuch (German user manual) |
| `GET` | `/network` | Static topology |
| `GET` | `/state` | Latest solved frame |
| `GET` | `/status` | Engine status |
| `WS` | `/ws` | One message type: the full projected StepResult per solved step. |

- **`GET /`** — Minimal self-contained live monitor fed by ``WS /ws`` (blueprint style).
- **`GET /health`** — Cheap liveness check for launchers/containers (no engine access).
- **`GET /history`** — The most recent frames (oldest first), through the same projection path as ``/state``. Bounded by ``RTWATERFLOW_HISTORY_SIZE``.
- **`GET /manual`** — The German user manual, rendered as HTML (``?format=md`` for the raw Markdown source). Authored in ``docs/Benutzerhandbuch.md``.
- **`GET /network`** — Active network topology: nodes (with elevation_m), trenches (single pipe layer, catalog sizing dn/material + geometry), consumers, producers and prvs (Druckminderer branches — zone boundaries). Rebuilt per request — consumer CRUD changes the inventory live.
- **`GET /state`** — The latest StepResult wire frame (projected). **404 before the first solve**; a failed solve still yields a frame with ``converged=false``.
- **`GET /status`** — Engine clock, run state, interval, active network, latest-frame digest.
- **`WS /ws`** — accept → subscribe → send latest if present → receive loop (the client sends nothing; receiving only detects disconnect). Dead sockets are discarded by the store on send failure (SPEC §7).

## control

| Method | Path | Summary |
|---|---|---|
| `POST` | `/control/interval` | Set the wall-clock step interval |
| `POST` | `/control/pause` | Pause the tick loop |
| `POST` | `/control/resume` | Resume a paused tick loop |
| `POST` | `/control/seek` | Jump to a step of day |
| `POST` | `/control/seekday` | Jump to a day |
| `POST` | `/control/start` | Start (or un-pause) the tick loop |

## producers

| Method | Path | Summary |
|---|---|---|
| `GET` | `/producers` | Head-source inventory |
| `POST` | `/station/{name}` | Override a station |
| `GET` | `/stations` | Pump stations |
| `GET` | `/tanks` | Tank states |

- **`GET /producers`** — All head sources with their current configuration (live table values).
- **`POST /station/{name}`** — Operator override for one pump station: ``auto`` hands control back to the hysteresis rule (manual-control stations return to their CONFIGURED running state); ``on``/``off`` force the state. Applied on the next tick (pre-solve, like every operating rule).
- **`GET /stations`** — Pump stations: control configuration, operator mode and the live running state.
- **`GET /tanks`** — Live tank states: level, usable volume, buffer time at the current draw, and the alarm flags (overflow / empty / fire-reserve breached). ``id`` is the platform producer pid (joinable with /producers).

## environment

| Method | Path | Summary |
|---|---|---|
| `GET` | `/environment` | Environment drivers + overrides |
| `POST` | `/environment` | Set weather overrides |

- **`GET /environment`** — Bundle environment drivers and the active runtime overrides.
- **`POST /environment`** — Apply runtime weather overrides (Hitzetag: ``t_offset_c`` + ``dryness`` 1.0). The demand engine rebuilds the archetype profiles from the next tick; legacy demand_factor bundles only shift their displayed temperature.

## compliance

| Method | Path | Summary |
|---|---|---|
| `GET` | `/findings` | Compliance findings (alarm center) |

- **`GET /findings`** — Current findings with severity counts. Every finding cites its rule (DVGW W 400-1 / W 405 / W 300-1 — docs/COMPLIANCE.md maps the checks).

## emitters

| Method | Path | Summary |
|---|---|---|
| `POST` | `/burst` | Place a pipe burst |
| `DELETE` | `/emitter/{name}` | Remove an emitter |
| `GET` | `/emitters` | Emitter states (leaks/hydrants/bursts) |
| `POST` | `/hydrant` | Open a fire hydrant |
| `DELETE` | `/leakage` | Clear background leakage |
| `POST` | `/leakage` | Seed background leakage |
| `GET` | `/pda` | Pressure-driven-demand toggle |
| `POST` | `/pda` | Toggle pressure-driven demand |

- **`GET /emitters`** — Live emitter states + the PDA toggle.
- **`POST /pda`** — ON (default): undersupplied taps deliver less (Wagner). OFF: fixed demand — undersupply shows as impossible negative pressure (the M0 contrast that motivates PDA).

## wellfield

| Method | Path | Summary |
|---|---|---|
| `POST` | `/wellfield/drought` | Set the drought factor |
| `POST` | `/wellfield/{name}/well/{well}/regenerate` | Regenerate a well |
| `GET` | `/wellfields` | Well-field states (raw-water side) |

- **`POST /wellfield/drought`** — Scale groundwater recharge on every aquifer. Below normal the level declines under abstraction, capping well production — the Lauenau drought mechanism.
- **`POST /wellfield/{name}/well/{well}/regenerate`** — Well regeneration (W 130): restore ~90 % of the nameplate specific capacity (the maintenance action against Verockerung/ageing).
- **`GET /wellfields`** — Live raw-side states: aquifer level, production, capacity, well ageing, water-right accounting, energy KPI.

## consumers

| Method | Path | Summary |
|---|---|---|
| `POST` | `/consumer` | Place a consumer |
| `DELETE` | `/consumer/{consumer_id}` | Remove a consumer |

- **`POST /consumer`** — Place a fixed-demand sink at an existing node. 400 on unknown node.
- **`DELETE /consumer/{consumer_id}`** — Remove by the sink element id reported in the frame's ``consumers`` list. 404 unknown; 409 for the last remaining consumer.

## measurements

| Method | Path | Summary |
|---|---|---|
| `GET` | `/estimation/config` | Estimation policy |
| `POST` | `/estimation/config` | Configure the estimation |
| `GET` | `/measurements` | Sensor placement + coverage |
| `DELETE` | `/measurements/consumer/{consumer_id}` | Remove a water meter |
| `POST` | `/measurements/consumer/{consumer_id}` | Place a water meter |
| `POST` | `/measurements/mode` | Set the meter fidelity mode |
| `DELETE` | `/measurements/node/{node_id}` | Remove a pressure sensor |
| `POST` | `/measurements/node/{node_id}` | Place a pressure sensor |
| `POST` | `/measurements/preset` | Apply a placement preset |

- **`GET /estimation/config`** — The estimation policy (enabled / prior basis / throttle) plus the current estimate sequence number and last-solve runtime. M7: the forward observer produces the ``estimated`` layer; ``enabled`` defaults to true.
- **`POST /estimation/config`** — Partial update. The policy survives grid swaps and scenario loads (held on the engine). The forward observer (M7) refreshes the estimate on converged frames per the metering raster + self-throttle.
- **`GET /measurements`** — Which consumers carry a water meter, which nodes a pressure sensor, the fidelity mode, and coverage fractions per element class. Source SCADA is always measured (real waterworks are) and does not appear as a placement.
- **`POST /measurements/consumer/{consumer_id}`** — Install a Wasserzähler at the consumer. In standard mode the new meter starts cold: readings stay null until its first 15-minute window closes.
- **`POST /measurements/mode`** — Bulk fidelity switch for every placed device: ``full`` = every channel every step; ``standard`` = 15-min-window means aligned to simulated time, null until the first window closes (honest cold start — the window state resets on every switch). Source SCADA stays live either way.
- **`POST /measurements/node/{node_id}`** — Install a pressure sensor at a node: it reads ``p_bar`` at that node's junction.
- **`POST /measurements/preset`** — Replace the placement wholesale: ``all_consumers`` (meter at every consumer — the default), ``plant_only`` (source pressure only), ``key_points`` (source + net ends + a meter at the currently known worst point), ``clear`` (no devices — the operator flies blind).

## networks

| Method | Path | Summary |
|---|---|---|
| `GET` | `/config/active` | Active configuration |
| `POST` | `/config/apply` | Swap the running network |
| `GET` | `/networks` | Network library |
| `POST` | `/networks/import` | Import a network (five-file bundle) |
| `GET` | `/networks/{network_id}` | Network preview |

- **`GET /config/active`** — Metadata of the currently loaded network (id, source).
- **`POST /config/apply`** — Load a catalog network and swap the running engine onto it: new Simulator built off-thread, store reset, clock at day 0 / step 0 — never a process restart.
- **`GET /networks`** — List the loadable networks of the committed library manifest.
- **`POST /networks/import`** — Import a five-file network bundle into ``data/user_networks/<id>/``. The documents are written to disk and validated by actually loading them through the full five-file contract (pydantic models + cross-validation); a bundle that does not load is removed again (400 — blueprint convention). On success the catalog is rescanned and the network appears in ``GET /networks`` with ``source="user"``.
- **`GET /networks/{network_id}`** — Net-free preview stats of a catalog network (loads + validates the five-file bundle on first access, cached).

## editor

| Method | Path | Summary |
|---|---|---|
| `POST` | `/editor/elevation` | DEM elevation for clicked points |
| `GET` | `/editor/geocode` | Place-name search (Nominatim) |
| `POST` | `/editor/loadcheck` | DVGW W 400-1 three-load-case check |
| `GET` | `/editor/streets` | OSM streets + buildings for a bbox |

- **`POST /editor/elevation`** — Frozen elevation for each point (EU-DEM 25 m via OpenTopoData) — the editor stamps it into the junction at edit time (TF §11).
- **`GET /editor/geocode`** — Resolve a place name → [{name, lat, lon}] for the map search box.
- **`POST /editor/loadcheck`** — Run the three W 400-1 sizing load cases (max delivery / peak-hour max day / fire case) on the five-file *bundle* the editor is building. Returns pass/fail per case + the binding quantities. 422 if the bundle does not validate (a structural problem the editor must fix first).
- **`GET /editor/streets`** — Streets (drawn pipes snap onto them) + building footprints (consumer placement) for the bbox (south, west, north, east). Overpass, cached.

## scenarios

| Method | Path | Summary |
|---|---|---|
| `GET` | `/scenarios` | Saved scenarios |
| `POST` | `/scenarios` | Save the live setup as a scenario |
| `DELETE` | `/scenarios/{sid}` | Delete a scenario |
| `POST` | `/scenarios/{sid}/load` | Load a scenario |

- **`GET /scenarios`** — Saved scenario recipes (name, description, network, created).
- **`POST /scenarios`** — Save the CURRENT live setup as a recipe: network id + runtime consumer ops + sensor placement + the engine clock. Recipes, not snapshots — same name overwrites.
- **`POST /scenarios/{sid}/load`** — Replay a scenario recipe: network swap, then the runtime layers (consumer ops, sensor placement), seek to the stored clock and run. Tolerant per entry — mismatching ops are skipped with a warning, never a partial 500.

## recording

| Method | Path | Summary |
|---|---|---|
| `GET` | `/export` | Export progress |
| `POST` | `/export/cancel` | Cancel the export |
| `POST` | `/export/days` | Bulk-export whole days |
| `GET` | `/recording` | Recorder status |
| `POST` | `/recording/start` | Start recording |
| `POST` | `/recording/stop` | Stop recording |
| `GET` | `/recordings` | Stored recordings |
| `DELETE` | `/recordings/{rid}` | Delete a recording |
| `GET` | `/recordings/{rid}/download` | Download a recording (ZIP) |

- **`GET /export`** — Progress of the bulk export (steps done/total, ETA, errors).
- **`POST /export/cancel`** — Stop the running bulk export; the partial pack is kept and finalized.
- **`POST /export/days`** — Replay whole days of the CURRENT setup offline, as fast as possible, into a recording pack (appears under ``/recordings`` when finished; byte-compatible with a live recording). One export at a time (409).
- **`GET /recording`** — State of the session recorder (active recording, steps, size).
- **`POST /recording/start`** — Record every published frame to ``data/recordings/<id>/`` (CSV pack + metadata.json recipe). One recording at a time (409).
- **`POST /recording/stop`** — Finish the active recording (flush, close, write metadata.json).
- **`GET /recordings`** — Stored recordings (finished ones carry metadata.json) + the recorder state, one poll for the Datei menu.
- **`DELETE /recordings/{rid}`** — Remove a stored recording (and its cached ZIP).
- **`GET /recordings/{rid}/download`** — The recording as a ZIP of CSVs + metadata.json.
