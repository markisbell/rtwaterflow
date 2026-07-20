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
  const row = (k: string, v: string) =>
    `<span style="color:var(--muted)">${k}</span> ${v}`;

  const trenchPopup = (trench: Topology["trenches"][number]): string => {
    const { latest: f, observedOnly: obs } = liveRef.current;
    const head = `<b>${esc(t("pop.trench", { from: trench.from_node, to: trench.to_node }))}</b>`
      + `<br><span style="color:var(--muted)">DN ${fmt(trench.inner_diameter_mm, 0)}`
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
      + `<br><span style="color:var(--muted)">${t("pop.designDemand")} ${fmt((cons.mdot_demand_kg_per_s ?? 0) * 3.6, 2)} m³/h</span>`;
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
    L.control.attribution({ prefix: false, position: "bottomleft" }).addAttribution(
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    ).addTo(map);

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
      pl.bindTooltip(t("tip.trench", { from: tr.from_node, to: tr.to_node }));
      pl.bindPopup(() => trenchPopup(tr), { autoPan: false });
      trenchRef.current.set(tr.id, pl);
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
      nm.bindTooltip(t("tip.node", { name: n.name, elev: fmt(n.elevation_m, 0) }));
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
      cm.bindTooltip(t("tip.consumer", { name: c.name }));
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
      cm.bindTooltip(t("tip.plant", { name: plant.name }));
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

    if (plantRef.current?.isPopupOpen()) {
      plantRef.current.setPopupContent(plantPopup());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latest, layer, observedOnly, topo]);

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
