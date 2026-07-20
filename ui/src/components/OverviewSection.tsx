/**
 * The Übersicht side-panel section (blueprint OverviewSection port): per view
 * mode it aggregates a different data layer — the revealed ground truth or
 * only what the placed water meters deliver.
 */
import { useTranslation } from "react-i18next";
import Section, { Stat } from "./Section";
import { fmt, pressureColor } from "../scales";
import type {
  EstimatedState,
  ObservedSummary,
  StepSummary,
} from "../types";

export default function OverviewSection({
  open, onToggle, mode, summary, observed,
  solverStatus, solveMs, canReveal, est, estAgeMin,
}: {
  open: boolean;
  onToggle: () => void;
  mode: "truth" | "observed" | "est";
  /** In est mode this is the SPLICED estimated summary (LiveWaterFlow). */
  summary: StepSummary | undefined;
  observed: ObservedSummary | null | undefined;
  solverStatus: string | undefined;
  solveMs: number | null;
  canReveal: boolean;
  est?: EstimatedState | null;
  estAgeMin?: number | null;
}) {
  const { t } = useTranslation();
  const reveal = (mode === "truth" || mode === "est") && !!summary;
  const s = summary;
  const os = observed;

  const solverBadge = solverStatus ? (
    <span className={`badge ${solverStatus}`}>
      {solverStatus === "ok" ? t("ov.solverOk")
        : solverStatus === "degraded" ? t("ov.solverDegraded")
        : t("ov.solverFailed")}
    </span>
  ) : null;

  const flow = (v: number | null | undefined) =>
    v != null ? `${fmt(v * 3.6, 2)} m³/h` : t("ov.na");

  return (
    <Section title={t("ov.heading")} open={open} onToggle={onToggle}>
      <div className="muted" style={{ fontSize: "0.68rem", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 2 }}>
        {mode === "est" ? <>🧮 {t("ov.estCaption")}</>
          : reveal ? <>👁 {t("ov.groundTruth")}</>
          : <>📟 {t("ov.observedCaption")}</>}
      </div>
      {reveal && s ? (
        <>
          <Stat label={t("ov.feedIn")} value={flow(s.mdot_feed_kg_per_s)} />
          <Stat label={t("ov.demand")} value={flow(s.mdot_demand_kg_per_s)} />
          <Stat label={t("ov.delivered")}
                value={flow(s.mdot_delivered_kg_per_s)} />
          <Stat label={t("ov.worstPoint")}
                value={s.p_min_bar != null
                  ? `${fmt(s.p_min_bar, 2)} bar · ${s.worst_consumer ?? "—"}`
                  : t("ov.na")}
                color={pressureColor(s.p_min_bar)} />
        </>
      ) : (
        <>
          <Stat label={t("ov.feedIn")} value={flow(os?.mdot_feed_kg_per_s)} />
          <Stat label={t("ov.demandMetered")}
                value={flow(os?.mdot_demand_metered_kg_per_s)} />
          <Stat label={t("ov.sourcePressure")}
                value={os?.p_source_bar != null
                  ? `${fmt(os.p_source_bar, 2)} bar` : t("ov.na")} />
          <Stat label={t("ov.worstPoint")}
                value={os?.p_min_bar != null
                  ? `${fmt(os.p_min_bar, 2)} bar · ${os.worst_consumer ?? "—"}`
                  : t("ov.na")}
                color={pressureColor(os?.p_min_bar)} />
          <Stat label={t("ov.coverage")}
                value={os ? `${os.n_metered}/${os.n_consumers}` : "—"} />
        </>
      )}
      {mode === "est" && est && (
        <>
          <div className="muted" style={{ fontSize: "0.68rem", textTransform: "uppercase", letterSpacing: "0.05em", margin: "6px 0 2px" }}>
            🧮 {t("ov.estQuality")}
          </div>
          <Stat label={t("ov.estAge")}
                value={estAgeMin != null && estAgeMin > 0
                  ? t("ov.estAgeMin", { min: estAgeMin, seq: est.seq })
                  : t("ov.estAgeNow", { seq: est.seq })} />
          <Stat label={t("ov.estErrMdot")}
                value={est.error.max_dmdot_kg_per_s != null
                  ? `${fmt(est.error.max_dmdot_kg_per_s, 3)} kg/s` : t("ov.na")} />
          <Stat label={t("ov.estErrDp")}
                value={est.error.max_dp_bar != null
                  ? `${fmt(est.error.max_dp_bar, 3)} bar` : t("ov.na")} />
          <Stat label={t("ov.estSolve")} value={`${fmt(est.solve_ms, 1)} ms`} />
          <div className="muted" style={{ fontSize: "0.68rem", marginTop: 4 }}>
            {t("ov.estNote")}
          </div>
        </>
      )}
      <div className="stat-row">
        <span className="muted">{t("ov.solver")}</span>
        <span className="v">{solverBadge ?? "—"}</span>
      </div>
      <Stat label={t("ov.solveTime")} value={solveMs != null ? `${fmt(solveMs, 1)} ms` : "—"} />
      {!reveal && (
        <div className="muted" style={{ fontSize: "0.68rem", marginTop: 4 }}>
          {t("ov.observedNote")}{!canReveal ? ` ${t("live.truthHidden")}` : ""}
        </div>
      )}
    </Section>
  );
}
