// Wire types — mirror the backend contract exactly (hydraulic StepResult,
// GET /network topology, GET /status). Every float went through the
// backend's _r() (NaN/±Inf -> null).

// ---- GET /network -----------------------------------------------------------

export interface TopoNode {
  name: string;
  kind: "node" | "consumer" | "source";
  geo: [number, number]; // [lat, lon] — WGS84, Leaflet-native order
  elevation_m: number;
  pn_bar: number;
}

export interface TopoTrench {
  id: number;
  from_node: string;
  to_node: string;
  length_km: number;
  inner_diameter_mm: number;
  k_mm: number;
  sections: number;
  geometry: [number, number][]; // [[lat, lon], ...]
  pipe: number; // pandapipes element id (single layer: == trench id)
}

export interface TopoConsumer {
  id: number;
  name: string;
  node: string;
  kind: "consumer";
  mdot_demand_kg_per_s: number | null;
}

export interface TopoProducer {
  id: number; // platform-unique pid
  kind: "slack";
  name: string;
  node: string;
}

export interface Topology {
  id: string;
  name: string;
  nodes: TopoNode[];
  trenches: TopoTrench[];
  consumers: TopoConsumer[];
  producers: TopoProducer[];
  steps_per_day: number;
  n_days: number;
}

// ---- GET /status (and every /control/* response) ------------------------------

export interface EngineStatus {
  api_version: string;
  running: boolean;
  step: number;
  day: number;
  time_of_day: string;
  interval_seconds: number;
  steps_per_day: number;
  network: { id: string; name: string };
  latest: {
    step: number;
    day: number;
    time_of_day: string;
    converged: boolean;
    solver_status: string;
    solve_ms: number;
  } | null;
}

// ---- StepResult (REST /state, /history entries, every WS frame) ---------------

export interface JunctionState {
  id: number;
  name: string;
  p_bar: number | null;
}

export interface PipeState {
  id: number;
  trench: number; // single layer: == id
  mdot_kg_per_s: number | null;
  v_m_per_s: number | null;
  dp_bar: number | null;
}

export interface ConsumerState {
  id: number;
  name: string;
  node: string;
  kind?: "consumer";
  mdot_demand_kg_per_s: number | null;
  mdot_kg_per_s: number | null;
  p_bar: number | null;
}

export interface ProducerState {
  id: number;
  kind: "slack";
  name: string;
  node: string;
  p_bar?: number | null;
  mdot_kg_per_s?: number | null;
}

export interface StepSummary {
  p_min_bar: number | null;
  worst_consumer: string | null;
  worst_node: string | null;
  mdot_feed_kg_per_s: number | null;
  mdot_demand_kg_per_s: number | null;
  mdot_delivered_kg_per_s: number | null;
  balance_err_kg_per_s: number | null;
}

export interface Controls {
  /** The observed layer misses the TRUE min-pressure worst point (no usable
   *  reading, or the critical consumer carries no meter). Null pre-solve. */
  blind_spot?: boolean | null;
}

/** Water-meter reading at a consumer (mdot + local pressure). In standard
 *  fidelity every channel is a 15-min-window mean and null until the first
 *  window after placement closes (honest cold start). */
export interface ConsumerMeasurement {
  id: number;
  name: string;
  node: string;
  mdot_kg_per_s: number | null;
  p_bar: number | null;
}

/** Pressure-sensor reading at a node's junction. */
export interface NodeMeasurement {
  node: string;
  p_bar: number | null;
}

export type MeterMode = "full" | "standard";
export type MeterPreset = "all_consumers" | "plant_only" | "key_points" | "clear";

export interface Measurements {
  preset?: string; // MeterPreset | "custom"
  mode?: MeterMode;
  consumers?: ConsumerMeasurement[];
  nodes?: NodeMeasurement[];
  plant?: {
    mdot_kg_per_s: number | null;
    p_bar: number | null;
  };
}

export interface ObservedSummary {
  mdot_feed_kg_per_s: number | null;
  mdot_demand_metered_kg_per_s: number | null;
  n_metered: number;
  n_consumers: number;
  n_node_sensors?: number;
  n_nodes?: number;
  p_min_bar: number | null;
  worst_consumer: string | null;
  p_source_bar: number | null;
}

// ---- GET /measurements + every /measurements/* verb ----------------------------

export interface MeasurementsResponse {
  preset: string; // MeterPreset | "custom"
  mode: MeterMode;
  consumer_meters: { id: number; name: string | null; node: string | null }[];
  node_sensors: string[];
  coverage: {
    n_consumers: number;
    n_consumer_meters: number;
    consumer_fraction: number;
    n_nodes: number;
    n_node_sensors: number;
    node_fraction: number;
  };
  expose_ground_truth: boolean;
}

/** The estimated layer — STUBBED in M0 (always null on the wire); the type
 *  stays so the three-view splice pattern survives for the M7 water observer. */
export interface EstimatedState {
  junctions: JunctionState[];
  pipes: PipeState[];
  consumers: ConsumerState[];
  summary: StepSummary;
  error: {
    max_dmdot_kg_per_s: number | null;
    mean_dmdot_kg_per_s: number | null;
    max_dp_bar: number | null;
    mean_dp_bar: number | null;
    n_points: number;
  };
  step: number;
  day: number;
  seq: number;
  solve_ms: number;
  solver_status: "ok" | "degraded";
}

/** The single wire format. In strict mode (RTWATERFLOW_EXPOSE_GROUND_TRUTH=
 *  false) the truth keys junctions/pipes/consumers/summary are ABSENT and
 *  error is blanked — the UI falls back to the measured view. */
export interface StepResult {
  step: number;
  day: number;
  time_of_day: string;
  converged: boolean;
  solver_status: "ok" | "degraded" | "failed";
  solve_ms: number;
  timestamp: number;
  junctions?: JunctionState[];
  pipes?: PipeState[];
  consumers?: ConsumerState[];
  summary?: StepSummary;
  producers: ProducerState[];
  controls: Controls;
  measurements: Measurements;
  observed_summary: ObservedSummary | null;
  estimated: EstimatedState | null;
  error: string | null;
}

// ---- GET/POST /estimation/config (stub in M0) -----------------------------------

export interface EstimationConfigInfo {
  enabled: boolean;
  throttle_factor: number;
  seq: number;
  last_solve_ms: number | null;
}

// ---- networks / config ----------------------------------------------------------

export interface NetworkListItem {
  id: string;
  name: string;
  character: string | null;
  nodes: number | null;
  pipe_km: number | null;
  source: string;
}

export interface NetworkPreview {
  id: string;
  name: string;
  character: string | null;
  n_nodes: number;
  n_pipes: number;
  n_consumers: number;
  n_supplies: number;
  pipe_km: number;
  demand_kg_per_s: number;
  demand_m3_per_h: number;
  elevation_min_m: number;
  elevation_max_m: number;
  resolution_minutes: number;
  steps: number;
  n_days: number;
  supply: {
    node: string;
    name: string | null;
    p_bar: number;
  };
}

export interface ActiveConfig {
  network_id: string;
  name: string;
  source: string;
  applied_at: number;
  n_consumers: number;
  n_days: number;
  scenario?: string;
}

export interface ApplyResponse {
  status: EngineStatus;
  active: ActiveConfig;
  network: Topology;
}

// ---- scenarios ------------------------------------------------------------------

export interface ScenarioInfo {
  id: string;
  name: string;
  description: string;
  network_id: string | null;
  created: string | null;
}

// ---- recording & bulk export -----------------------------------------------------

export interface RecordingStatus {
  active: boolean;
  id: string | null;
  steps: number;
  started: string | null;
  bytes: number;
}

export interface RecordingInfo {
  id: string;
  network: string | null;
  started: string | null;
  ended: string | null;
  steps: number | null;
  bytes: number;
}

export interface ExportStatus {
  active: boolean;
  id?: string;
  days?: number[];
  steps_total?: number;
  steps_done?: number;
  day?: number | null;
  started?: number;
  eta_seconds?: number;
  error?: string | null;
  cancelled?: boolean;
}

/** POST /networks/import — the five contract documents as one bundle. */
export interface NetworkImportBundle {
  name?: string;
  network_structure: unknown;
  pipes: unknown;
  consumers: unknown;
  supply: unknown;
  environment: unknown;
}
