/**
 * Pinned element sections (blueprint EquipmentControls port, water domain):
 * Ctrl-click on a map element pins a details Section here; the section shows
 * live values from the stream. The NumInput helper stays for the M2 water
 * asset config knobs (tanks, pump stations).
 */
import { useEffect, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type { StepResult } from "../types";
import { fmt, pressureColor } from "../scales";
import Section, { Stat } from "./Section";

export interface PinTarget {
  kind: "consumer" | "producer";
  id: number;
  name: string;
}

const numStyle: CSSProperties = {
  width: 70, fontSize: "0.75rem", background: "var(--panel-2)",
  color: "var(--text)", border: "1px solid var(--border)", borderRadius: 4,
  padding: "1px 4px", textAlign: "right",
};

export function NumInput({ value, onCommit, min, step }: {
  value: number; onCommit: (v: number) => void; min?: number; step?: number;
}) {
  const [v, setV] = useState(value);
  useEffect(() => setV(value), [value]);
  return (
    <input type="number" style={numStyle} value={v} min={min} step={step}
           onChange={(e) => setV(+e.target.value)}
           onBlur={() => v !== value && v > (min ?? -Infinity) && onCommit(v)}
           onKeyDown={(e) => e.key === "Enter"
             && (e.target as HTMLInputElement).blur()} />
  );
}

export default function PinnedSection({ pin, latest, onClose, onChanged }: {
  pin: PinTarget;
  latest: StepResult | null;
  onClose: () => void;
  onChanged: () => void; // consumer inventory changed -> reload topology
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);

  const head = (icon: string) => `${icon} ${pin.name}`;
  const remove = (fn: () => Promise<unknown>) => () =>
    fn().then(() => { onClose(); onChanged(); }).catch(() => {});

  if (pin.kind === "consumer") {
    const truthC = latest?.consumers?.find((x) => x.id === pin.id);
    const c = truthC
      ?? latest?.measurements?.consumers?.find((x) => x.id === pin.id);
    const demanded: number | null = truthC?.mdot_demand_kg_per_s ?? null;
    return (
      <Section title={head("🏠")} open={open}
               onToggle={() => setOpen(!open)} badges={["📌"]}>
        {c ? (
          <>
            {demanded != null && (
              <Stat label={t("pin.demanded")}
                    value={`${fmt(demanded, 3)} kg/s`} />
            )}
            <Stat label={t("pin.delivered")}
                  value={`${fmt(c.mdot_kg_per_s, 3)} kg/s`} />
            <Stat label={t("pin.pressure")} value={`${fmt(c.p_bar, 2)} bar`}
                  color={pressureColor(c.p_bar)} />
          </>
        ) : <div className="muted" style={{ fontSize: "0.75rem" }}>{t("pin.noData")}</div>}
        <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
          <button className="ghost" style={{ flex: 1, fontSize: "0.72rem" }}
                  onClick={remove(() => api.removeConsumer(pin.id))}>
            🗑️ {t("menu.removeConsumer")}
          </button>
          <button className="ghost" style={{ fontSize: "0.72rem" }} onClick={onClose}>
            {t("pin.unpin")}
          </button>
        </div>
      </Section>
    );
  }

  // producer (M0: the head source)
  const p = latest?.producers?.find((x) => x.id === pin.id);
  return (
    <Section title={head("🏔️")} open={open} onToggle={() => setOpen(!open)}
             badges={["📌"]}>
      {p ? (
        <>
          <Stat label={t("pin.pressure")} value={`${fmt(p.p_bar, 2)} bar`} />
          <Stat label={t("pin.feed")}
                value={`${fmt(p.mdot_kg_per_s, 3)} kg/s`} />
        </>
      ) : <div className="muted" style={{ fontSize: "0.75rem" }}>{t("pin.noData")}</div>}
      <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
        <button className="ghost" style={{ fontSize: "0.72rem" }} onClick={onClose}>
          {t("pin.unpin")}
        </button>
      </div>
    </Section>
  );
}
