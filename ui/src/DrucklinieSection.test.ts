import { describe, expect, it } from "vitest";
import { shortestPath } from "./components/DrucklinieSection";
import type { Topology } from "./types";

// A 5-node fixture with a PRV: source -> a -> [PRV] -> b -> leaf, plus an
// unreachable island node. Mirrors the Musterdorf zone-boundary pattern.
const topo: Topology = {
  id: "fixture",
  name: "fixture",
  nodes: [
    { name: "src", kind: "source", geo: [49, 9], elevation_m: 420, pn_bar: 1 },
    { name: "a", kind: "node", geo: [49, 9.001], elevation_m: 380, pn_bar: 1 },
    { name: "b", kind: "node", geo: [49, 9.002], elevation_m: 340, pn_bar: 1 },
    { name: "leaf", kind: "consumer", geo: [49, 9.003], elevation_m: 320, pn_bar: 1 },
    { name: "island", kind: "node", geo: [49, 9.01], elevation_m: 300, pn_bar: 1 },
  ],
  trenches: [
    { id: 0, from_node: "src", to_node: "a", length_km: 0.5, dn: 150,
      material: "GGG", inner_diameter_mm: 150, k_mm: 0.4, sections: 1,
      geometry: [[49, 9], [49, 9.001]], pipe: 0 },
    { id: 1, from_node: "b", to_node: "leaf", length_km: 0.3, dn: 110,
      material: "PE", inner_diameter_mm: 96.8, k_mm: 0.1, sections: 1,
      geometry: [[49, 9.002], [49, 9.003]], pipe: 1 },
  ],
  consumers: [
    { id: 0, name: "Leaf", node: "leaf", kind: "residential",
      mdot_demand_kg_per_s: 0.1 },
  ],
  producers: [{ id: 0, kind: "slack", name: "Tank", node: "src" }],
  prvs: [{ id: 1, name: "DM", from_node: "a", to_node: "b" }],
  steps_per_day: 1440,
  n_days: 1,
};

describe("shortestPath (Drucklinie)", () => {
  it("crosses the PRV pseudo-edge in order and accumulates distance", () => {
    const path = shortestPath(topo, "src", "leaf");
    expect(path).not.toBeNull();
    expect(path!.map((p) => p.node)).toEqual(["src", "a", "b", "leaf"]);
    // cumulative distance: 0.5 km pipe + ~0 PRV edge + 0.3 km pipe
    expect(path![path!.length - 1].dist_km).toBeCloseTo(0.8, 2);
    // the PRV pseudo-edge contributes (almost) nothing
    expect(path![2].dist_km - path![1].dist_km).toBeLessThan(0.001);
  });

  it("carries node elevations for the terrain profile", () => {
    const path = shortestPath(topo, "src", "leaf")!;
    expect(path.map((p) => p.elevation_m)).toEqual([420, 380, 340, 320]);
  });

  it("returns null for unreachable targets (renders the honest fallback)", () => {
    expect(shortestPath(topo, "src", "island")).toBeNull();
  });

  it("survives a topology without prvs", () => {
    const noPrv = { ...topo, prvs: [] };
    expect(shortestPath(noPrv, "src", "a")!.map((p) => p.node))
      .toEqual(["src", "a"]);
    expect(shortestPath(noPrv, "src", "leaf")).toBeNull(); // PRV was the link
  });
});
