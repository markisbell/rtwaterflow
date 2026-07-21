/**
 * EventSection — Störungen & Ereignisse (M5).
 *
 * The scenario cockpit: toggle pressure-driven demand, open a fire hydrant,
 * place a pipe burst, seed background leakage, and see the live emitters
 * (with their delivered flow) — each removable. The node is picked from the
 * network's junctions; a live deficit read-out ("taps running dry") shows
 * when the network cannot meet demand.
 *
 * PDA off is the teaching contrast (undersupply then shows as impossible
 * negative pressure); a warning makes that explicit.
 */
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type { EmitterState, StepResult, Topology } from "../types";
import { M3H_PER_KG_S, fmt } from "../scales";
import Section from "./Section";

const KIND_ICON: Record<EmitterState["kind"], string> = {
  hydrant: "🚒", burst: "💥", leak: "💧",
};
const FIRE_FLOWS = [48, 96, 192]; // W 405 target flows m³/h

export default function EventSection({ open, onToggle, topo, latest }: {
  open: boolean;
  onToggle: () => void;
  topo: Topology;
  latest: StepResult | null;
}) {
  const { t } = useTranslation();
  const [pda, setPda] = useState(true);
  const [node, setNode] = useState("");
  const [busy, setBusy] = useState(false);

  const nodes = topo.nodes.map((n) => n.name);
  useEffect(() => { if (!node && nodes.length) setNode(nodes[0]); }, [nodes, node]);

  const refreshPda = useCallback(() => {
    api.emitters().then((e) => setPda(e.pda_enabled)).catch(() => {});
  }, []);
  useEffect(() => { refreshPda(); }, [refreshPda, topo]);

  const emitters = latest?.emitters ?? [];
  const deficit = latest?.summary?.mdot_deficit_kg_per_s ?? 0;
  const emitted = latest?.summary?.mdot_emitted_kg_per_s ?? 0;

  // act() takes a THUNK, not a live promise: the request must not be
  // dispatched until AFTER the busy check (an eagerly-evaluated promise
  // fires before the guard and its rejection goes unhandled — M5 review)
  const act = (make: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    make().catch((e) => window.alert(String(e))).finally(() => setBusy(false));
  };

  return (
    <Section title={t("event.heading")} open={open} onToggle={onToggle}
             badges={emitters.length ? [String(emitters.length)] : []}>
      {/* PDA toggle */}
      <div className="stat-row">
        <span className="muted" title={t("event.pdaTitle")}>
          {t("event.pda")}
        </span>
        <span className="mbar-seg" style={{ display: "inline-flex" }}>
          <button className={pda ? "on" : ""}
                  onClick={() => act(() => api.setPda(true).then(() => setPda(true)))}>
            {t("event.on")}
          </button>
          <button className={!pda ? "on" : ""}
                  onClick={() => act(() => api.setPda(false).then(() => setPda(false)))}>
            {t("event.off")}
          </button>
        </span>
      </div>
      {!pda && (
        <div className="note" style={{ fontSize: "0.68rem" }}>
          ⚠ {t("event.pdaOffWarn")}
        </div>
      )}

      {(deficit > 0.01 || emitted > 0.01) && (
        <div style={{ fontSize: "0.72rem", margin: "3px 0" }}>
          {deficit > 0.01 && (
            <div style={{ color: "#ef4444" }}>
              🚱 {t("event.deficit", {
                v: fmt(deficit * M3H_PER_KG_S, 1) })}
            </div>
          )}
          {emitted > 0.01 && (
            <div className="muted">
              💧 {t("event.emitted", {
                v: fmt(emitted * M3H_PER_KG_S, 1) })}
            </div>
          )}
        </div>
      )}

      {/* node picker + actions */}
      <div className="field" style={{ marginTop: 4 }}>
        <label>{t("event.node")}</label>
        <select value={node} onChange={(e) => setNode(e.target.value)}>
          {nodes.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </div>
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 2 }}>
        {FIRE_FLOWS.map((q) => (
          <button key={q} disabled={busy} title={t("event.hydrantTitle")}
                  onClick={() => act(() => api.openHydrant({
                    node, target_m3_h: q, duration_minutes: 120 }))}>
            🚒 {q}
          </button>
        ))}
        <button disabled={busy} title={t("event.burstTitle")}
                onClick={() => act(() => api.placeBurst({ node, area_m2: 0.01 }))}>
          💥 {t("event.burst")}
        </button>
      </div>
      <div style={{ display: "flex", gap: 4, marginTop: 4, alignItems: "center" }}>
        <span className="muted" style={{ fontSize: "0.7rem" }}>{t("event.leak")}</span>
        <button disabled={busy}
                onClick={() => act(() => api.setLeakage(0.05))}>+ {t("event.seed")}</button>
        <button disabled={busy}
                onClick={() => act(() => api.clearLeakage())}>{t("event.repair")}</button>
      </div>

      {/* live emitters */}
      {emitters.length > 0 && (
        <div style={{ marginTop: 6 }}>
          {emitters.filter((e) => e.kind !== "leak").map((e) => (
            <div key={e.name} className="stat-row">
              <span className="muted" title={e.node}>
                {KIND_ICON[e.kind]} {e.name}
              </span>
              <span className="v">
                {fmt(e.m3_per_h, 1)} m³/h
                <button className="mini-x" style={{ marginLeft: 6 }}
                        title={t("event.remove")}
                        onClick={() => act(() => api.removeEmitter(e.name))}>✕</button>
              </span>
            </div>
          ))}
          {emitters.some((e) => e.kind === "leak") && (
            <div className="stat-row">
              <span className="muted">💧 {t("event.leaksActive")}</span>
              <span className="v">
                {emitters.filter((e) => e.kind === "leak").length}
                <button className="mini-x" style={{ marginLeft: 6 }}
                        onClick={() => act(() => api.clearLeakage())}>✕</button>
              </span>
            </div>
          )}
        </div>
      )}

      <div className="muted" style={{ fontSize: "0.66rem", marginTop: 4 }}>
        {t("event.hint")}
      </div>
    </Section>
  );
}
