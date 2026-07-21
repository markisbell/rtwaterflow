/**
 * ConsumerTableSection — the M3 consumer table (roadmap: "consumer table
 * with delivered/demanded columns").
 *
 * One row per consumer: archetype icon, name, demanded (Soll), delivered
 * (Ist) and local pressure, sorted by demand descending. In the measured
 * view only metered consumers show values (demanded is a model quantity —
 * honest "—" there); unknown stays unknown.
 */
import { useTranslation } from "react-i18next";
import type { StepResult } from "../types";
import { M3H_PER_KG_S, fmt, pressureColor } from "../scales";
import Section from "./Section";

const KIND_ICON: Record<string, string> = {
  residential: "🏠", residential_city: "🏙", residential_village: "🏠",
  industry: "🏭", farm: "🐄", farm_dairy: "🐄", farm_pigs: "🐖",
  school: "🏫", office: "🏢", hospital: "🏥", pool: "🏊", other: "📦",
  consumer: "📦",
};

export default function ConsumerTableSection({
  open, onToggle, latest, observedOnly,
}: {
  open: boolean;
  onToggle: () => void;
  /** the (possibly estimate-spliced) frame the view renders */
  latest: StepResult | null;
  observedOnly: boolean;
}) {
  const { t } = useTranslation();

  type Row = {
    key: string; icon: string; name: string;
    demanded: number | null; delivered: number | null; p: number | null;
  };
  let rows: Row[] = [];
  if (latest && !observedOnly && latest.consumers) {
    rows = latest.consumers.map((c) => ({
      key: `c${c.id}`, icon: KIND_ICON[c.kind ?? "consumer"] ?? "📦",
      name: c.name,
      demanded: c.mdot_demand_kg_per_s, delivered: c.mdot_kg_per_s,
      p: c.p_bar,
    }));
  } else if (latest && observedOnly) {
    rows = (latest.measurements?.consumers ?? []).map((c) => ({
      key: `m${c.id}`, icon: "📟", name: c.name,
      demanded: null, delivered: c.mdot_kg_per_s, p: c.p_bar,
    }));
  }
  rows.sort((a, b) => (b.demanded ?? b.delivered ?? 0)
    - (a.demanded ?? a.delivered ?? 0));

  return (
    <Section title={t("ct.heading")} open={open} onToggle={onToggle}
             badges={[String(rows.length)]}>
      {rows.length === 0 && (
        <div className="muted" style={{ fontSize: "0.72rem" }}>
          {observedOnly ? t("ct.noneMetered") : t("pin.noData")}
        </div>
      )}
      {rows.length > 0 && (
        <div style={{ maxHeight: 220, overflowY: "auto" }}>
          <table className="ctable" style={{
            width: "100%", fontSize: "0.72rem",
            borderCollapse: "collapse",
          }}>
            <thead>
              <tr style={{ textAlign: "right", color: "var(--muted)" }}>
                <th style={{ textAlign: "left", fontWeight: 400 }}>
                  {t("ct.consumer")}
                </th>
                <th style={{ fontWeight: 400 }}>{t("ct.demanded")}</th>
                <th style={{ fontWeight: 400 }}>{t("ct.delivered")}</th>
                <th style={{ fontWeight: 400 }}>p</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} style={{ textAlign: "right" }}>
                  <td style={{
                    textAlign: "left", maxWidth: 140, overflow: "hidden",
                    textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }} title={r.name}>
                    {r.icon} {r.name}
                  </td>
                  <td>{r.demanded != null
                    ? fmt(r.demanded * M3H_PER_KG_S, 2) : "—"}</td>
                  <td>{r.delivered != null
                    ? fmt(r.delivered * M3H_PER_KG_S, 2) : "—"}</td>
                  <td style={r.p != null
                    ? { color: pressureColor(r.p) } : undefined}>
                    {r.p != null ? fmt(r.p, 1) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="muted" style={{ fontSize: "0.66rem", marginTop: 4 }}>
        {t("ct.hint")}
      </div>
    </Section>
  );
}
