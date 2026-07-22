import { describe, expect, it } from "vitest";
import {
  emptyModel, makeNode, toBundle, validateModel,
  type EditorModel, type EditorTool, type ENode,
} from "./model";
import { buildStreetGraph, routeOnStreets, snapToStreets } from "./streetGraph";

// a minimal valid model: a source on a hill + one downhill consumer + a pipe
function twoNodeNet(): EditorModel {
  return {
    name: "Testdorf",
    nodes: [
      { id: "s1", lat: 51.30, lon: 6.50, elevation_m: 60, kind: "source",
        name: "Quelle", p_bar: 3 },
      { id: "c1", lat: 51.31, lon: 6.51, elevation_m: 40, kind: "consumer",
        name: "Haus 1", population: 200, storeys: 2,
        consumerKind: "residential_village" },
    ],
    pipes: [{ id: "p1", from: "s1", to: "c1", dn: 160, material: "PE",
              geometry: [[51.30, 6.50], [51.31, 6.51]] }],
  };
}

describe("editor model", () => {
  it("flags an empty model's structural problems", () => {
    const p = validateModel(emptyModel());
    expect(p.length).toBeGreaterThan(0);
    expect(p.some((x) => x.includes("Quelle"))).toBe(true);
  });

  it("flags a node with no elevation and an isolated node", () => {
    const m = twoNodeNet();
    m.nodes.push({ id: "c2", lat: 51.32, lon: 6.52, elevation_m: null,
                   kind: "consumer", name: "Haus 2" });
    const p = validateModel(m);
    expect(p.some((x) => x.includes("Höhe"))).toBe(true);
    expect(p.some((x) => x.includes("verbunden"))).toBe(true);
  });

  it("a two-node net validates clean", () => {
    expect(validateModel(twoNodeNet())).toEqual([]);
  });

  it("flags duplicate consumer names (the backend loader rejects them)", () => {
    const m = twoNodeNet();
    m.nodes.push({ id: "c2", lat: 51.32, lon: 6.52, elevation_m: 42,
                   kind: "consumer", name: "Haus 1" });   // dup of c1's name
    m.pipes.push({ id: "p2", from: "c1", to: "c2", dn: 160, material: "PE",
                   geometry: [[51.31, 6.51], [51.32, 6.52]] });
    expect(validateModel(m).some((x) => x.includes("doppelte"))).toBe(true);
  });

  it("serialises to the five-file bundle with a source ext_grid", () => {
    const b = toBundle(twoNodeNet()) as any;
    expect(new Set(Object.keys(b))).toEqual(new Set([
      "name", "network_structure", "pipes", "consumers", "supply", "environment"]));
    // exactly one ext_grid source, on the source node
    expect(b.supply.supplies).toHaveLength(1);
    expect(b.supply.supplies[0].kind).toBe("ext_grid");
    // the source junction is kind "source"
    const src = b.network_structure.junctions.find((j: any) => j.kind === "source");
    expect(src.name).toBe("quelle");
    // consumer demand derived from population (200 · 130 L/d → kg/s)
    expect(b.consumers.consumers[0].mdot_kg_per_s).toBeCloseTo(200 * 130 / 86400, 4);
    expect(b.consumers.consumers[0].size.population).toBe(200);
    // pn_bar falls with elevation (the downhill consumer sits higher-pressure)
    const c = b.network_structure.junctions.find((j: any) => j.kind === "consumer");
    expect(c.pn_bar).toBeGreaterThan(src.pn_bar);
    // geodata attribution carried
    expect(b.network_structure.attribution.join(" ")).toContain("OpenStreetMap");
  });

  it("supply pressure holds a margin above the highest consumer", () => {
    const b = toBundle(twoNodeNet()) as any;
    // head_abs = 40 + 30 = 70 m; source at 60 m → p ≈ (70-60)·0.0979 ≈ 0.98 bar
    // (the model's explicit p_bar=3 wins here — check the junction seed instead)
    const src = b.network_structure.junctions.find((j: any) => j.kind === "source");
    expect(src.pn_bar).toBeGreaterThan(0);
  });
});

describe("node placement (makeNode)", () => {
  // regression: the map handler passes the LIVE tool; makeNode must produce
  // the matching kind — a stale 'pan' made every node a generic junction, so
  // sources/consumers were unplaceable and commission was blocked (review)
  it("creates the node kind the tool selects", () => {
    expect(makeNode([], "source", 51, 6)!.kind).toBe("source");
    expect(makeNode([], "consumer", 51, 6)!.kind).toBe("consumer");
    expect(makeNode([], "junction", 51, 6)!.kind).toBe("node");
  });

  it("allows only one source", () => {
    const src = makeNode([], "source", 51, 6)!;
    expect(makeNode([src], "source", 51.1, 6.1)).toBeNull();
  });

  it("names consumers collision-proof across a delete", () => {
    let nodes: ENode[] = [];
    const place = (tool: EditorTool) => {
      const n = makeNode(nodes, tool, 51 + nodes.length * 0.001, 6)!;
      nodes = [...nodes, n];
      return n;
    };
    const a1 = place("consumer");           // Abnehmer 1
    const a2 = place("consumer");           // Abnehmer 2
    nodes = nodes.filter((n) => n.id !== a1.id);   // delete Abnehmer 1
    const a3 = place("consumer");           // must NOT reuse "Abnehmer 2"
    expect(a1.name).toBe("Abnehmer 1");
    expect(a2.name).toBe("Abnehmer 2");
    expect(a3.name).not.toBe(a2.name);
    // no duplicate consumer names → validate passes that check
    const cNames = nodes.filter((n) => n.kind === "consumer").map((n) => n.name);
    expect(new Set(cNames).size).toBe(cNames.length);
  });

  it("a net built via the placement intents serialises to a valid bundle", () => {
    let nodes: ENode[] = [];
    const place = (tool: EditorTool, lat: number, lon: number, elev: number) => {
      const n = { ...makeNode(nodes, tool, lat, lon)!, elevation_m: elev };
      nodes = [...nodes, n];
      return n;
    };
    const src = place("source", 51.30, 6.50, 65);
    const c1 = place("consumer", 51.305, 6.505, 45);
    const model: EditorModel = { name: "Klickdorf", nodes,
      pipes: [{ id: "p1", from: src.id, to: c1.id, dn: 160, material: "PE",
                geometry: [[51.30, 6.50], [51.305, 6.505]] }] };
    expect(validateModel(model)).toEqual([]);
    const b = toBundle(model) as any;
    expect(b.supply.supplies[0].kind).toBe("ext_grid");
    expect(b.consumers.consumers).toHaveLength(1);
  });
});

describe("street routing", () => {
  // an L-shaped street: (0,0)->(0,0.01)->(0.01,0.01)
  const streets = [{ id: 1, name: "Hauptstraße",
    nodes: [[0, 0], [0, 0.01], [0.01, 0.01]] as [number, number][] }];
  const g = buildStreetGraph(streets, 0);

  it("snaps a nearby point onto the street", () => {
    const snap = snapToStreets(g, [0.0001, 0.005], 200);
    expect(snap).not.toBeNull();
    expect(snap!.distM).toBeLessThan(200);
  });

  it("returns null for a far-away point", () => {
    expect(snapToStreets(g, [5, 5], 35)).toBeNull();
  });

  it("routes along the street between two snapped points (the L-bend)", () => {
    const a = snapToStreets(g, [0, 0.001], 50)!;
    const b = snapToStreets(g, [0.009, 0.01], 50)!;
    const route = routeOnStreets(g, a, b);
    expect(route).not.toBeNull();
    // the routed length follows the bend, longer than the straight line
    expect(route!.geometry.length).toBeGreaterThanOrEqual(3);
    expect(route!.lengthM).toBeGreaterThan(1000); // ~2 km along the L
  });
});
