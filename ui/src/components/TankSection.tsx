/**
 * TankSection — Behälter & Pumpwerke (M2).
 *
 * Per tank: an SVG level gauge (dead storage below level_min, the
 * Löschwasserreserve band directly above it, the usable band up to
 * level_max, the hysteresis switch marks of the owning station), the live
 * level/volume, the buffer countdown while draining, and the alarm lines.
 * Per pump station: the running lamp, the check-valve alarm, and the
 * operator mode segment (Auto / Ein / Aus → POST /station/{name}).
 *
 * Mode display: `latest.controls.stations` is authoritative — it is fresh
 * even on non-converged frames (the producers list is a stale copy there),
 * and a just-POSTed mode is kept as an optimistic overlay until any frame
 * confirms it (while paused no frames flow at all — M2 review finding).
 *
 * The hysteresis band (on_below/off_above) lives only in GET /stations
 * (static config) — fetched once per topology and drawn on the owning
 * tank's gauge.
 *
 * Tanks and stations are station SCADA (always telemetered in a real
 * waterworks, TF §7) — the section reads `latest.tanks` / the producer
 * entries, which survive strict mode.
 */
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type {
  StationInfo, StationMode, StepResult, TankState, Topology,
} from "../types";
import { M3H_PER_KG_S, fmt } from "../scales";
import Section, { Stat } from "./Section";

const MODES: StationMode[] = ["auto", "on", "off"];

/** on/off switch levels per tank name (from the static station config). */
type BandByTank = Record<string, { on: number; off: number }>;

export default function TankSection({ open, onToggle, topo, latest }: {
  open: boolean;
  onToggle: () => void;
  topo: Topology;
  latest: StepResult | null;
}) {
  const { t } = useTranslation();
  const tanks = latest?.tanks ?? [];
  const stations = (latest?.producers ?? []).filter(
    (p) => p.kind === "station");
  const hasTanks = topo.producers.some((p) => p.kind === "tank");
  const hasAny = hasTanks || (topo.stations ?? []).length > 0;

  // static station config (hysteresis band, curve) — once per topology
  const [config, setConfig] = useState<StationInfo[] | null>(null);
  useEffect(() => {
    let alive = true;
    if ((topo.stations ?? []).length) {
      api.stations().then((s) => { if (alive) setConfig(s); })
        .catch(() => {});
    } else {
      setConfig(null);
    }
    return () => { alive = false; };
  }, [topo]);

  // optimistic mode overlay: POSTed but not yet confirmed by a frame
  const [pending, setPending] = useState<Record<string, StationMode>>({});
  const confirmed = latest?.controls?.stations;
  useEffect(() => {
    if (!confirmed) return;
    setPending((p) => {
      const next = { ...p };
      for (const [name, mode] of Object.entries(p)) {
        if (confirmed[name] === mode) delete next[name];
      }
      return Object.keys(next).length === Object.keys(p).length ? p : next;
    });
  }, [confirmed]);

  if (!hasAny) return null;

  const bands: BandByTank = {};
  for (const s of config ?? []) {
    if (s.control.mode === "hysteresis" && s.control.tank
        && s.control.on_below_m != null && s.control.off_above_m != null) {
      bands[s.control.tank] = {
        on: s.control.on_below_m, off: s.control.off_above_m,
      };
    }
  }

  const alarmCount = tanks.filter(
    (tk) => tk.overflow || tk.empty || tk.fire_reserve_breached).length
    + stations.filter((s) => s.cv_closed).length;

  const setMode = (name: string, mode: StationMode) =>
    api.setStationMode(name, mode)
      .then((r) => setPending((p) => ({ ...p, [name]: r.mode })))
      .catch((e) => window.alert(String(e)));

  return (
    <Section title={t("tank.heading")} open={open} onToggle={onToggle}
             badges={alarmCount ? [`⚠ ${alarmCount}`] : []}>
      {!latest && (
        <div className="muted" style={{ fontSize: "0.72rem" }}>
          {t("pin.noData")}
        </div>
      )}
      {latest && !hasTanks && stations.length > 0 && (
        <div className="muted" style={{ fontSize: "0.72rem" }}>
          {t("tank.noTanks")}
        </div>
      )}

      {tanks.map((tk) => (
        <TankGauge key={tk.node} tank={tk} band={bands[tk.name]} />
      ))}

      {stations.map((s) => {
        const mode: StationMode = pending[s.name]
          ?? confirmed?.[s.name] ?? (s.mode ?? "auto") as StationMode;
        return (
          <div key={s.id} className="station-row" style={{ marginTop: 6 }}>
            <div className="stat-row">
              <span className="muted">⚙️ {s.name}</span>
              <span className="v" style={{
                color: s.cv_closed ? "#ef4444"
                  : s.running ? "#3fb950" : "var(--muted)",
              }}>
                {s.running ? `● ${t("tank.running")}` : `○ ${t("tank.stopped")}`}
                {s.running && s.mdot_kg_per_s != null && s.mdot_kg_per_s > 0
                  && ` · ${fmt(s.mdot_kg_per_s * M3H_PER_KG_S, 1)} m³/h`}
              </span>
            </div>
            {s.cv_closed && (
              <div className="note" style={{ fontSize: "0.68rem" }}>
                ⚠ {t("tank.cvClosed")}
              </div>
            )}
            <div className="mbar-seg" role="group"
                 style={{ marginTop: 2, display: "inline-flex" }}>
              {MODES.map((m) => (
                <button key={m} className={mode === m ? "on" : ""}
                        title={t(`tank.mode${m[0].toUpperCase()}${m.slice(1)}Title`)}
                        onClick={() => setMode(s.name, m)}>
                  {t(`tank.mode${m[0].toUpperCase()}${m.slice(1)}`)}
                </button>
              ))}
            </div>
          </div>
        );
      })}

      <div className="muted" style={{ fontSize: "0.68rem", marginTop: 4 }}>
        {t("tank.hint")}
      </div>
    </Section>
  );
}

/** Horizontal level gauge: 0..level_max with the dead band (< min), the
 *  fire reserve directly above min, the live fill, and — when a hysteresis
 *  station owns this tank — the on/off switch marks. */
function TankGauge({ tank: tk, band }: {
  tank: TankState;
  band?: { on: number; off: number };
}) {
  const { t } = useTranslation();
  const W = 280;
  const H = 14;
  const max = tk.level_max_m || 1;
  const min = tk.level_min_m ?? 0;
  const level = tk.level_m ?? 0;
  const x = (m: number) => Math.max(0, Math.min(W, (m / max) * W));
  // the fire reserve occupies fire_reserve_m3/area metres directly above
  // level_min; capacity = (max − min)·area, so the band height in metres
  // is fire/capacity · (max − min)
  const fireM = tk.fire_reserve_m3 != null && tk.capacity_m3 != null
    && tk.capacity_m3 > 0 && tk.level_max_m != null && tk.level_min_m != null
    ? (tk.fire_reserve_m3 / tk.capacity_m3) * (tk.level_max_m - tk.level_min_m)
    : 0;
  const flow = (tk.mdot_kg_per_s ?? 0) > 0.001 ? `▲ ${t("tank.inflow")}`
    : (tk.mdot_kg_per_s ?? 0) < -0.001 ? `▼ ${t("tank.outflow")}`
    : t("tank.balanced");

  return (
    <div style={{ marginBottom: 8 }}>
      <div className="stat-row">
        <span className="muted">🗼 {tk.name}</span>
        <span className="v">{t(`tank.${tk.kind}`)}</span>
      </div>
      <svg width="100%" viewBox={`0 0 ${W} ${H + 12}`}
           style={{ display: "block" }}>
        {/* shell */}
        <rect x={0} y={0} width={W} height={H} rx={2}
              fill="var(--panel2, #1c2128)" stroke="#5b6472"
              strokeWidth={1} />
        {/* dead storage below min */}
        <rect x={0} y={0} width={x(min)} height={H}
              fill="#39424f" opacity={0.9} />
        {/* fire reserve band above min */}
        {fireM > 0 && (
          <rect x={x(min)} y={0} width={x(min + fireM) - x(min)} height={H}
                fill="#7f1d1d" opacity={0.55}>
            <title>{t("tank.fireBand")}</title>
          </rect>
        )}
        {/* live fill */}
        <rect x={0} y={2} width={x(level)} height={H - 4}
              fill={tk.empty || tk.fire_reserve_breached ? "#f87171"
                : "#60a5fa"} opacity={0.85} />
        {/* min tick */}
        <line x1={x(min)} y1={0} x2={x(min)} y2={H} stroke="#e6edf3"
              strokeWidth={1} strokeDasharray="2 2" />
        {/* hysteresis switch marks (owning station's on/off levels) */}
        {band && (
          <g>
            <line x1={x(band.on)} y1={-1} x2={x(band.on)} y2={H + 1}
                  stroke="#4ade80" strokeWidth={1.4} />
            <line x1={x(band.off)} y1={-1} x2={x(band.off)} y2={H + 1}
                  stroke="#f2ae00" strokeWidth={1.4} />
            <title>
              {t("tank.band", { on: fmt(band.on, 1), off: fmt(band.off, 1) })}
            </title>
          </g>
        )}
        <text x={2} y={H + 10} fontSize={8} fill="var(--muted, #8b949e)">
          0
        </text>
        <text x={x(min)} y={H + 10} fontSize={8} textAnchor="middle"
              fill="var(--muted, #8b949e)">
          {fmt(min, 1)}
        </text>
        <text x={W - 2} y={H + 10} fontSize={8} textAnchor="end"
              fill="var(--muted, #8b949e)">
          {fmt(max, 1)} m
        </text>
      </svg>
      <Stat label={t("tank.level")}
            value={`${fmt(level, 2)} m · ${flow}`} />
      <Stat label={t("tank.volume")}
            value={`${fmt(tk.volume_m3, 0)} / ${fmt(tk.capacity_m3, 0)} m³`} />
      {tk.buffer_time_h != null && (
        <div title={t("tank.bufferTitle")}>
          <Stat label={t("tank.buffer")}
                value={`${fmt(tk.buffer_time_h, 1)} h`}
                color={tk.buffer_time_h < 3 ? "#ef4444" : undefined} />
        </div>
      )}
      {tk.overflow && (
        <div className="note" style={{ fontSize: "0.68rem" }}>
          ⚠ {t("tank.overflow")}
        </div>
      )}
      {tk.empty && (
        <div className="note" style={{ fontSize: "0.68rem" }}>
          ⚠ {t("tank.empty")}
        </div>
      )}
      {!tk.empty && tk.fire_reserve_breached && (
        <div className="note" style={{ fontSize: "0.68rem" }}>
          ⚠ {t("tank.fireReserve", { m3: fmt(tk.fire_reserve_m3, 0) })}
        </div>
      )}
    </div>
  );
}
