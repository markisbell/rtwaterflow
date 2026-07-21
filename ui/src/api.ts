import type {
  ActiveConfig,
  ApplyResponse,
  EmitterInfo,
  EmitterState,
  EngineStatus,
  EnvironmentInfo,
  EstimationConfigInfo,
  ExportStatus,
  MeasurementsResponse,
  MeterMode,
  MeterPreset,
  NetworkImportBundle,
  NetworkListItem,
  NetworkPreview,
  RecordingInfo,
  RecordingStatus,
  ScenarioInfo,
  StationInfo,
  StationMode,
  StepResult,
  TankState,
  Topology,
} from "./types";

// All backend calls go through "/api" (Vite dev proxy / nginx in prod);
// the prefix is stripped by the proxy rewrite.
const API = "/api";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`);
  if (!r.ok) throw new Error(`GET ${path} -> ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`POST ${path} -> ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function del<T>(path: string): Promise<T> {
  const r = await fetch(`${API}${path}`, { method: "DELETE" });
  if (!r.ok) throw new Error(`DELETE ${path} -> ${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

// Typed wrappers for the M0 surface (docs/API.md; engine verbs take JSON
// bodies, unlike the blueprint's query params).
export const api = {
  network: () => get<Topology>("/network"),
  status: () => get<EngineStatus>("/status"),
  state: () => get<StepResult>("/state"),
  history: (limit = 96) => get<StepResult[]>(`/history?limit=${limit}`),

  start: () => post<EngineStatus>("/control/start"),
  pause: () => post<EngineStatus>("/control/pause"),
  resume: () => post<EngineStatus>("/control/resume"),
  seek: (step: number) => post<EngineStatus>("/control/seek", { step }),
  seekDay: (day: number) => post<EngineStatus>("/control/seekday", { day }),
  stepInterval: (seconds: number) =>
    post<EngineStatus>("/control/interval", { seconds }),

  producers: () => get<Record<string, unknown>[]>("/producers"),

  // ---- tanks + pump stations (M2) ----
  tanks: () => get<TankState[]>("/tanks"),
  stations: () => get<StationInfo[]>("/stations"),
  setStationMode: (name: string, mode: StationMode) =>
    post<{ name: string; mode: StationMode }>(
      `/station/${encodeURIComponent(name)}`, { mode }),

  // ---- environment / weather knob (M3) ----
  environment: () => get<EnvironmentInfo>("/environment"),
  setEnvironment: (body: {
    t_offset_c?: number;
    dryness?: number;
    clear_dryness?: boolean;
  }) => post<EnvironmentInfo>("/environment", body),

  // ---- pressure-dependent hydraulics: emitters + PDA (M5) ----
  emitters: () => get<EmitterInfo>("/emitters"),
  openHydrant: (body: {
    node: string; target_m3_h: number; duration_minutes?: number;
    name?: string;
  }) => post<EmitterState>("/hydrant", body),
  placeBurst: (body: { node: string; area_m2: number; name?: string }) =>
    post<EmitterState>("/burst", body),
  setLeakage: (coefficient_per_km: number) =>
    post<{ leaks: number; coefficient_per_km: number }>(
      "/leakage", { coefficient_per_km }),
  clearLeakage: () => del<{ cleared: number }>("/leakage"),
  removeEmitter: (name: string) =>
    del<{ removed: string }>(`/emitter/${encodeURIComponent(name)}`),
  setPda: (enabled: boolean) =>
    post<{ pda_enabled: boolean }>("/pda", { enabled }),

  // ---- consumers (M0: fixed demand) ----
  addConsumer: (body: {
    node: string;
    name?: string;
    mdot_kg_per_s: number;
  }) => post<{ added: { id: number } }>("/consumer", body),
  removeConsumer: (id: number) => del<unknown>(`/consumer/${id}`),

  // ---- sensor placement ----
  measurements: () => get<MeasurementsResponse>("/measurements"),
  placeConsumerMeter: (id: number) =>
    post<MeasurementsResponse>(`/measurements/consumer/${id}`),
  removeConsumerMeter: (id: number) =>
    del<MeasurementsResponse>(`/measurements/consumer/${id}`),
  placeNodeSensor: (node: string) =>
    post<MeasurementsResponse>(`/measurements/node/${encodeURIComponent(node)}`),
  removeNodeSensor: (node: string) =>
    del<MeasurementsResponse>(`/measurements/node/${encodeURIComponent(node)}`),
  setMeasurementMode: (mode: MeterMode) =>
    post<MeasurementsResponse>("/measurements/mode", { mode }),
  setMeasurementPreset: (preset: MeterPreset) =>
    post<MeasurementsResponse>("/measurements/preset", { preset }),

  // ---- estimation policy (stub in M0) ----
  estimationConfig: () => get<EstimationConfigInfo>("/estimation/config"),
  manualUrl: () => `${API}/manual`,

  // ---- network catalog + swap ----
  networks: () => get<{ available: boolean; networks: NetworkListItem[] }>("/networks"),
  networkPreview: (id: string) => get<NetworkPreview>(`/networks/${id}`),
  importNetwork: (bundle: NetworkImportBundle) =>
    post<NetworkPreview>("/networks/import", bundle),
  applyConfig: (network_id: string) =>
    post<ApplyResponse>("/config/apply", { network_id }),
  activeConfig: () => get<ActiveConfig>("/config/active"),

  // ---- scenarios ----
  scenarios: () => get<{ scenarios: ScenarioInfo[] }>("/scenarios"),
  saveScenario: (name: string, description = "") =>
    post<{ id: string; name: string }>("/scenarios", { name, description }),
  loadScenario: (sid: string) => post<ApplyResponse>(`/scenarios/${sid}/load`),
  deleteScenario: (sid: string) => del<unknown>(`/scenarios/${sid}`),

  // ---- session recording + bulk export ----
  recording: () => get<RecordingStatus>("/recording"),
  recordingStart: (name?: string) =>
    post<RecordingStatus>("/recording/start", name ? { name } : undefined),
  recordingStop: () => post<RecordingStatus>("/recording/stop"),
  recordings: () =>
    get<{ recordings: RecordingInfo[]; active: RecordingStatus }>("/recordings"),
  recordingDownloadUrl: (rid: string) =>
    `${API}/recordings/${encodeURIComponent(rid)}/download`,
  deleteRecording: (rid: string) =>
    del<{ deleted: string }>(`/recordings/${encodeURIComponent(rid)}`),
  exportDays: (days: number | number[], name?: string) =>
    post<ExportStatus>("/export/days", { days, name }),
  exportStatus: () => get<ExportStatus>("/export"),
  exportCancel: () => post<ExportStatus>("/export/cancel"),
};

export function wsUrl(): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}
