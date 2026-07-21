// Shared color/size scales for the water-network visualization.
//
// Principle carried over from the blueprint: **the unknown is styled as
// unknown** — an element without a measurement renders in a dedicated dim
// grey, never in a color the ramps could produce for a healthy reading.

/** Color for an element with no measurement — deliberately dim/neutral. */
export const UNOBSERVED = "#39424f";
export const UNOBSERVED_LINE = "#2b323c";
/** Dash pattern for unobserved polylines (grey alone can read as "cold"). */
export const UNOBSERVED_DASH = "6 6";

/** Minimum service pressure [bar] — DVGW W 400-1 ground-floor value; the
 *  pressure ramp reads red below this. */
export const P_MIN_BAR = 2.0;
/** High-pressure anchor [bar] — max Ruhedruck per German practice; amber
 *  at/above (PN 10 building installations expect <= 8 bar rest pressure). */
export const P_HIGH_BAR = 8.0;

/** Velocity warning anchor [m/s] (DVGW W 400-1: max 2.0 normal operation). */
export const V_WARN = 2.0;
/** Velocity scale end [m/s] — full red (2.5 short peaks, beyond = red). */
export const V_MAX = 3.5;

/** Metres of water column per bar — derived from the SAME density/gravity
 *  the backend pins (BAR_PER_M = 998.2·9.81/1e5): one constant, both ends.
 *  (rho=1000 shortcuts drifted the HGL ~0.15 % off the solver's own head
 *  bookkeeping — M2 review finding.) */
export const M_PER_BAR = 1e5 / (998.2 * 9.81);
/** kg/s → m³/h at the backend's pinned water density (NOT ×3.6). */
export const M3H_PER_KG_S = 3600 / 998.2;

type Stop = [number, [number, number, number]];

function ramp(stops: Stop[], t: number): string {
  const x = Math.min(1, Math.max(0, t));
  for (let i = 1; i < stops.length; i++) {
    if (x <= stops[i][0]) {
      const [t0, c0] = stops[i - 1];
      const [t1, c1] = stops[i];
      const f = t1 === t0 ? 0 : (x - t0) / (t1 - t0);
      const c = c0.map((v, k) => Math.round(v + (c1[k] - v) * f));
      return `rgb(${c[0]},${c[1]},${c[2]})`;
    }
  }
  const last = stops[stops.length - 1][1];
  return `rgb(${last[0]},${last[1]},${last[2]})`;
}

// Service pressure: red below P_MIN_BAR (2.0), through amber to the green
// plateau 4-6 bar (the DVGW mid-zone recommendation), back to amber toward
// P_HIGH_BAR (8.0). Domain: 0..10 bar mapped 0..1 below.
const P_DOMAIN_BAR = 10.0;
const PRESSURE: Stop[] = [
  [0.0, [239, 68, 68]],            // 0 bar   — red
  [P_MIN_BAR / P_DOMAIN_BAR, [239, 68, 68]],   // 2 bar — still red
  [3.0 / P_DOMAIN_BAR, [245, 158, 11]],        // 3 bar — amber
  [4.0 / P_DOMAIN_BAR, [34, 197, 94]],         // 4 bar — green plateau
  [6.0 / P_DOMAIN_BAR, [34, 197, 94]],         // 6 bar — green plateau
  [P_HIGH_BAR / P_DOMAIN_BAR, [245, 158, 11]], // 8 bar — amber
  [1.0, [245, 158, 11]],           // 10 bar  — amber (hard limit territory)
];

// Velocity: green (idle) … amber at the V_WARN anchor … red at V_MAX.
const VELOCITY: Stop[] = [
  [0.0, [34, 197, 94]],
  [V_WARN / V_MAX, [245, 158, 11]],
  [0.85, [249, 115, 22]],
  [1.0, [239, 68, 68]],
];

/** Service pressure [bar] -> DVGW-anchored ramp (red <2, green 4-6,
 *  amber >=8). */
export function pressureColor(p: number | null | undefined): string {
  if (p == null) return UNOBSERVED;
  return ramp(PRESSURE, p / P_DOMAIN_BAR);
}

/** Flow velocity [m/s] -> green…amber(2.0)…red(3.5). Sign is ignored. */
export function velocityColor(v: number | null | undefined): string {
  if (v == null) return UNOBSERVED;
  return ramp(VELOCITY, Math.abs(v) / V_MAX);
}

/** Map a mass flow to a stroke width, relative to the snapshot's max. */
export function mdotWidth(mdot: number, maxMdot: number): number {
  if (maxMdot <= 0) return 1.5;
  return 1.5 + 4.5 * Math.sqrt(Math.min(1, Math.abs(mdot) / maxMdot));
}

/** Consumer marker radius [px] by design demand [kg/s] (sqrt — area ∝ demand). */
export function consumerRadius(mdotDesign: number | null | undefined): number {
  if (mdotDesign == null || mdotDesign <= 0) return 5;
  return Math.min(14, Math.max(4, 4 + 6 * Math.sqrt(mdotDesign)));
}

export function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null) return "–";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

/** CSS linear-gradient string for a colorbar legend of the given colormap. */
function gradientCss(stops: Stop[]): string {
  const parts = stops.map(
    ([t, c]) => `rgb(${c[0]},${c[1]},${c[2]}) ${(t * 100).toFixed(0)}%`,
  );
  return `linear-gradient(to top, ${parts.join(", ")})`;
}

export const PRESSURE_GRADIENT = gradientCss(PRESSURE);
export const VELOCITY_GRADIENT = gradientCss(VELOCITY);
