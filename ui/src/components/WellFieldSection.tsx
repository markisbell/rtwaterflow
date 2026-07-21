/**
 * WellFieldSection — Brunnenfeld & Grundwasser (M6).
 *
 * The raw-water cockpit: per well field the aquifer level, the current
 * production vs capacity, the pumping energy KPI and the water-right
 * accounting; per well the ageing and a regenerate action. A drought
 * slider scales the recharge — the Lauenau teaching loop (drought →
 * falling aquifer → capacity caps → households run dry).
 *
 * The raw side is station SCADA (an operator sees the well telemetry), so
 * it reads from `latest.wellfields`, present in strict mode too. The
 * drought factor comes from the wire, not a local poll (no stale race).
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type { StepResult, WellFieldState } from "../types";
import { fmt } from "../scales";
import Section, { Stat } from "./Section";

export default function WellFieldSection({ open, onToggle, latest }: {
  open: boolean;
  onToggle: () => void;
  latest: StepResult | null;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const fields = latest?.wellfields ?? [];
  if (!latest || fields.length === 0) return null;

  const drought = fields[0]?.aquifer_drought_factor ?? 1.0;
  const anyExceeded = fields.some(
    (w) => w.water_right.day_exceeded || w.water_right.year_exceeded);

  const act = (make: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    make().catch((e) => window.alert(String(e))).finally(() => setBusy(false));
  };

  return (
    <Section title={t("well.heading")} open={open} onToggle={onToggle}
             badges={anyExceeded ? ["⚠"] : []}>
      {/* drought slider (applies to every aquifer) */}
      <div className="field" style={{ marginBottom: 4 }}>
        <label title={t("well.droughtTitle")}>
          {t("well.drought")}: {fmt(drought, 2)}
          {drought < 0.5 ? " 🏜" : drought >= 1.0 ? " 💧" : ""}
        </label>
        <input type="range" min={0} max={1.5} step={0.1} value={drought}
               disabled={busy}
               onChange={(e) => act(() =>
                 api.setDrought(Number(e.target.value)))} />
      </div>

      {fields.map((wf) => (
        <WellFieldCard key={wf.name} wf={wf} busy={busy} act={act} />
      ))}

      <div className="muted" style={{ fontSize: "0.66rem", marginTop: 4 }}>
        {t("well.hint")}
      </div>
    </Section>
  );
}

function WellFieldCard({ wf, busy, act }: {
  wf: WellFieldState;
  busy: boolean;
  act: (make: () => Promise<unknown>) => void;
}) {
  const { t } = useTranslation();
  const wr = wf.water_right;
  const capped = wf.production_m3_h >= wf.capacity_m3_h - 0.5;

  return (
    <div style={{ marginBottom: 6 }}>
      <div className="stat-row">
        <span className="muted">🏗 {wf.name}</span>
        <span className="v">{wf.n_wells_running}/{wf.wells.length} 🔵</span>
      </div>
      <Stat label={t("well.aquifer")}
            value={`${fmt(wf.aquifer_level_m, 1)} m`} />
      <Stat label={t("well.production")}
            value={`${fmt(wf.production_m3_h, 1)} / ${fmt(wf.capacity_m3_h, 1)} m³/h`}
            color={capped ? "#f2ae00" : undefined} />
      {wf.energy_kwh_per_m3 != null && (
        <Stat label={t("well.energy")}
              value={`${fmt(wf.energy_kwh_per_m3, 2)} kWh/m³`} />
      )}
      {(wr.day_limit_m3 != null || wr.year_limit_m3 != null) && (
        <Stat label={t("well.right")}
              value={wr.year_limit_m3 != null
                ? `${fmt(wr.year_m3, 0)} / ${fmt(wr.year_limit_m3, 0)} m³/a`
                : `${fmt(wr.day_m3, 0)} / ${fmt(wr.day_limit_m3 ?? 0, 0)} m³/d`}
              color={wr.year_exceeded || wr.day_exceeded ? "#ef4444" : undefined} />
      )}
      {(wr.year_exceeded || wr.day_exceeded) && (
        <div className="note" style={{ fontSize: "0.68rem" }}>
          ⚠ {t("well.rightExceeded")}
        </div>
      )}
      {wf.wells.map((w) => (
        <div key={w.name} className="stat-row" style={{ fontSize: "0.72rem" }}>
          <span className="muted">
            {w.running ? "🔵" : "⚪"} {w.name}
            {w.aged_fraction >= 0.1
              && <span style={{ color: "#f2ae00" }}> · {t("well.aged", {
                p: fmt(w.aged_fraction * 100, 0) })}</span>}
          </span>
          {w.aged_fraction >= 0.05 && (
            <button className="mini-x" disabled={busy}
                    style={{ color: "var(--accent)" }}
                    title={t("well.regenerateTitle")}
                    onClick={() => act(() =>
                      api.regenerateWell(wf.name, w.name))}>
              ⟳
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
