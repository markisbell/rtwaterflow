/**
 * Drucklinie (hydraulic grade line) — the single best water-network teaching
 * visual: along the path from the head source to a chosen consumer, the
 * terrain profile (filled) and the HGL = elevation + pressure head
 * (p_bar × 10.197 m). The vertical gap between the two lines IS the local
 * service pressure; at a PRV the HGL drops in a visible step.
 *
 * Pure client-side: shortest path (Dijkstra over pipe lengths; PRV branches
 * count as ~zero-length edges) on the static topology + junction pressures
 * from the live frame. Hand-rolled SVG like Sparkline (no chart libs).
 */
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { StepResult, Topology } from "../types";
import { fmt } from "../scales";
import Section from "./Section";

const M_PER_BAR = 10.197; // metres of water column per bar

export interface PathPoint {
  node: string;
  dist_km: number;
  elevation_m: number;
}

/** Dijkstra over the pipe graph (+ PRV edges) from *source* to *target*.
 *  Exported for unit tests (a silent null here kills the flagship
 *  teaching visual with no error). */
export function shortestPath(topo: Topology, source: string,
                             target: string): PathPoint[] | null {
  const adj = new Map<string, { to: string; km: number }[]>();
  const edge = (a: string, b: string, km: number) => {
    if (!adj.has(a)) adj.set(a, []);
    adj.get(a)!.push({ to: b, km });
  };
  for (const tr of topo.trenches) {
    edge(tr.from_node, tr.to_node, tr.length_km);
    edge(tr.to_node, tr.from_node, tr.length_km);
  }
  for (const v of topo.prvs ?? []) {
    edge(v.from_node, v.to_node, 1e-4);
    edge(v.to_node, v.from_node, 1e-4);
  }
  const dist = new Map<string, number>([[source, 0]]);
  const prev = new Map<string, string>();
  const todo = new Set<string>([source]);
  while (todo.size) {
    let u: string | null = null;
    let best = Infinity;
    for (const n of todo) {
      const d = dist.get(n) ?? Infinity;
      if (d < best) { best = d; u = n; }
    }
    if (u === null) break;
    todo.delete(u);
    if (u === target) break;
    for (const { to, km } of adj.get(u) ?? []) {
      const nd = best + km;
      if (nd < (dist.get(to) ?? Infinity)) {
        dist.set(to, nd);
        prev.set(to, u);
        todo.add(to);
      }
    }
  }
  if (!dist.has(target)) return null;
  const elev = new Map(topo.nodes.map((n) => [n.name, n.elevation_m]));
  const chain: string[] = [target];
  while (chain[0] !== source) {
    const p = prev.get(chain[0]);
    if (!p) return null;
    chain.unshift(p);
  }
  return chain.map((node) => ({
    node,
    dist_km: dist.get(node) ?? 0,
    elevation_m: elev.get(node) ?? 0,
  }));
}

export default function DrucklinieSection({ open, onToggle, topo, latest }: {
  open: boolean;
  onToggle: () => void;
  topo: Topology;
  latest: StepResult | null;
}) {
  const { t } = useTranslation();
  const source = topo.producers.find((p) => p.kind === "slack")?.node;
  const worst = latest?.summary?.worst_node ?? null;
  const [target, setTarget] = useState<string | "worst">("worst");
  const effTarget = target === "worst" ? worst : target;

  const path = useMemo(
    () => (source && effTarget && effTarget !== source
      ? shortestPath(topo, source, effTarget) : null),
    [topo, source, effTarget]);

  const pByName = useMemo(() => {
    const m = new Map<string, number>();
    for (const j of latest?.junctions ?? []) {
      if (j.p_bar != null) m.set(j.name, j.p_bar);
    }
    return m;
  }, [latest]);

  const W = 300;
  const H = 150;
  const PAD = { l: 34, r: 6, t: 8, b: 18 };

  let svg = null;
  if (path && path.length >= 2 && pByName.size) {
    const totalKm = path[path.length - 1].dist_km || 1e-9;
    const hgl = path.map((p) => {
      const bar = pByName.get(p.node);
      return bar != null ? p.elevation_m + bar * M_PER_BAR : null;
    });
    const yMin = Math.min(...path.map((p) => p.elevation_m)) - 5;
    const yMax = Math.max(
      ...hgl.filter((v): v is number => v != null),
      ...path.map((p) => p.elevation_m)) + 5;
    const x = (km: number) =>
      PAD.l + ((W - PAD.l - PAD.r) * km) / totalKm;
    const y = (m: number) =>
      PAD.t + (H - PAD.t - PAD.b) * (1 - (m - yMin) / (yMax - yMin));

    const terrain = path.map(
      (p) => `${x(p.dist_km).toFixed(1)},${y(p.elevation_m).toFixed(1)}`);
    const terrainArea = `${PAD.l},${H - PAD.b} ${terrain.join(" ")} `
      + `${x(totalKm).toFixed(1)},${H - PAD.b}`;
    const hglPts = path
      .map((p, i) => (hgl[i] != null
        ? `${x(p.dist_km).toFixed(1)},${y(hgl[i]!).toFixed(1)}` : null))
      .filter((s): s is string => s != null);

    svg = (
      <svg width="100%" viewBox={`0 0 ${W} ${H}`}
           style={{ display: "block" }}>
        <polygon points={terrainArea} fill="#4b5563" opacity={0.45} />
        <polyline points={terrain.join(" ")} fill="none"
                  stroke="#9ca3af" strokeWidth={1.2} />
        <polyline points={hglPts.join(" ")} fill="none"
                  stroke="#38bdf8" strokeWidth={2} />
        {/* axis hints */}
        <text x={PAD.l - 4} y={y(yMax - 5) + 4} fontSize={9}
              fill="var(--muted, #8a949e)" textAnchor="end">
          {fmt(yMax - 5, 0)} m
        </text>
        <text x={PAD.l - 4} y={y(yMin + 5) + 4} fontSize={9}
              fill="var(--muted, #8a949e)" textAnchor="end">
          {fmt(yMin + 5, 0)} m
        </text>
        <text x={PAD.l} y={H - 5} fontSize={9}
              fill="var(--muted, #8a949e)">0</text>
        <text x={W - PAD.r} y={H - 5} fontSize={9}
              fill="var(--muted, #8a949e)" textAnchor="end">
          {fmt(totalKm, 2)} km
        </text>
      </svg>
    );
  }

  return (
    <Section title={t("hgl.heading")} open={open} onToggle={onToggle}>
      <div className="field">
        <label>{t("hgl.target")}</label>
        <select value={target}
                onChange={(e) => setTarget(e.target.value)}>
          <option value="worst">{t("hgl.targetWorst")}</option>
          {topo.consumers.map((c) => (
            <option key={c.id} value={c.node}>{c.name}</option>
          ))}
        </select>
      </div>
      {svg ?? (
        <div className="muted" style={{ fontSize: "0.75rem" }}>
          {t("hgl.noData")}
        </div>
      )}
      <div className="muted" style={{ fontSize: "0.68rem", marginTop: 4 }}>
        <span style={{ color: "#38bdf8" }}>■</span> {t("hgl.hgl")} ·{" "}
        <span style={{ color: "#6b7280" }}>■</span> {t("hgl.terrain")}
        <br />{t("hgl.hint")}
      </div>
    </Section>
  );
}
