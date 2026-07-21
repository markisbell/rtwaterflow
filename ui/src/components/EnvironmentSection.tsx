/**
 * EnvironmentSection — Umwelt & Wetter (M3).
 *
 * Shows the live air temperature (bundle series + runtime offset) and the
 * weather-scenario segment: Normal ↔ Hitzetag (t +6 °C, dryness 0.9 — the
 * 2018 hot-dry regime: the demand engine shifts the daily peak to
 * 19–21 h and roughly doubles hot-day volumes). Only bundles whose
 * consumers carry archetype size data react hydraulically; legacy bundles
 * shift the displayed temperature only (the API says which — honest note).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type { EnvironmentInfo, Topology } from "../types";
import { fmt } from "../scales";
import Section, { Stat } from "./Section";

/** dryness at/above this actually arms the irrigation surge (engine's
 *  IRRIGATION_DRYNESS) — lower overrides are not a Hitzetag */
const HOT_DRYNESS = 0.5;

export default function EnvironmentSection({ open, onToggle, topo }: {
  open: boolean;
  onToggle: () => void;
  topo: Topology;
}) {
  const { t } = useTranslation();
  const [env, setEnv] = useState<EnvironmentInfo | null>(null);
  const [busy, setBusy] = useState(false);
  // write sequence guard: a poll GET resolved AFTER a POST but read
  // BEFORE it must not overwrite the fresh state (M3 review finding)
  const writeSeq = useRef(0);

  const load = useCallback(() => {
    const seq = writeSeq.current;
    api.environment().then((e) => {
      if (writeSeq.current === seq) setEnv(e);
    }).catch(() => {});
  }, []);
  useEffect(() => {
    load();
    const iv = setInterval(load, 5000);
    return () => clearInterval(iv);
  }, [load, topo]);

  // three-way state (M3 review: negative offsets / sub-threshold dryness
  // are neither Normal nor Hitzetag): normal = no overrides at all; hot =
  // warming offset or surge-arming dryness; anything else = custom
  const offset = env?.t_offset_c ?? 0;
  const dry = env?.dryness_override;
  const state: "normal" | "hot" | "custom" =
    offset === 0 && dry == null ? "normal"
    : offset > 0 || (dry != null && dry >= HOT_DRYNESS) ? "hot"
    : "custom";
  const hot = state === "hot";

  const apply = (makeHot: boolean) => {
    if (busy) return;
    setBusy(true);
    writeSeq.current += 1;
    const body = makeHot
      ? { t_offset_c: 6.0, dryness: 0.9 }
      : { t_offset_c: 0.0, clear_dryness: true };
    api.setEnvironment(body).then(setEnv)
      .catch((e) => window.alert(String(e)))
      .finally(() => setBusy(false));
  };

  return (
    <Section title={t("env.heading")} open={open} onToggle={onToggle}
             badges={hot ? ["🔥"] : []}>
      <Stat label={t("env.tAir")}
            value={env?.t_air_now_c != null
              ? `${fmt(env.t_air_now_c, 1)} °C` : "—"}
            color={hot ? "#f2ae00" : undefined} />
      {(env?.t_offset_c ?? 0) !== 0 && (
        <Stat label={t("env.tOffset")}
              value={`${env!.t_offset_c > 0 ? "+" : ""}${fmt(env!.t_offset_c, 1)} K`} />
      )}
      <div className="mbar-seg" role="group"
           style={{ marginTop: 4, display: "inline-flex" }}>
        <button className={state === "normal" ? "on" : ""} disabled={busy}
                title={t("env.normalTitle")}
                onClick={() => apply(false)}>
          {t("env.normal")}
        </button>
        <button className={hot ? "on" : ""} disabled={busy}
                title={t("env.hotTitle")}
                onClick={() => apply(true)}>
          🔥 {t("env.hot")}
        </button>
      </div>
      {state === "custom" && (
        <div className="muted" style={{ fontSize: "0.68rem", marginTop: 2 }}>
          ≠ {t("env.custom")}
        </div>
      )}
      {env && env.n_profiled_consumers === 0 && (
        <div className="note" style={{ fontSize: "0.68rem", marginTop: 4 }}>
          {t("env.legacyNote")}
        </div>
      )}
      <div className="muted" style={{ fontSize: "0.68rem", marginTop: 4 }}>
        {t("env.hint")}
      </div>
    </Section>
  );
}
