/**
 * EditorMap (M8 stage 2b) — the interactive Leaflet surface of the NetzStudio
 * water editor. Renders the fetched OSM streets + the model (nodes + pipes) and
 * turns clicks into edits per the active tool:
 *  - source/junction/consumer: snap the click onto a street → place a node,
 *  - pipe: click two placed nodes → route a pipe along the streets between them,
 *  - delete: click a node (and its pipes) or a pipe to remove it.
 * All heavy state lives in the parent (NetzStudioEditor); this component only
 * draws + emits intents.
 */
import { useEffect, useRef } from "react";
import L from "leaflet";
import type { EditorModel, EditorTool, LatLon } from "./model";
import {
  buildStreetGraph, routeOnStreets, snapToStreets, type EditorStreet,
} from "./streetGraph";

const KIND_STYLE: Record<string, { color: string; icon: string }> = {
  source: { color: "#2563eb", icon: "◆" },
  consumer: { color: "#16a34a", icon: "▲" },
  node: { color: "#64748b", icon: "●" },
};

export default function EditorMap({
  streets, model, tool, dn, material, pendingFrom,
  onBboxChange, onPlaceNode, onDrawPipe, onDeleteNode, onDeletePipe,
  onPickPipeFrom,
}: {
  streets: EditorStreet[];
  model: EditorModel;
  tool: EditorTool;
  dn: number;
  material: string;
  pendingFrom: string | null;
  onBboxChange: (b: { s: number; w: number; n: number; e: number }) => void;
  onPlaceNode: (tool: EditorTool, lat: number, lon: number) => void;
  onDrawPipe: (from: string, to: string, geometry: LatLon[]) => void;
  onDeleteNode: (id: string) => void;
  onDeletePipe: (id: string) => void;
  onPickPipeFrom: (id: string | null) => void;
}) {
  const elRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const streetLayer = useRef<L.LayerGroup | null>(null);
  const modelLayer = useRef<L.LayerGroup | null>(null);
  const graphRef = useRef<ReturnType<typeof buildStreetGraph> | null>(null);
  // latest props for the map click handler (registered once)
  const cb = useRef({ tool, dn, material, pendingFrom, model });
  cb.current = { tool, dn, material, pendingFrom, model };

  // -- init the map once --
  useEffect(() => {
    if (!elRef.current || mapRef.current) return;
    const map = L.map(elRef.current, { preferCanvas: true, zoomSnap: 0.25,
                                       attributionControl: false })
      .setView([51.5767, 6.5129], 15);
    L.control.attribution({ prefix: false, position: "bottomleft" })
      .addAttribution('&copy; <a href="https://www.openstreetmap.org/copyright">'
        + "OpenStreetMap</a> &copy; CARTO").addTo(map);
    L.tileLayer(
      "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
      { maxZoom: 20 }).addTo(map);
    streetLayer.current = L.layerGroup().addTo(map);
    modelLayer.current = L.layerGroup().addTo(map);
    mapRef.current = map;

    const emitBbox = () => {
      const b = map.getBounds();
      onBboxChange({ s: b.getSouth(), w: b.getWest(),
                     n: b.getNorth(), e: b.getEast() });
    };
    map.on("moveend", emitBbox);
    emitBbox();

    map.on("click", (ev: L.LeafletMouseEvent) => {
      const { tool: t } = cb.current;
      if (t === "pan" || t === "pipe" || t === "delete") return;
      const g = graphRef.current;
      const ll: LatLon = [ev.latlng.lat, ev.latlng.lng];
      // snap onto a street when one is near; else drop where clicked
      const snap = g ? snapToStreets(g, ll, 40) : null;
      const p = snap ? snap.point : ll;
      // pass the LIVE tool (cb.current) — the handler is registered once, so a
      // captured `tool` would be stale (always the initial 'pan') (review)
      onPlaceNode(cb.current.tool, p[0], p[1]);
    });
    return () => { map.remove(); mapRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // -- rebuild the street layer + routing graph when streets change --
  useEffect(() => {
    const lg = streetLayer.current;
    if (!lg) return;
    lg.clearLayers();
    for (const st of streets) {
      L.polyline(st.nodes as [number, number][],
        { color: "#94a3b8", weight: 2, opacity: 0.7, interactive: false })
        .addTo(lg);
    }
    const center = mapRef.current?.getCenter();
    graphRef.current = streets.length
      ? buildStreetGraph(streets, center ? center.lat : 51.5) : null;
  }, [streets]);

  // -- rebuild the model layer whenever the model / tool / selection change --
  useEffect(() => {
    const lg = modelLayer.current;
    if (!lg) return;
    lg.clearLayers();
    const nodeById = new Map(model.nodes.map((n) => [n.id, n]));

    for (const p of model.pipes) {
      const line = L.polyline(p.geometry as [number, number][],
        { color: "#0ea5e9", weight: 4, opacity: 0.9 });
      line.on("click", (e) => {
        L.DomEvent.stop(e);
        if (cb.current.tool === "delete") onDeletePipe(p.id);
      });
      line.bindTooltip(`${p.material} d${p.dn}`);
      line.addTo(lg);
    }

    for (const n of model.nodes) {
      const style = KIND_STYLE[n.kind] ?? KIND_STYLE.node;
      const selected = pendingFrom === n.id;
      const marker = L.circleMarker([n.lat, n.lon], {
        radius: n.kind === "source" ? 9 : 7,
        color: selected ? "#f59e0b" : style.color,
        weight: selected ? 4 : 2, fillColor: style.color, fillOpacity: 0.85,
      });
      const hasElev = n.elevation_m != null;
      marker.bindTooltip(
        `${style.icon} ${n.name}${hasElev ? ` · ${n.elevation_m} m` : " · Höhe?"}`);
      marker.on("click", (e) => {
        L.DomEvent.stop(e);
        const t = cb.current.tool;
        if (t === "delete") { onDeleteNode(n.id); return; }
        if (t !== "pipe") return;
        const from = cb.current.pendingFrom;
        if (!from) { onPickPipeFrom(n.id); return; }
        if (from === n.id) { onPickPipeFrom(null); return; }
        const a = nodeById.get(from);
        if (!a) { onPickPipeFrom(n.id); return; }
        const g = graphRef.current;
        let geometry: LatLon[] = [[a.lat, a.lon], [n.lat, n.lon]];
        if (g) {
          const sa = snapToStreets(g, [a.lat, a.lon], 60);
          const sb = snapToStreets(g, [n.lat, n.lon], 60);
          if (sa && sb) {
            const r = routeOnStreets(g, sa, sb);
            if (r) geometry = [[a.lat, a.lon], ...r.geometry, [n.lat, n.lon]];
          }
        }
        onDrawPipe(from, n.id, geometry);
        onPickPipeFrom(null);
      });
      marker.addTo(lg);
    }
  }, [model, tool, pendingFrom, dn, material,
      onDeleteNode, onDeletePipe, onDrawPipe, onPickPipeFrom]);

  return <div ref={elRef} className="editor-map"
              style={{ width: "100%", height: "100%" }} />;
}
