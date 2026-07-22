// The NetzStudio water-editor model (M8 stage 2b) and its serialisation to the
// five-file bundle the backend loads. A branched gravity network is drawn on
// real OSM streets: one ext_grid SOURCE on the high point, junctions/consumers
// along the streets, pipes routed between them. `toBundle` mirrors the offline
// bundle_builder's synthesis so a hand-drawn net loads/validates/solves the
// same way — and can be commissioned via POST /networks/import.

export type LatLon = [number, number]; // [lat, lon] — Leaflet-native order

export type EditorTool =
  | "pan" | "source" | "junction" | "consumer" | "pipe" | "delete";

export interface ENode {
  id: string;
  lat: number;
  lon: number;
  elevation_m: number | null; // frozen from the DEM at placement; null until set
  kind: "source" | "node" | "consumer";
  name: string;
  // consumer-only
  population?: number;
  storeys?: number;
  consumerKind?: string;
  // source-only: supply pressure at the source node
  p_bar?: number;
}

export interface EPipe {
  id: string;
  from: string; // ENode id
  to: string;   // ENode id
  geometry: LatLon[]; // street-routed polyline incl. endpoints
  dn: number;
  material: string;
}

export interface EditorModel {
  name: string;
  nodes: ENode[];
  pipes: EPipe[];
}

/** Catalog-valid pipe choices (PE d-series outer diameters, GGG DN-series). */
export const PIPE_CATALOG: { label: string; dn: number; material: string }[] = [
  { label: "PE d110", dn: 110, material: "PE" },
  { label: "PE d125", dn: 125, material: "PE" },
  { label: "PE d160", dn: 160, material: "PE" },
  { label: "GGG DN100", dn: 100, material: "GGG" },
  { label: "GGG DN150", dn: 150, material: "GGG" },
  { label: "GGG DN200", dn: 200, material: "GGG" },
];
export const DEFAULT_PIPE = PIPE_CATALOG[2]; // PE d160

export const CONSUMER_KINDS = [
  "residential_village", "residential_city", "industry", "school",
  "office", "hospital", "farm_dairy", "pool",
] as const;
export const DEFAULT_CONSUMER_KIND = "residential_village";
export const DEFAULT_POPULATION = 120;
export const DEFAULT_STOREYS = 2;
//: mean per-capita demand [L/(person·d)] → base sink mdot (ρ ≈ 1)
export const PER_CAPITA_L_PER_D = 130;
//: head held above the highest consumer at the source (≈ 3 bar spare)
export const HEAD_RESERVE_M = 30;
const BAR_PER_M = 0.0979;

export function emptyModel(name = "Mein Netz"): EditorModel {
  return { name, nodes: [], pipes: [] };
}

let _seq = 0;
export function nextId(prefix: string): string {
  _seq += 1;
  return `${prefix}${_seq}`;
}

export const sourceNode = (m: EditorModel): ENode | undefined =>
  m.nodes.find((n) => n.kind === "source");

/** Create a node for the active *tool* at (lat, lon) — the placement logic,
 *  pure + testable (elevation is frozen on by the caller). Returns null when
 *  the source tool is used but a source already exists (one source only).
 *  Names are collision-proof: max existing index + 1, so a delete-then-add
 *  never reuses a name (which would collide in the bundle). */
export function makeNode(nodes: ENode[], tool: EditorTool,
                         lat: number, lon: number): ENode | null {
  if (tool === "source" && nodes.some((n) => n.kind === "source")) return null;
  const kind: ENode["kind"] = tool === "source" ? "source"
    : tool === "consumer" ? "consumer" : "node";
  const nextNum = (pred: (n: ENode) => boolean) =>
    Math.max(0, ...nodes.filter(pred).map(
      (n) => parseInt(n.name.replace(/\D/g, ""), 10) || 0)) + 1;
  return {
    id: nextId(kind[0]), lat, lon, elevation_m: null, kind,
    name: kind === "source" ? "Einspeisung"
      : kind === "consumer" ? `Abnehmer ${nextNum((n) => n.kind === "consumer")}`
      : `Knoten ${nextNum((n) => n.kind === "node")}`,
    ...(kind === "consumer" ? {
      population: DEFAULT_POPULATION, storeys: DEFAULT_STOREYS,
      consumerKind: DEFAULT_CONSUMER_KIND } : {}),
  };
}

/** Problems that block a commission (structural, before the load cases). */
export function validateModel(m: EditorModel): string[] {
  const problems: string[] = [];
  if (m.nodes.length < 2) problems.push("mindestens zwei Knoten setzen");
  if (!sourceNode(m)) problems.push("eine Einspeisung (Quelle) setzen");
  if (m.nodes.some((n) => n.elevation_m == null))
    problems.push("für jeden Knoten eine Höhe abrufen");
  if (!m.pipes.length) problems.push("mindestens eine Leitung ziehen");
  if (!m.nodes.some((n) => n.kind === "consumer"))
    problems.push("mindestens einen Abnehmer setzen");
  // the backend loader rejects duplicate consumer names — surface it in-panel
  // rather than as a raw 422 at commission time (review)
  const cNames = m.nodes.filter((n) => n.kind === "consumer").map((n) => n.name);
  if (new Set(cNames).size !== cNames.length)
    problems.push("doppelte Abnehmer-Namen — einen umbenennen/löschen");
  // connectivity: every node reachable from the source over pipes
  const src = sourceNode(m);
  if (src && m.pipes.length) {
    const adj = new Map<string, string[]>();
    for (const n of m.nodes) adj.set(n.id, []);
    for (const p of m.pipes) {
      adj.get(p.from)?.push(p.to);
      adj.get(p.to)?.push(p.from);
    }
    const seen = new Set<string>([src.id]);
    const stack = [src.id];
    while (stack.length) {
      const k = stack.pop()!;
      for (const nb of adj.get(k) ?? []) if (!seen.has(nb)) { seen.add(nb); stack.push(nb); }
    }
    const isolated = m.nodes.filter((n) => !seen.has(n.id));
    if (isolated.length)
      problems.push(`${isolated.length} Knoten nicht mit der Quelle verbunden`);
  }
  return problems;
}

const pn = (headAbs: number, elev: number): number =>
  Math.max(0.3, Math.round((headAbs - elev) * BAR_PER_M * 100) / 100);

/** Serialise to the five-file bundle (the NetworkImportBundle shape). Throws
 *  if the model is structurally incomplete (call validateModel first). */
export function toBundle(m: EditorModel): {
  name: string; network_structure: unknown; pipes: unknown;
  consumers: unknown; supply: unknown; environment: unknown;
} {
  const src = sourceNode(m);
  if (!src || src.elevation_m == null) throw new Error("no source with elevation");
  const nameOf = new Map<string, string>();
  m.nodes.forEach((n, i) => nameOf.set(n.id, n.kind === "source" ? "quelle" : `n${i}`));

  const consumers = m.nodes.filter((n) => n.kind === "consumer");
  const highestConsumerElev = consumers.length
    ? Math.max(...consumers.map((c) => c.elevation_m ?? src.elevation_m!))
    : src.elevation_m!;
  const headAbs = highestConsumerElev + HEAD_RESERVE_M;
  const sourceP = Math.max(0.5,
    Math.round((headAbs - src.elevation_m!) * BAR_PER_M * 100) / 100);

  const junctions = m.nodes.map((n) => ({
    name: nameOf.get(n.id),
    kind: n.kind === "source" ? "source" : n.kind === "consumer" ? "consumer" : "node",
    geo: [Math.round(n.lat * 1e6) / 1e6, Math.round(n.lon * 1e6) / 1e6],
    elevation_m: Math.round((n.elevation_m ?? 0) * 10) / 10,
    pn_bar: pn(headAbs, n.elevation_m ?? 0),
  }));

  const pipes = m.pipes.map((p) => ({
    from_node: nameOf.get(p.from), to_node: nameOf.get(p.to),
    dn: p.dn, material: p.material, year_laid: 2015,
    geometry: p.geometry.map(([la, lo]) => [
      Math.round(la * 1e6) / 1e6, Math.round(lo * 1e6) / 1e6]),
  }));

  const consumerDocs = consumers.map((c) => {
    const pop = c.population ?? DEFAULT_POPULATION;
    return {
      node: nameOf.get(c.id),
      name: c.name || nameOf.get(c.id),
      mdot_kg_per_s: Math.round(pop * PER_CAPITA_L_PER_D / 86400 * 1e5) / 1e5,
      kind: c.consumerKind ?? DEFAULT_CONSUMER_KIND,
      storeys: c.storeys ?? DEFAULT_STOREYS,
      size: { population: pop },
    };
  });

  // a standard summer day (mirrors the generators/builder)
  const STEPS = 96;
  const t_air = Array.from({ length: STEPS }, (_, i) =>
    Math.round((18 - 7 * Math.cos((2 * Math.PI * (i - 8)) / STEPS)) * 100) / 100);

  return {
    name: m.name,
    network_structure: {
      name: m.name, junctions,
      attribution: [
        "© OpenStreetMap contributors (ODbL)",
        "Elevation: EU-DEM v1.1 — Copernicus data, funded by the European Union",
      ],
    },
    pipes: { pipes },
    consumers: { consumers: consumerDocs },
    supply: {
      supplies: [{ node: nameOf.get(src.id), name: `Einspeisung ${m.name}`,
                   kind: "ext_grid", p_bar: src.p_bar ?? sourceP }],
      prvs: [], tanks: [], stations: [], wellfields: [],
    },
    environment: {
      resolution_minutes: 15, steps: STEPS, t_air_c: t_air,
      day_types: ["workday"], dryness: [0.3], season_day_of_year: 205,
    },
  };
}
