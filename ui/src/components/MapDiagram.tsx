import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type {
  ConsumerMeasurement,
  ConsumerState,
  JunctionState,
  MeasurementsResponse,
  PipeState,
  StepResult,
  Topology,
} from "../types";
import type { MapLayer } from "../App";
import type { MenuTarget } from "./ElementMenu";
import {
  M3H_PER_KG_S,
  P_HIGH_BAR,
  P_MIN_BAR,
  PRESSURE_GRADIENT,
  UNOBSERVED,
  UNOBSERVED_DASH,
  UNOBSERVED_LINE,
  V_MAX,
  VELOCITY_GRADIENT,
  consumerRadius,
  fmt,
  mdotWidth,
  pressureColor,
  velocityColor,
} from "../scales";

interface Props {
  topo: Topology;
  latest: StepResult | null;
  layer: MapLayer;
  /** the map-corner switch mirrors the Ansicht menu (shared lifted state) */
  onLayer: (layer: MapLayer) => void;
  /** measured view: color only sensored elements, grey/dash the rest */
  observedOnly: boolean;
  /** sensor placement: 📟 water-meter / 🌡️ pressure-sensor map markers */
  placement: MeasurementsResponse | null;
  /** right-click context menu on elements/nodes */
  onMenu?: (target: MenuTarget) => void;
  /** Ctrl-click pins an element details section */
  onPin?: (target: MenuTarget) => void;
}

const LAYERS: MapLayer[] = ["pressure", "velocity"];

const PLANT_COLOR = "#f2ae00"; // amber source marker (blueprint convention)

const TILES = {
  light: {
    url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
    bg: "#e9eaec",
    stroke: "#3a3a3a",
  },
  dark: {
    url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
    bg: "#0b0d11",
    stroke: "#0b0d11",
  },
};

/** Live drinking-water net on OSM/CARTO tiles: ONE polyline per pipe,
 *  consumers as circle markers sized by design demand, the head source as
 *  an amber station marker. All vector layers are created once and restyled
 *  in place per WS frame (`setStyle` only — never rebuilt). The unknown is
 *  styled as unknown: without a frame, or for unsensored elements in the
 *  measured view, elements render in the dedicated UNOBSERVED grey/dash —
 *  never in a healthy ramp color. */
export default function MapDiagram({
  topo, latest, layer, onLayer, observedOnly, placement, onMenu, onPin,
}: Props) {
  const { t, i18n } = useTranslation();
  const elRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const tileRef = useRef<L.TileLayer | null>(null);
  const trenchRef = useRef<Map<number, L.Polyline>>(new Map());
  const consRef = useRef<Map<number, L.CircleMarker>>(new Map());
  const nodeRef = useRef<Map<string, L.CircleMarker>>(new Map());
  const sensorRef = useRef<Map<string, L.Marker>>(new Map());
  const arrowRef = useRef<Map<number, L.Marker>>(new Map());
  const bearingRef = useRef<Map<number, number>>(new Map());
  const prvRef = useRef<Map<number, L.CircleMarker>>(new Map());
  const stationRef = useRef<Map<number, L.CircleMarker>>(new Map());
  const tankRef = useRef<Map<number, L.CircleMarker>>(new Map());
  const alarmRef = useRef<Map<string, L.CircleMarker>>(new Map());
  const emitterRef = useRef<Map<string, L.Marker>>(new Map());
  const plantRef = useRef<L.CircleMarker | null>(null);
  const [light, setLight] = useState(true);

  // popup content readers pull the CURRENT frame/layer state through refs,
  // so a popup opened once keeps updating while frames stream in
  const liveRef = useRef<{
    latest: StepResult | null;
    observedOnly: boolean;
  }>({ latest: null, observedOnly: false });
  liveRef.current = { latest, observedOnly };
  const cbRef = useRef<{ onMenu?: Props["onMenu"]; onPin?: Props["onPin"] }>({});
  cbRef.current = { onMenu, onPin };

  // right-click → ElementMenu; Ctrl-click → pinned section
  const wireInteractions = (
    lyr: L.Layer, target: Omit<MenuTarget, "x" | "y">,
  ) => {
    lyr.on("contextmenu", (e: L.LeafletMouseEvent) => {
      L.DomEvent.stop(e);
      const oe = e.originalEvent;
      cbRef.current.onMenu?.({ ...target, x: oe.clientX, y: oe.clientY });
    });
    lyr.on("click", (e: L.LeafletMouseEvent) => {
      if (!e.originalEvent.ctrlKey) return; // plain click keeps the popup
      L.DomEvent.stop(e);
      (lyr as L.Marker).closePopup?.();
      const oe = e.originalEvent;
      cbRef.current.onPin?.({ ...target, x: oe.clientX, y: oe.clientY });
    });
  };

  // ---- live-data lookups ----------------------------------------------------

  const pipeLive = (id: number): PipeState | undefined => {
    const { latest: f, observedOnly: obs } = liveRef.current;
    if (!f || obs) return undefined; // pipes carry no meter
    return (f.pipes ?? []).find((p) => p.trench === id);
  };

  /** Junction pressure by node name: truth view reads the full junction
   *  table; measured view reads only placed node sensors. */
  const nodePressure = (name: string): number | null | undefined => {
    const { latest: f, observedOnly: obs } = liveRef.current;
    if (!f) return undefined;
    if (obs) {
      const n = f.measurements?.nodes?.find((x) => x.node === name);
      return n ? n.p_bar : undefined;
    }
    const j = (f.junctions ?? []).find((x: JunctionState) => x.name === name);
    return j ? j.p_bar : undefined;
  };

  const consumerLive = (id: number): ConsumerState | ConsumerMeasurement | undefined => {
    const { latest: f, observedOnly: obs } = liveRef.current;
    if (!f) return undefined;
    const src = obs ? f.measurements?.consumers : f.consumers;
    return src?.find((c) => c.id === id);
  };

  // ---- popup HTML (built lazily at open + refreshed per frame) --------------

  const esc = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
  const row = (k: string, v: string) =>
    `<span style="color:var(--muted)">${k}</span> ${v}`;

  const trenchPopup = (trench: Topology["trenches"][number]): string => {
    const { latest: f, observedOnly: obs } = liveRef.current;
    // plastics are d-series (outer diameter), metallic pipes DN — never
    // label a PE bore (96.8 mm) as "DN 97"
    const sizing = trench.material && trench.dn
      ? (trench.material === "PE" || trench.material === "PVC"
        ? `${trench.material} d${trench.dn}`
        : `${trench.material} DN ${trench.dn}`)
      : `DN ${fmt(trench.inner_diameter_mm, 0)}`;
    const head = `<b>${esc(t("pop.trench", { from: trench.from_node, to: trench.to_node }))}</b>`
      + `<br><span style="color:var(--muted)">${sizing}`
      + ` · ${t("pop.length")} ${fmt(trench.length_km * 1000, 0)} m`
      + ` · k ${fmt(trench.k_mm, 2)} mm</span>`;
    if (!f) return `${head}<br>${t("pop.noData")}`;
    if (obs) return `${head}<br>${t("pop.unobserved")}`;
    const p = pipeLive(trench.id);
    if (!p) return `${head}<br>${t("pop.noData")}`;
    return `${head}<br>${row(t("pop.mdot"), `${fmt(Math.abs(p.mdot_kg_per_s ?? NaN), 3)} kg/s`)} · `
      + row(t("pop.velocity"), `${fmt(Math.abs(p.v_m_per_s ?? NaN), 3)} m/s`)
      + `<br>${row("Δp", `${fmt(p.dp_bar, 3)} bar`)}`;
  };

  const consumerPopup = (cons: Topology["consumers"][number]): string => {
    const { latest: f, observedOnly: obs } = liveRef.current;
    const head = `<b>${esc(t("tip.consumer", { name: cons.name }))}</b>`
      + `<br><span style="color:var(--muted)">${t("pop.designDemand")} ${fmt((cons.mdot_demand_kg_per_s ?? 0) * M3H_PER_KG_S, 2)} m³/h</span>`;
    if (!f) return `${head}<br>${t("pop.noData")}`;
    const c = consumerLive(cons.id);
    if (!c) return `${head}<br>${t("pop.unobserved")}`;
    // a standard-fidelity meter is honest about its raster — cold start
    // until the first 15-min window closes, windowed means afterwards
    const std = obs && f.measurements?.mode === "standard";
    if (std && c.mdot_kg_per_s == null) return `${head}<br>📟 ${t("pop.coldStart")}`;
    const demanded = "mdot_demand_kg_per_s" in c
      ? `${row(t("pop.demanded"), `${fmt(c.mdot_demand_kg_per_s, 3)} kg/s`)} · ` : "";
    return `${head}<br>${demanded}`
      + row(t("pop.delivered"), `${fmt(c.mdot_kg_per_s, 3)} kg/s`)
      + `<br>${row(t("pop.pressure"), `${fmt(c.p_bar, 2)} bar`)}`
      + (std ? `<br><span style="color:var(--muted)">📟 ${t("pop.windowed")}</span>` : "");
  };

  const plantPopup = (): string => {
    const { latest: f } = liveRef.current;
    const plant = topo.producers.find((p) => p.kind === "slack");
    const head = `<b>${esc(t("tip.plant", { name: plant?.name ?? "?" }))}</b>`;
    const live = f?.producers.find((p) => p.kind === "slack");
    if (!live) return `${head}<br>${t("pop.noData")}`;
    return `${head}<br>${row(t("pop.pressure"), `${fmt(live.p_bar, 2)} bar`)}`
      + `<br>${row(t("pop.feed"), `${fmt(live.mdot_kg_per_s, 3)} kg/s`)}`;
  };

  const prvPopup = (pid: number, name: string): string => {
    const { latest: f } = liveRef.current;
    const head = `<b>${esc(t("tip.prv", { name }))}</b>`;
    const live = f?.producers.find((p) => p.kind === "prv" && p.id === pid);
    if (!live) return `${head}<br>${t("pop.noData")}`;
    const abnormal = live.reducing === false
      ? `<br><span style="color:#ef4444">⚠ ${t("pop.prvAbnormal")}</span>`
      : "";
    return `${head}<br>${row(t("pop.prvIn"), `${fmt(live.p_in_bar, 2)} bar`)} → `
      + row(t("pop.prvOut"), `${fmt(live.p_out_bar, 2)} bar`)
      + ` <span style="color:var(--muted)">(${t("pop.prvSet")} ${fmt(live.p_set_bar, 2)})</span>`
      + `<br>${row(t("pop.mdot"), `${fmt(live.mdot_kg_per_s, 3)} kg/s`)}`
      + abnormal;
  };

  const stationPopup = (pid: number, name: string): string => {
    const { latest: f } = liveRef.current;
    const head = `<b>${esc(t("tip.station", { name }))}</b>`;
    const live = f?.producers.find(
      (p) => p.kind === "station" && p.id === pid);
    if (!live) return `${head}<br>${t("pop.noData")}`;
    const state = live.running
      ? `<span style="color:#3fb950">● ${t("tank.running")}</span>`
      : `<span style="color:var(--muted)">○ ${t("tank.stopped")}</span>`;
    const cv = live.cv_closed
      ? `<br><span style="color:#ef4444">⚠ ${t("tank.cvClosed")}</span>` : "";
    const body = live.running && live.p_in_bar != null
      ? `<br>${row(t("pop.prvIn"), `${fmt(live.p_in_bar, 2)} bar`)} → `
        + row(t("pop.prvOut"), `${fmt(live.p_out_bar, 2)} bar`)
        + `<br>${row(t("pop.mdot"), `${fmt((live.mdot_kg_per_s ?? 0) * M3H_PER_KG_S, 1)} m³/h`)}`
      : "";
    // controls.stations is fresh even on non-converged frames (the
    // producers list is a stale copy there — M2 review finding)
    const mode = f?.controls?.stations?.[name] ?? live.mode ?? "auto";
    return `${head}<br>${state} · ${t(`tank.mode${cap(mode)}`)}`
      + body + cv;
  };

  const tankPopup = (node: string, name: string): string => {
    const { latest: f } = liveRef.current;
    const head = `<b>${esc(t("tip.tank", { name }))}</b>`;
    const tk = f?.tanks?.find((x) => x.node === node);
    if (!tk) return `${head}<br>${t("pop.noData")}`;
    const flow = (tk.mdot_kg_per_s ?? 0) > 0.001 ? `▲ ${t("tank.inflow")}`
      : (tk.mdot_kg_per_s ?? 0) < -0.001 ? `▼ ${t("tank.outflow")}`
      : t("tank.balanced");
    const alarms = [
      tk.overflow ? `⚠ ${t("tank.overflow")}` : "",
      tk.empty ? `⚠ ${t("tank.empty")}` : "",
      !tk.empty && tk.fire_reserve_breached
        ? `⚠ ${t("tank.fireReserve", { m3: fmt(tk.fire_reserve_m3, 0) })}` : "",
    ].filter(Boolean).map((a) => `<br><span style="color:#ef4444">${a}</span>`)
      .join("");
    return `${head}<br><span style="color:var(--muted)">${t(`tank.${tk.kind}`)}</span>`
      + `<br>${row(t("tank.level"), `${fmt(tk.level_m, 2)} m`)} (${fmt(tk.level_min_m, 1)}–${fmt(tk.level_max_m, 1)})`
      + `<br>${row(t("tank.volume"), `${fmt(tk.volume_m3, 0)} / ${fmt(tk.capacity_m3, 0)} m³`)} · ${flow}`
      + (tk.buffer_time_h != null
        ? `<br>${row(t("tank.buffer"), `${fmt(tk.buffer_time_h, 1)} h`)}` : "")
      + alarms;
  };

  // ---- build map + static layers ONCE per topology (and language) -----------

  useEffect(() => {
    if (!elRef.current) return;
    const map = L.map(elRef.current, {
      preferCanvas: true,
      zoomSnap: 0.25,
      attributionControl: false,
    });
    mapRef.current = map;
    map.zoomControl.setPosition("topleft");
    const attribution = L.control.attribution(
      { prefix: false, position: "bottomleft" }).addAttribution(
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    );
    // geodata credit for OSM/DEM-built bundles (M8) — the data sources behind
    // this network's geometry + elevations, distinct from the tile credit
    for (const credit of topo.attribution ?? []) {
      attribution.addAttribution(
        credit.replace(/&/g, "&amp;").replace(/</g, "&lt;"));
    }
    attribution.addTo(map);

    const nodeGeo = new Map<string, [number, number]>();
    for (const n of topo.nodes) nodeGeo.set(n.name, n.geo);

    trenchRef.current.clear();
    const allPts: [number, number][] = [];
    for (const tr of topo.trenches) {
      const latlngs: [number, number][] = tr.geometry.length >= 2
        ? tr.geometry
        : ([nodeGeo.get(tr.from_node), nodeGeo.get(tr.to_node)]
            .filter((p): p is [number, number] => !!p));
      if (latlngs.length < 2) continue;
      allPts.push(...latlngs);
      const pl = L.polyline(latlngs, {
        color: UNOBSERVED_LINE, weight: 2, opacity: 0.95,
      }).addTo(map);
      // tooltips are innerHTML in Leaflet — esc() every user-controlled
      // name (imported bundles / POST /consumer names; M3 review: XSS)
      pl.bindTooltip(t("tip.trench", { from: esc(tr.from_node), to: esc(tr.to_node) }));
      pl.bindPopup(() => trenchPopup(tr), { autoPan: false });
      trenchRef.current.set(tr.id, pl);
    }

    // flow-direction arrows: one rotatable glyph per pipe midpoint,
    // created once, rotated/faded per frame (create-once/restyle)
    arrowRef.current.clear();
    bearingRef.current.clear();
    for (const tr of topo.trenches) {
      const pts: [number, number][] = tr.geometry.length >= 2
        ? tr.geometry
        : ([nodeGeo.get(tr.from_node), nodeGeo.get(tr.to_node)]
            .filter((p): p is [number, number] => !!p));
      if (pts.length < 2) continue;
      const mid = pts[Math.floor((pts.length - 1) / 2)];
      const nxt = pts[Math.floor((pts.length - 1) / 2) + 1];
      const midPt: [number, number] = [(mid[0] + nxt[0]) / 2,
                                       (mid[1] + nxt[1]) / 2];
      // screen rotation for the "➤" glyph (points east at 0°): CSS rotate
      // is clockwise, bearing is from north
      const dLat = nxt[0] - mid[0];
      const dLon = (nxt[1] - mid[1]) * Math.cos((mid[0] * Math.PI) / 180);
      const bearing = (Math.atan2(dLon, dLat) * 180) / Math.PI;
      bearingRef.current.set(tr.id, bearing - 90);
      const arrow = L.marker(midPt, {
        icon: L.divIcon({
          className: "flow-arrow",
          html: '<span style="display:none">➤</span>',
          iconSize: [14, 14], iconAnchor: [7, 7],
        }),
        interactive: false, keyboard: false,
      }).addTo(map);
      arrowRef.current.set(tr.id, arrow);
    }

    // PRV stations (Druckminderer): violet diamond at the outlet node
    prvRef.current.clear();
    for (const v of topo.prvs ?? []) {
      const pos = nodeGeo.get(v.to_node);
      if (!pos) continue;
      const pm = L.circleMarker(pos, {
        radius: 6, color: "#4c1d95", weight: 1.5,
        fillColor: "#8b5cf6", fillOpacity: 1,
      }).addTo(map);
      pm.bindTooltip(t("tip.prv", { name: esc(v.name) }));
      pm.bindPopup(() => prvPopup(v.id, v.name), { autoPan: false });
      prvRef.current.set(v.id, pm);
    }

    // pump stations (Pumpwerke): teal marker at the branch midpoint,
    // restyled per frame (green running / grey stopped / red CV alarm)
    stationRef.current.clear();
    for (const s of topo.stations ?? []) {
      const a = nodeGeo.get(s.from_node);
      const b = nodeGeo.get(s.to_node);
      if (!a || !b) continue;
      const pos: [number, number] = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      const sm = L.circleMarker(pos, {
        radius: 7, color: "#0e7490", weight: 1.5,
        fillColor: "#67e8f9", fillOpacity: 1,
      }).addTo(map);
      sm.bindTooltip(t("tip.station", { name: esc(s.name) }));
      sm.bindPopup(() => stationPopup(s.id, s.name), { autoPan: false });
      stationRef.current.set(s.id, sm);
      L.marker(pos, {
        icon: L.divIcon({ className: "plant-icon", html: "⚙️",
                          iconAnchor: [-6, 18] }),
        interactive: false, keyboard: false,
      }).addTo(map);
    }

    // tanks (Hochbehälter / Wassertürme): blue marker at their node —
    // level/volume/alarms live in the popup + the sidebar tank widget
    tankRef.current.clear();
    for (const p of topo.producers.filter((x) => x.kind === "tank")) {
      const pos = nodeGeo.get(p.node);
      if (!pos) continue;
      allPts.push(pos);
      const tm = L.circleMarker(pos, {
        radius: 8, color: "#1e40af", weight: 1.5,
        fillColor: "#60a5fa", fillOpacity: 1,
      }).addTo(map);
      tm.bindTooltip(t("tip.tank", { name: esc(p.name) }));
      tm.bindPopup(() => tankPopup(p.node, p.name), { autoPan: false });
      tankRef.current.set(p.id, tm);
      L.marker(pos, {
        icon: L.divIcon({ className: "plant-icon", html: "🗼",
                          iconAnchor: [-6, 18] }),
        interactive: false, keyboard: false,
      }).addTo(map);
    }

    // plain nodes (small, always visible): restyled by the pressure layer;
    // right-click opens the element menu (sensor placement)
    nodeRef.current.clear();
    for (const n of topo.nodes) {
      if (n.kind === "consumer" || n.kind === "source") continue;
      const nm = L.circleMarker(n.geo, {
        radius: 3.5, color: "#5b6472", weight: 1,
        fillColor: "#39424f", fillOpacity: 0.9,
      }).addTo(map);
      nm.bindTooltip(t("tip.node", { name: esc(n.name), elev: fmt(n.elevation_m, 0) }));
      wireInteractions(nm, {
        kind: "node", id: n.name, name: n.name, node: n.name,
      });
      nodeRef.current.set(n.name, nm);
    }

    consRef.current.clear();
    for (const c of topo.consumers) {
      const p = nodeGeo.get(c.node);
      if (!p) continue;
      allPts.push(p);
      const cm = L.circleMarker(p, {
        radius: consumerRadius(c.mdot_demand_kg_per_s),
        color: TILES[light ? "light" : "dark"].stroke,
        weight: 1,
        fillColor: UNOBSERVED,
        fillOpacity: 0.9,
      }).addTo(map);
      cm.bindTooltip(t("tip.consumer", { name: esc(c.name) }));
      cm.bindPopup(() => consumerPopup(c), { autoPan: false });
      wireInteractions(cm, {
        kind: "consumer", id: c.id, name: c.name, node: c.node,
        consumerKind: "consumer",
      });
      consRef.current.set(c.id, cm);
    }

    plantRef.current = null;
    const plant = topo.producers.find((p) => p.kind === "slack");
    const plantPos = plant ? nodeGeo.get(plant.node) : undefined;
    if (plant && plantPos) {
      allPts.push(plantPos);
      const cm = L.circleMarker(plantPos, {
        radius: 8, color: "#7a5400", weight: 1.5,
        fillColor: PLANT_COLOR, fillOpacity: 1,
      }).addTo(map);
      cm.bindTooltip(t("tip.plant", { name: esc(plant.name) }));
      cm.bindPopup(() => plantPopup(), { autoPan: false });
      wireInteractions(cm, {
        kind: "producer", id: plant.id, name: plant.name, node: plant.node,
        producerKind: "slack",
      });
      plantRef.current = cm;
      // decorative source glyph (never intercepts clicks)
      L.marker(plantPos, {
        icon: L.divIcon({ className: "plant-icon", html: "🏔️", iconAnchor: [-6, 18] }),
        interactive: false, keyboard: false,
      }).addTo(map);
    }

    const fit = () => {
      if (allPts.length) map.fitBounds(L.latLngBounds(allPts).pad(0.08));
    };
    fit();
    // re-measure + re-fit once layout has settled: when the map mounts in
    // the same commit that lays out the grid, the container can still be
    // 0-sized at construction and fitBounds lands at world zoom
    const timer = setTimeout(() => {
      map.invalidateSize();
      fit();
    }, 80);

    return () => {
      clearTimeout(timer);
      map.remove();
      mapRef.current = null;
      tileRef.current = null;
      sensorRef.current.clear();
      arrowRef.current.clear();
      bearingRef.current.clear();
      prvRef.current.clear();
      stationRef.current.clear();
      tankRef.current.clear();
      alarmRef.current.clear();
      emitterRef.current.clear();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topo, i18n.language]); // rebuild (incl. tooltips) on language change

  // ---- sensor markers: 📟 water meter at metered consumers, 🌡️ pressure
  // sensor at sensored nodes — diffed against the placement, decorative
  // (never intercept the element's own clicks) ----

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const nodeGeo = new Map<string, [number, number]>(
      topo.nodes.map((n) => [n.name, n.geo]));
    const consNode = new Map<number, string>(
      topo.consumers.map((c) => [c.id, c.node]));
    const want = new Map<string, { emoji: string; pos: [number, number] }>();
    for (const m of placement?.consumer_meters ?? []) {
      const pos = nodeGeo.get(m.node ?? consNode.get(m.id) ?? "");
      if (pos) want.set(`m${m.id}`, { emoji: "📟", pos });
    }
    for (const node of placement?.node_sensors ?? []) {
      const pos = nodeGeo.get(node);
      if (pos) want.set(`t${node}`, { emoji: "🌡️", pos });
    }
    for (const [key, mk] of sensorRef.current) {
      if (!want.has(key)) {
        map.removeLayer(mk);
        sensorRef.current.delete(key);
      }
    }
    for (const [key, w] of want) {
      if (sensorRef.current.has(key)) continue;
      const mk = L.marker(w.pos, {
        icon: L.divIcon({ className: "equip-icon", html: w.emoji,
                          iconAnchor: [-8, -4] }),
        interactive: false, keyboard: false,
      }).addTo(map);
      sensorRef.current.set(key, mk);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [placement, topo]);

  // ---- basemap (light/dark) --------------------------------------------------

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const theme = light ? TILES.light : TILES.dark;
    if (tileRef.current) map.removeLayer(tileRef.current);
    tileRef.current = L.tileLayer(theme.url, { maxZoom: 20 }).addTo(map);
    tileRef.current.bringToBack();
    map.getContainer().style.background = theme.bg;
    for (const cm of consRef.current.values()) cm.setStyle({ color: theme.stroke });
  }, [light, topo]);

  // ---- per-frame restyle: `.setStyle()` only, layers are never rebuilt -------

  useEffect(() => {
    if (!mapRef.current) return;
    const f = latest;
    const pipeData = new Map<number, PipeState>();
    let maxMdot = 0;
    if (f && !observedOnly) {
      for (const p of f.pipes ?? []) {
        pipeData.set(p.trench, p);
        maxMdot = Math.max(maxMdot, Math.abs(p.mdot_kg_per_s ?? 0));
      }
    }

    const trenchByld = new Map<number, Topology["trenches"][number]>(
      topo.trenches.map((tr) => [tr.id, tr]));

    for (const [id, pl] of trenchRef.current) {
      const p = pipeData.get(id);
      const tr = trenchByld.get(id);
      // pressure layer: color the pipe by the mean of its endpoint node
      // pressures (readable even in the measured view IF both ends carry
      // sensors); velocity layer: pipe property, truth view only
      let color: string | null = null;
      let known = false;
      if (layer === "pressure" && tr && f) {
        const pa = nodePressure(tr.from_node);
        const pb = nodePressure(tr.to_node);
        if (pa != null && pb != null) {
          color = pressureColor((pa + pb) / 2);
          known = true;
        }
      } else if (layer === "velocity" && p) {
        color = velocityColor(p.v_m_per_s);
        known = true;
      }
      if (!known || color == null) {
        pl.setStyle({ color: UNOBSERVED_LINE, weight: 2, opacity: 0.9,
                      dashArray: UNOBSERVED_DASH });
      } else {
        const weight = p
          ? mdotWidth(Math.abs(p.mdot_kg_per_s ?? 0), maxMdot) : 2.5;
        pl.setStyle({ color, weight, opacity: 0.95, dashArray: undefined });
      }
      if (pl.isPopupOpen() && tr) pl.setPopupContent(trenchPopup(tr));
    }

    // plain nodes: colored by junction pressure on the pressure layer
    for (const [name, nm] of nodeRef.current) {
      if (layer === "pressure" && f) {
        const p = nodePressure(name);
        if (p != null) {
          nm.setStyle({ fillColor: pressureColor(p), radius: 4.5,
                        fillOpacity: 0.95 });
          continue;
        }
      }
      nm.setStyle({ fillColor: "#39424f", radius: 3.5, fillOpacity: 0.9 });
    }

    for (const [id, cm] of consRef.current) {
      const c = f ? consumerLive(id) : undefined;
      if (!c) {
        cm.setStyle({ fillColor: UNOBSERVED, fillOpacity: 0.7,
                      dashArray: undefined });
      } else {
        // staleness hint: a standard-fidelity meter inside its first
        // 15-min window has no values yet — dashed ring, muted fill
        const stale = observedOnly
          && f?.measurements?.mode === "standard" && c.mdot_kg_per_s == null;
        const fill = layer === "pressure" ? pressureColor(c.p_bar)
          : "#94a3b8"; // velocity is a pipe property — consumers stay neutral
        cm.setStyle({ fillColor: fill, fillOpacity: stale ? 0.75 : 0.95,
                      dashArray: stale ? "3 3" : undefined });
      }
      if (cm.isPopupOpen()) {
        const cons = topo.consumers.find((x) => x.id === id);
        if (cons) cm.setPopupContent(consumerPopup(cons));
      }
    }

    // flow-direction arrows: rotate with the flow sign, hide when unknown
    for (const [id, arrow] of arrowRef.current) {
      const el = arrow.getElement()?.firstElementChild as HTMLElement | null;
      if (!el) continue;
      const p = pipeData.get(id);
      const base = bearingRef.current.get(id) ?? 0;
      if (!p || p.mdot_kg_per_s == null
          || Math.abs(p.mdot_kg_per_s) < 1e-6) {
        el.style.display = "none";
      } else {
        el.style.display = "inline-block";
        const flip = p.mdot_kg_per_s < 0 ? 180 : 0;
        el.style.transform = `rotate(${base + flip}deg)`;
      }
    }

    // PRV popups refresh while open
    const prvsById = new Map((topo.prvs ?? []).map((v) => [v.id, v]));
    for (const [id, pm] of prvRef.current) {
      if (pm.isPopupOpen()) {
        const v = prvsById.get(id);
        if (v) pm.setPopupContent(prvPopup(v.id, v.name));
      }
    }

    // station markers: green while pumping, grey stopped, red CV alarm
    const stById = new Map((topo.stations ?? []).map((s) => [s.id, s]));
    for (const [id, sm] of stationRef.current) {
      const live = f?.producers.find(
        (p) => p.kind === "station" && p.id === id);
      if (live?.cv_closed) {
        sm.setStyle({ fillColor: "#f87171", color: "#b91c1c" });
      } else if (live?.running) {
        sm.setStyle({ fillColor: "#4ade80", color: "#166534" });
      } else {
        sm.setStyle({ fillColor: "#94a3b8", color: "#475569" });
      }
      if (sm.isPopupOpen()) {
        const s = stById.get(id);
        if (s) sm.setPopupContent(stationPopup(s.id, s.name));
      }
    }

    // tank markers: alarm ring on overflow/empty/fire-reserve breach
    const tanksByPid = new Map(
      topo.producers.filter((x) => x.kind === "tank").map((p) => [p.id, p]));
    for (const [id, tm] of tankRef.current) {
      const meta = tanksByPid.get(id);
      const tk = meta ? f?.tanks?.find((x) => x.node === meta.node) : undefined;
      const alarm = tk && (tk.overflow || tk.empty || tk.fire_reserve_breached);
      tm.setStyle(alarm
        ? { fillColor: "#f87171", color: "#b91c1c" }
        : { fillColor: "#60a5fa", color: "#1e40af" });
      if (tm.isPopupOpen() && meta) {
        tm.setPopupContent(tankPopup(meta.node, meta.name));
      }
    }

    if (plantRef.current?.isPopupOpen()) {
      plantRef.current.setPopupContent(plantPopup());
    }

    // M5 emitter markers (hydrant 🚒 / burst 💥 — leaks stay off the map to
    // avoid clutter): diffed decorative glyphs at the emitter node
    if (mapRef.current) {
      const emMap = mapRef.current;
      const nodeGeoE = new Map(topo.nodes.map((n) => [n.name, n.geo]));
      const wantEm = new Map<string, { pos: [number, number]; icon: string; title: string }>();
      for (const em of f?.emitters ?? []) {
        if (em.kind === "leak") continue;
        const pos = nodeGeoE.get(em.node);
        if (!pos) continue;
        wantEm.set(em.name, {
          // esc() the user-controlled name — the tooltip is innerHTML in
          // Leaflet (stored XSS via imported/POSTed names — M5 review)
          pos, icon: em.kind === "hydrant" ? "🚒" : "💥",
          title: `${esc(em.name)}: ${fmt(em.m3_per_h, 1)} m³/h`,
        });
      }
      for (const [key, mk] of emitterRef.current) {
        if (!wantEm.has(key)) { emMap.removeLayer(mk); emitterRef.current.delete(key); }
      }
      for (const [key, w] of wantEm) {
        const existing = emitterRef.current.get(key);
        if (existing) {
          existing.setTooltipContent(w.title);
        } else {
          const mk = L.marker(w.pos, {
            icon: L.divIcon({ className: "emitter-icon", html: w.icon,
                              iconAnchor: [8, 8] }),
            interactive: true, keyboard: false,
          }).addTo(emMap);
          mk.bindTooltip(w.title);
          emitterRef.current.set(key, mk);
        }
      }
    }

    // M4 traffic-light overlay: alarm halos on affected entities, diffed
    // per frame (violation red / warning amber ring, decorative). In the
    // measured view findings are truth-derived — no halos (M4 review).
    const map = mapRef.current;
    if (map) {
      const nodeGeo = new Map(topo.nodes.map((n) => [n.name, n.geo]));
      const consNode = new Map(topo.consumers.map((c) => [c.name, c.node]));
      const tankNode = new Map(topo.producers
        .filter((p) => p.kind === "tank").map((p) => [p.name, p.node]));
      const want = new Map<string, { pos: [number, number]; color: string }>();
      for (const fd of (observedOnly ? [] : f?.findings ?? [])) {
        if (fd.severity === "info") continue;
        const color = fd.severity === "violation" ? "#ef4444" : "#f2ae00";
        let pos: [number, number] | undefined;
        if (fd.entity_kind === "consumer") {
          pos = nodeGeo.get(consNode.get(fd.entity) ?? "");
        } else if (fd.entity_kind === "node") {
          pos = nodeGeo.get(fd.entity);
        } else if (fd.entity_kind === "tank") {
          pos = nodeGeo.get(tankNode.get(fd.entity) ?? "");
        } else if (fd.entity_kind === "pipe") {
          const pl = trenchRef.current.get(Number(fd.entity));
          const c = pl?.getCenter();
          if (c) pos = [c.lat, c.lng];
        }
        if (pos) {
          const key = `${fd.entity_kind}:${fd.entity}`;
          const prev = want.get(key);
          if (!prev || color === "#ef4444") want.set(key, { pos, color });
        }
      }
      for (const [key, mk] of alarmRef.current) {
        if (!want.has(key)) {
          map.removeLayer(mk);
          alarmRef.current.delete(key);
        }
      }
      for (const [key, w] of want) {
        const existing = alarmRef.current.get(key);
        if (existing) {
          existing.setStyle({ color: w.color });
        } else {
          alarmRef.current.set(key, L.circleMarker(w.pos, {
            radius: 12, color: w.color, weight: 2.5, fill: false,
            opacity: 0.9, interactive: false,
          }).addTo(map));
        }
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latest, layer, observedOnly, topo, i18n.language]);
  // i18n.language: the language toggle rebuilds every layer (tooltips) —
  // without re-running the restyle pass the fresh layers would sit
  // unstyled until the next WS frame (blank while paused — M4 review)

  // ---- colorbar legend per layer ----------------------------------------------

  const legend = layer === "pressure" ? {
    gradient: PRESSURE_GRADIENT,
    top: `${fmt(P_HIGH_BAR + 2, 0)} bar`,
    bottom: "0 bar",
    caption: t("map.cbPressure", { min: P_MIN_BAR }),
  } : {
    gradient: VELOCITY_GRADIENT,
    top: `${V_MAX} m/s`,
    bottom: "0",
    caption: t("map.cbVelocity"),
  };

  return (
    <div className="map-wrap">
      <div ref={elRef} className="map-canvas" />
      <button className="map-basemap" onClick={() => setLight((v) => !v)}>
        {light ? t("map.dark") : t("map.light")}
      </button>
      <div className="map-layers">
        {LAYERS.map((l) => (
          <button key={l} className={layer === l ? "on" : ""}
                  title={t(`layer.${l}Title`)} onClick={() => onLayer(l)}>
            {t(`layer.${l}`)}
          </button>
        ))}
      </div>
      <div className="map-colorbars">
        <Colorbar gradient={legend.gradient} top={legend.top}
                  bottom={legend.bottom} caption={legend.caption} />
      </div>
    </div>
  );
}

function Colorbar({ gradient, top, bottom, caption }: {
  gradient: string; top: string; bottom: string; caption: string;
}) {
  return (
    <div className="colorbar">
      <span className="cb-top">{top}</span>
      <div className="cb-ramp" style={{ background: gradient }} />
      <span className="cb-bot">{bottom}</span>
      <span className="cb-cap">{caption}</span>
    </div>
  );
}
