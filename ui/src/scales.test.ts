import { describe, expect, it } from "vitest";
import {
  P_HIGH_BAR,
  P_MIN_BAR,
  UNOBSERVED,
  V_WARN,
  consumerRadius,
  mdotWidth,
  pressureColor,
  velocityColor,
} from "./scales";

// The ramps are DOMAIN-ANCHORED to DVGW practice: red strictly below the
// 2.0 bar minimum service pressure, green plateau across the recommended
// 4-6 bar mid-zone band, amber at/above the 8 bar rest-pressure limit —
// and the unknown is styled as unknown (dedicated grey that no healthy
// ramp value can produce).

const RED = "rgb(239,68,68)";
const AMBER = "rgb(245,158,11)";
const GREEN = "rgb(34,197,94)";

describe("pressureColor", () => {
  it("is red at and below the W 400-1 minimum service pressure", () => {
    expect(pressureColor(0)).toBe(RED);
    expect(pressureColor(1.0)).toBe(RED);
    expect(pressureColor(P_MIN_BAR)).toBe(RED);
  });
  it("is green across the recommended 4-6 bar mid-zone band", () => {
    expect(pressureColor(4.0)).toBe(GREEN);
    expect(pressureColor(5.0)).toBe(GREEN);
    expect(pressureColor(6.0)).toBe(GREEN);
  });
  it("is amber at/above the 8 bar rest-pressure limit", () => {
    expect(pressureColor(P_HIGH_BAR)).toBe(AMBER);
    expect(pressureColor(9.5)).toBe(AMBER);
    expect(pressureColor(12)).toBe(AMBER); // above domain -> clamp
  });
  it("passes through amber between red and green (3 bar)", () => {
    expect(pressureColor(3.0)).toBe(AMBER);
  });
});

describe("velocityColor", () => {
  it("hits the amber warning anchor exactly at V_WARN (2.0 m/s, W 400-1)", () => {
    expect(velocityColor(V_WARN)).toBe(AMBER);
  });
  it("is green when idle and full red at/beyond 3.5 m/s", () => {
    expect(velocityColor(0)).toBe(GREEN);
    expect(velocityColor(3.5)).toBe(RED);
    expect(velocityColor(5)).toBe(RED);
  });
  it("ignores the flow sign (flows may run against from->to)", () => {
    expect(velocityColor(-1.5)).toBe(velocityColor(1.5));
  });
});

describe("unknown-grey principle", () => {
  it("returns the dedicated UNOBSERVED grey for null on every scale", () => {
    expect(pressureColor(null)).toBe(UNOBSERVED);
    expect(velocityColor(null)).toBe(UNOBSERVED);
  });

  it("no healthy ramp value can produce the UNOBSERVED grey", () => {
    for (let i = 0; i <= 120; i++) {
      expect(pressureColor(i / 10)).not.toBe(UNOBSERVED); // 0..12 bar sweep
      expect(velocityColor(i / 25)).not.toBe(UNOBSERVED);
    }
  });
});

describe("size scales", () => {
  it("mdotWidth grows monotonically and saturates at the snapshot max", () => {
    expect(mdotWidth(0, 2)).toBeCloseTo(1.5);
    expect(mdotWidth(1, 2)).toBeLessThan(mdotWidth(2, 2));
    expect(mdotWidth(4, 2)).toBe(mdotWidth(2, 2)); // capped
    expect(mdotWidth(1, 0)).toBe(1.5); // degenerate snapshot
  });
  it("consumerRadius is bounded for tiny and big consumers", () => {
    expect(consumerRadius(0.05)).toBeGreaterThanOrEqual(4);
    expect(consumerRadius(5)).toBeLessThanOrEqual(14);
    expect(consumerRadius(2)).toBeGreaterThan(consumerRadius(0.1));
    expect(consumerRadius(null)).toBe(5); // no design value -> default
  });
});
