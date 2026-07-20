/**
 * WorstPointSection — the Schlechtpunkt (minimum service pressure) readout.
 *
 * M0: read-only. The worst point is the consumer with the lowest service
 * pressure; the trace shows how it evolves. The M2 water controllers
 * (worst-point service-pressure control on pump stations, same
 * observed-layer-only discipline as the fork parent's Δp controller)
 * re-grow their cockpit here.
 */
import { useTranslation } from "react-i18next";
import type { StepResult } from "../types";
import { fmt, pressureColor } from "../scales";
import Section, { Stat } from "./Section";
import Sparkline from "./Sparkline";

export default function WorstPointSection({ open, onToggle, latest, trace }: {
  open: boolean;
  onToggle: () => void;
  latest: StepResult | null;
  /** client-accumulated min-pressure per frame (observed layer preferred) */
  trace: number[];
}) {
  const { t } = useTranslation();

  const pObserved = latest?.observed_summary?.p_min_bar ?? null;
  const worst = latest?.observed_summary?.worst_consumer
    ?? latest?.summary?.worst_consumer ?? null;
  const pTruth = latest?.summary?.p_min_bar ?? null;
  const blindSpot = latest?.controls?.blind_spot ?? null;

  return (
    <Section title={t("wp.heading")} open={open} onToggle={onToggle}>
      {trace.length > 1 && (
        <Sparkline values={trace} width={300} height={110} fluid
                   hourAxis={false} color="#7fd1ff"
                   yTitle={t("wp.yTitle")} />
      )}

      <Stat label={t("wp.observed")}
            value={pObserved != null
              ? `${fmt(pObserved, 2)} bar · ${worst ?? "—"}`
              : t("wp.blind")}
            color={pressureColor(pObserved)} />
      {pTruth != null && (
        <Stat label={t("wp.truth")}
              value={`${fmt(pTruth, 2)} bar`}
              color={pressureColor(pTruth)} />
      )}
      {blindSpot === true && (
        /* the TRUE worst point carries no meter — the operator's view is
         * incomplete (meta-information about the sensor layout) */
        <div className="note" style={{ fontSize: "0.7rem", marginTop: 4 }}>
          ⚠️ {pObserved != null ? t("wp.blindSpot") : t("wp.blindSpotNoMeter")}
        </div>
      )}
      <div className="muted" style={{ fontSize: "0.68rem", marginTop: 4 }}>
        {t("wp.hint")}
      </div>
    </Section>
  );
}
