// Client-side street graph for the water editor (M8 stage 2b): drawn pipes
// snap to the nearest OSM street point and route along the street network
// (Dijkstra). Positions are [lat, lon] (Leaflet-native); metric work is done
// in a local equirectangular projection around the area centre.
import type { LatLon } from "./model";

export interface EditorStreet {
  id: number;
  name: string | null;
  nodes: LatLon[]; // [[lat, lon], ...]
}

interface Seg {
  a: LatLon; b: LatLon;
  am: [number, number]; bm: [number, number];
  lengthM: number;
}

export interface StreetGraph {
  nodes: Map<string, LatLon>;
  adj: Map<string, { to: string; lengthM: number }[]>;
  segments: Seg[];
  toM: (p: LatLon) => [number, number];
}

const keyOf = (p: LatLon) => `${p[0].toFixed(7)},${p[1].toFixed(7)}`;

export function buildStreetGraph(streets: EditorStreet[],
                                 centerLat: number): StreetGraph {
  const cosl = Math.cos((centerLat * Math.PI) / 180);
  // lat → metres north, lon → metres east
  const toM = (p: LatLon): [number, number] =>
    [p[1] * 111320 * cosl, p[0] * 110540];

  const nodes = new Map<string, LatLon>();
  const adj = new Map<string, { to: string; lengthM: number }[]>();
  const segments: Seg[] = [];
  const ensure = (p: LatLon): string => {
    const k = keyOf(p);
    if (!nodes.has(k)) { nodes.set(k, p); adj.set(k, []); }
    return k;
  };
  for (const st of streets) {
    for (let i = 0; i < st.nodes.length - 1; i++) {
      const a = st.nodes[i], b = st.nodes[i + 1];
      const ka = ensure(a), kb = ensure(b);
      if (ka === kb) continue;
      const am = toM(a), bm = toM(b);
      const len = Math.hypot(bm[0] - am[0], bm[1] - am[1]);
      adj.get(ka)!.push({ to: kb, lengthM: len });
      adj.get(kb)!.push({ to: ka, lengthM: len });
      segments.push({ a, b, am, bm, lengthM: len });
    }
  }
  return { nodes, adj, segments, toM };
}

export interface Snap {
  point: LatLon; distM: number; segIdx: number; t: number;
}

/** Nearest point on any street segment, or null when farther than maxM. */
export function snapToStreets(g: StreetGraph, p: LatLon, maxM = 35): Snap | null {
  const pm = g.toM(p);
  let best: { d2: number; point: LatLon; segIdx: number; t: number } | null = null;
  for (let i = 0; i < g.segments.length; i++) {
    const s = g.segments[i];
    const dx = s.bm[0] - s.am[0], dy = s.bm[1] - s.am[1];
    const L2 = dx * dx + dy * dy;
    const t = L2 === 0 ? 0 : Math.max(0, Math.min(1,
      ((pm[0] - s.am[0]) * dx + (pm[1] - s.am[1]) * dy) / L2));
    const cx = s.am[0] + t * dx, cy = s.am[1] + t * dy;
    const d2 = (pm[0] - cx) ** 2 + (pm[1] - cy) ** 2;
    if (!best || d2 < best.d2) {
      best = { d2, segIdx: i, t,
        point: [s.a[0] + t * (s.b[0] - s.a[0]), s.a[1] + t * (s.b[1] - s.a[1])] };
    }
  }
  if (!best) return null;
  const d = Math.sqrt(best.d2);
  return d <= maxM
    ? { point: best.point, distM: d, segIdx: best.segIdx, t: best.t } : null;
}

export interface Route { geometry: LatLon[]; lengthM: number; }

/** Shortest path along the streets between two snapped points (Dijkstra). The
 *  straight line is the fallback when no street route exists. */
export function routeOnStreets(g: StreetGraph, from: Snap, to: Snap): Route | null {
  const segF = g.segments[from.segIdx], segT = g.segments[to.segIdx];
  if (!segF || !segT) return null;
  if (from.segIdx === to.segIdx) {
    const len = Math.abs(from.t - to.t) * segF.lengthM;
    return len < 0.5 ? null : { geometry: [from.point, to.point], lengthM: len };
  }
  const dist = new Map<string, number>();
  const prev = new Map<string, string | null>();
  const heap: { k: string; d: number }[] = [];
  const push = (k: string, d: number, p: string | null) => {
    const cur = dist.get(k);
    if (cur !== undefined && cur <= d) return;
    dist.set(k, d); prev.set(k, p);
    heap.push({ k, d });
    let i = heap.length - 1;
    while (i > 0) {
      const par = (i - 1) >> 1;
      if (heap[par].d <= heap[i].d) break;
      [heap[par], heap[i]] = [heap[i], heap[par]]; i = par;
    }
  };
  const pop = () => {
    const top = heap[0]; const last = heap.pop()!;
    if (heap.length) {
      heap[0] = last; let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1; let mI = i;
        if (l < heap.length && heap[l].d < heap[mI].d) mI = l;
        if (r < heap.length && heap[r].d < heap[mI].d) mI = r;
        if (mI === i) break;
        [heap[mI], heap[i]] = [heap[i], heap[mI]]; i = mI;
      }
    }
    return top;
  };
  const kTa = keyOf(segT.a), kTb = keyOf(segT.b);
  push(keyOf(segF.a), from.t * segF.lengthM, null);
  push(keyOf(segF.b), (1 - from.t) * segF.lengthM, null);
  const settled = new Set<string>();
  while (heap.length) {
    const { k, d } = pop();
    if (d !== dist.get(k) || settled.has(k)) continue;
    settled.add(k);
    if (settled.has(kTa) && settled.has(kTb)) break;
    for (const e of g.adj.get(k) ?? []) push(e.to, d + e.lengthM, k);
  }
  const candA = dist.has(kTa) ? dist.get(kTa)! + to.t * segT.lengthM : Infinity;
  const candB = dist.has(kTb) ? dist.get(kTb)! + (1 - to.t) * segT.lengthM : Infinity;
  const total = Math.min(candA, candB);
  if (!isFinite(total) || total < 0.5) return null;
  const chain: string[] = [];
  for (let k: string | null = candA <= candB ? kTa : kTb; k; k = prev.get(k) ?? null)
    chain.push(k);
  chain.reverse();
  const raw = [from.point, ...chain.map((k) => g.nodes.get(k)!), to.point];
  const geometry = raw.filter(
    (p, i) => i === 0 || p[0] !== raw[i - 1][0] || p[1] !== raw[i - 1][1]);
  return { geometry, lengthM: total };
}
