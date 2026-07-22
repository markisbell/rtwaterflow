/**
 * NetzStudioEditor (M8 stage 2b) — the "how networks are built" editor. Draw a
 * water network on real OSM streets, verify it against the three DVGW W 400-1
 * load cases, and commission it into the catalog. Holds the editor model +
 * tool state; EditorMap is the drawing surface, the side panel the toolbox.
 */
import { useCallback, useRef, useState,
         type Dispatch, type SetStateAction } from "react";
import { api } from "../api";
import type { ApplyResponse, LoadCheckResult } from "../types";
import EditorMap from "./EditorMap";
import {
  DEFAULT_PIPE, PIPE_CATALOG, emptyModel, makeNode, nextId, sourceNode,
  toBundle, validateModel,
  type EditorModel, type EditorTool, type LatLon,
} from "./model";
import type { EditorStreet } from "./streetGraph";

type Bbox = { s: number; w: number; n: number; e: number };

const TOOLS: { id: EditorTool; label: string; hint: string }[] = [
  { id: "pan", label: "✋ Verschieben", hint: "Karte bewegen" },
  { id: "source", label: "◆ Einspeisung", hint: "Quelle auf den Hochpunkt" },
  { id: "junction", label: "● Knoten", hint: "Verzweigung/Knoten" },
  { id: "consumer", label: "▲ Abnehmer", hint: "Hausanschluss" },
  { id: "pipe", label: "／ Leitung", hint: "zwei Knoten verbinden" },
  { id: "delete", label: "✕ Löschen", hint: "Knoten/Leitung entfernen" },
];

export default function NetzStudioEditor({
  onApplied, model, setModel, streets, setStreets,
}: {
  onApplied: (r: ApplyResponse) => void;
  // model + streets are held by the parent so a drawn net survives the
  // Katalog↔Editor toggle (review); the rest is transient editor state
  model: EditorModel;
  setModel: Dispatch<SetStateAction<EditorModel>>;
  streets: EditorStreet[];
  setStreets: Dispatch<SetStateAction<EditorStreet[]>>;
}) {
  const [tool, setTool] = useState<EditorTool>("pan");
  const [pipeSel, setPipeSel] = useState(DEFAULT_PIPE);
  const [pendingFrom, setPendingFrom] = useState<string | null>(null);
  // always-current model for the (stable) placement callback — reading it here
  // avoids both a stale closure and side effects inside the setModel updater
  const modelRef = useRef(model);
  modelRef.current = model;
  const [bbox, setBbox] = useState<Bbox | null>(null);
  const [check, setCheck] = useState<LoadCheckResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const problems = validateModel(model);
  const hasSource = !!sourceNode(model);

  const loadStreets = useCallback(() => {
    if (!bbox) return;
    setBusy("streets"); setMsg(null);
    api.editorStreets(bbox.s, bbox.w, bbox.n, bbox.e)
      .then((r) => { setStreets(r.streets); setMsg(
        `${r.streets.length} Straßen geladen`); })
      .catch((e) => setMsg(String(e).slice(0, 140)))
      .finally(() => setBusy(null));
  }, [bbox]);

  // takes the LIVE tool from the (once-registered) map handler; STABLE (no
  // deps) so the captured instance is never stale. The DEM fetch is fired
  // AFTER the pure append updater, not inside it (StrictMode double-invokes
  // updaters, which would double-fire the request) — review.
  const placeNode = useCallback(
    (toolArg: EditorTool, lat: number, lon: number) => {
      // compute the node ONCE from the current model (ref), then append with a
      // pure updater — no nextId/fetch side effects inside setModel (review)
      const node = makeNode(modelRef.current.nodes, toolArg, lat, lon);
      if (!node) return;                     // source tool but one exists
      setModel((cur) => ({ ...cur, nodes: [...cur.nodes, node] }));
      // freeze the DEM elevation onto the placed node (fire-and-forget)
      api.editorElevation([[lat, lon]]).then((r) => {
        const el = r.elevations[0];
        if (el != null) setModel((cur) => ({ ...cur, nodes: cur.nodes.map(
          (x) => x.id === node.id ? { ...x, elevation_m: el } : x) }));
      }).catch(() => { /* leave elevation null → validation flags it */ });
    }, [setModel]);

  const drawPipe = useCallback((from: string, to: string, geometry: LatLon[]) => {
    setModel((m) => {
      if (m.pipes.some((p) => (p.from === from && p.to === to)
                           || (p.from === to && p.to === from))) return m;
      return { ...m, pipes: [...m.pipes, {
        id: nextId("p"), from, to, geometry,
        dn: pipeSel.dn, material: pipeSel.material }] };
    });
  }, [pipeSel]);

  const deleteNode = useCallback((id: string) => setModel((m) => ({
    ...m, nodes: m.nodes.filter((n) => n.id !== id),
    pipes: m.pipes.filter((p) => p.from !== id && p.to !== id) })), []);
  const deletePipe = useCallback((id: string) => setModel((m) => ({
    ...m, pipes: m.pipes.filter((p) => p.id !== id) })), []);

  const runCheck = () => {
    setBusy("check"); setMsg(null);
    try {
      const bundle = toBundle(model);
      api.editorLoadcheck(bundle)
        .then(setCheck)
        .catch((e) => setMsg(String(e).slice(0, 160)))
        .finally(() => setBusy(null));
    } catch (e) { setMsg(String(e)); setBusy(null); }
  };

  const commission = () => {
    setBusy("commission"); setMsg(null);
    try {
      const bundle = toBundle(model);
      api.importNetwork(bundle as never)
        .then((prev) => api.applyConfig(prev.id ?? (prev as never)))
        .then((r) => { onApplied(r as ApplyResponse);
          setMsg("Netz in Betrieb genommen ✓"); })
        .catch((e) => setMsg(String(e).slice(0, 200)))
        .finally(() => setBusy(null));
    } catch (e) { setMsg(String(e)); setBusy(null); }
  };

  const nC = model.nodes.filter((n) => n.kind === "consumer").length;

  return (
    <div className="editor" style={{ display: "grid",
         gridTemplateColumns: "1fr 300px", height: "100%" }}>
      <div style={{ position: "relative" }}>
        <EditorMap streets={streets} model={model} tool={tool}
          dn={pipeSel.dn} material={pipeSel.material} pendingFrom={pendingFrom}
          onBboxChange={setBbox} onPlaceNode={placeNode} onDrawPipe={drawPipe}
          onDeleteNode={deleteNode} onDeletePipe={deletePipe}
          onPickPipeFrom={setPendingFrom} />
      </div>

      <div className="editor-panel" style={{ overflowY: "auto", padding: 10,
           borderLeft: "1px solid var(--border, #334)", fontSize: "0.8rem" }}>
        <h3 style={{ marginTop: 0 }}>🛠 NetzStudio — Editor</h3>
        <div className="muted" style={{ fontSize: "0.7rem", marginBottom: 6 }}>
          Netz auf echten Straßen zeichnen, gegen die W 400-1 Lastfälle prüfen,
          in Betrieb nehmen.
        </div>

        <button style={{ width: "100%", marginBottom: 6 }}
                disabled={!bbox || busy === "streets"} onClick={loadStreets}>
          {busy === "streets" ? "…" : "🗺 Straßen für Ausschnitt laden"}
        </button>

        <div className="mi-hdr">Werkzeug</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4 }}>
          {TOOLS.map((tl) => (
            <button key={tl.id} title={tl.hint}
              style={{ padding: "4px 2px",
                       outline: tool === tl.id ? "2px solid var(--accent,#38f)" : "none" }}
              disabled={tl.id === "source" && hasSource && tool !== "source"}
              onClick={() => { setTool(tl.id); setPendingFrom(null); }}>
              {tl.label}
            </button>
          ))}
        </div>
        {tool === "pipe" && (
          <div className="note" style={{ fontSize: "0.68rem", marginTop: 4 }}>
            {pendingFrom ? "Zielknoten wählen…" : "Startknoten wählen…"}
          </div>
        )}

        {tool === "pipe" && (
          <div style={{ marginTop: 6 }}>
            <div className="mi-hdr">Leitung (DN/Material)</div>
            <select value={pipeSel.label} style={{ width: "100%" }}
              onChange={(e) => setPipeSel(
                PIPE_CATALOG.find((p) => p.label === e.target.value)!)}>
              {PIPE_CATALOG.map((p) => (
                <option key={p.label} value={p.label}>{p.label}</option>))}
            </select>
          </div>
        )}

        <div className="mi-hdr" style={{ marginTop: 8 }}>Netz</div>
        <div className="muted" style={{ fontSize: "0.72rem" }}>
          {model.nodes.length} Knoten · {nC} Abnehmer · {model.pipes.length} Leitungen
          {hasSource ? " · ◆ Quelle" : " · keine Quelle"}
        </div>
        <button style={{ width: "100%", marginTop: 4 }}
          onClick={() => { setModel(emptyModel()); setCheck(null); setStreets(
            streets); setPendingFrom(null); }}>
          ⟲ Neu anfangen
        </button>

        {problems.length > 0 && (
          <div className="note" style={{ fontSize: "0.7rem", marginTop: 8,
               color: "#f2ae00" }}>
            ⚠ noch offen: {problems.join("; ")}
          </div>
        )}

        <div className="mi-hdr" style={{ marginTop: 8 }}>W 400-1 Lastfälle</div>
        <button style={{ width: "100%" }} disabled={problems.length > 0 || !!busy}
                onClick={runCheck}>
          {busy === "check" ? "prüfe…" : "✓ Lastfälle prüfen"}
        </button>
        {check && (
          <div style={{ marginTop: 6 }}>
            <div style={{ fontWeight: 600,
                 color: check.passed ? "#16a34a" : "#ef4444" }}>
              {check.passed ? "✓ alle Lastfälle bestanden" : "✗ Lastfall nicht bestanden"}
            </div>
            {check.cases.map((c) => (
              <div key={c.id} style={{ borderLeft: `3px solid ${
                c.passed ? "#16a34a" : "#ef4444"}`, padding: "2px 6px",
                marginTop: 3, fontSize: "0.7rem" }}>
                <b>{c.passed ? "✓" : "✗"} {c.name}</b>
                <div className="muted">{c.detail}</div>
              </div>
            ))}
          </div>
        )}

        <button style={{ width: "100%", marginTop: 8, fontWeight: 600 }}
          disabled={problems.length > 0 || !!busy}
          onClick={commission}>
          {busy === "commission" ? "…" : "🏁 Netz in Betrieb nehmen"}
        </button>
        {msg && (
          <div className="note" style={{ fontSize: "0.7rem", marginTop: 6 }}>
            {msg}
          </div>
        )}
      </div>
    </div>
  );
}
