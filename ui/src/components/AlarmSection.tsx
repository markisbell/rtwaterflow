/**
 * AlarmSection — the M4 alarm center (Meldungen).
 *
 * Groups the frame's compliance findings by severity (Verletzung /
 * Warnung / Hinweis), each with its German rule citation. Findings derive
 * from the truth layer, so they show ONLY in the reality view: in strict
 * mode and in the measured OR estimated views the section shows an honesty
 * note instead of truth-derived alarms or a fake "all green" — the M7
 * forward observer produces no findings of its own, so surfacing the truth
 * findings in the est view would betray the very anomalies the estimate
 * hides. A non-converged cold start shows "no data", never ✅.
 */
import { useTranslation } from "react-i18next";
import type { Finding, StepResult } from "../types";
import Section from "./Section";

const SEV_ICON: Record<Finding["severity"], string> = {
  violation: "🔴", warning: "🟡", info: "ℹ️",
};
const SEV_COLOR: Record<Finding["severity"], string> = {
  violation: "#ef4444", warning: "#f2ae00", info: "var(--muted)",
};
const ENTITY_ICON: Record<Finding["entity_kind"], string> = {
  consumer: "🏠", node: "●", pipe: "▬", tank: "🗼", system: "⚙",
};

export default function AlarmSection({ open, onToggle, latest, mode }: {
  open: boolean;
  onToggle: () => void;
  latest: StepResult | null;
  /** the active Sicht: findings derive from truth, so only "truth" reveals
   *  them — "observed"/"est" hide them honestly (M7). */
  mode: "truth" | "observed" | "est";
}) {
  const { t } = useTranslation();
  const truthAvailable = latest
    ? latest.findings !== undefined && mode === "truth" : true;
  const findings = truthAvailable ? (latest?.findings ?? []) : [];
  const nViol = findings.filter((f) => f.severity === "violation").length;
  const nWarn = findings.filter((f) => f.severity === "warning").length;
  const badges = [];
  if (nViol) badges.push(`🔴 ${nViol}`);
  if (nWarn) badges.push(`🟡 ${nWarn}`);

  const order: Finding["severity"][] = ["violation", "warning", "info"];
  const sorted = [...findings].sort(
    (a, b) => order.indexOf(a.severity) - order.indexOf(b.severity));

  return (
    <Section title={t("alarm.heading")} open={open} onToggle={onToggle}
             badges={badges}>
      {latest && !truthAvailable && (
        <div className="note" style={{ fontSize: "0.7rem" }}>
          {mode === "est" ? t("alarm.estHidden")
            : mode === "observed" ? t("alarm.measuredHidden")
            : t("alarm.truthHidden")}
        </div>
      )}
      {truthAvailable && latest && findings.length === 0 && (
        latest.converged ? (
          <div className="muted" style={{ fontSize: "0.75rem" }}>
            ✅ {t("alarm.allClear")}
          </div>
        ) : (
          /* non-converged empty shell: compliance never ran — never fake
           * an all-clear (M4 review) */
          <div className="muted" style={{ fontSize: "0.72rem" }}>
            {t("pin.noData")}
          </div>
        )
      )}
      {!latest && (
        <div className="muted" style={{ fontSize: "0.72rem" }}>
          {t("pin.noData")}
        </div>
      )}
      {sorted.length > 0 && (
        <div style={{ maxHeight: 260, overflowY: "auto" }}>
          {sorted.map((f, i) => (
            <div key={`${f.check}:${f.entity}:${i}`}
                 style={{
                   borderLeft: `3px solid ${SEV_COLOR[f.severity]}`,
                   padding: "2px 6px", marginBottom: 4,
                   fontSize: "0.72rem",
                 }}>
              <div>
                {SEV_ICON[f.severity]}{" "}
                <b>{ENTITY_ICON[f.entity_kind]} {f.entity}</b>
                <span className="muted"> · {f.rule}</span>
              </div>
              <div className="muted">{f.text_de}</div>
            </div>
          ))}
        </div>
      )}
      <div className="muted" style={{ fontSize: "0.66rem", marginTop: 4 }}>
        {t("alarm.hint")}
      </div>
    </Section>
  );
}
