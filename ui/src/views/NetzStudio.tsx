import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api";
import type {
  ApplyResponse, NetworkImportBundle, NetworkListItem, NetworkPreview,
} from "../types";
import { fmt } from "../scales";

const FIVE_FILES = [
  "network_structure", "pipes", "consumers", "supply", "environment",
] as const;

/** The network workflow view: column 1 picks or imports a catalog network,
 *  column 2 previews it (KPI tiles) and applies it to the running engine.
 *  The M8 water editor ("show how networks are built": place nodes on the
 *  map, draw pipes, live DVGW load-case checks) grows here. */
export default function NetzStudio({ selected, onSelect, onApplied }: {
  selected: string | null;
  onSelect: (id: string | null) => void;
  onApplied: (r: ApplyResponse) => void;
}) {
  const { t } = useTranslation();
  const [networks, setNetworks] = useState<NetworkListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [netPrev, setNetPrev] = useState<NetworkPreview | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.networks().then((r) => setNetworks(r.networks))
      .catch((e) => setError(String(e)));
  }, []);

  // topology preview of the selected network (stale responses ignored)
  const reqRef = useRef(0);
  useEffect(() => {
    setNetPrev(null);
    if (!selected) return;
    const my = ++reqRef.current;
    api.networkPreview(selected)
      .then((p) => { if (reqRef.current === my) setNetPrev(p); })
      .catch((e) => { if (reqRef.current === my) setError(String(e)); });
  }, [selected]);

  const apply = () => {
    if (!selected) return;
    setBusy(true);
    api.applyConfig(selected)
      .then(onApplied)
      .catch((e) => setError(String(e)))
      .finally(() => setBusy(false));
  };

  // ---- five-file bundle import (POST /networks/import) ----
  const fileRef = useRef<HTMLInputElement>(null);
  const [note, setNote] = useState<string | null>(null);

  const importFiles = async (files: File[]) => {
    setNote(null);
    try {
      let bundle: NetworkImportBundle;
      if (files.length === 1) {
        // a single JSON carrying all five documents as keys
        const doc = JSON.parse(await files[0].text());
        const missing = FIVE_FILES.filter((k) => !(k in doc));
        if (missing.length) {
          throw new Error(`${t("netz.importNeedFive")} (${missing.join(", ")})`);
        }
        bundle = doc as NetworkImportBundle;
      } else {
        // the five contract files picked together (named <doc>.json)
        const byName: Record<string, unknown> = {};
        for (const f of files) {
          const key = f.name.replace(/\.json$/i, "");
          if ((FIVE_FILES as readonly string[]).includes(key)) {
            byName[key] = JSON.parse(await f.text());
          }
        }
        const missing = FIVE_FILES.filter((k) => !(k in byName));
        if (missing.length) {
          throw new Error(`${t("netz.importNeedFive")} (${missing.join(", ")})`);
        }
        bundle = byName as unknown as NetworkImportBundle;
      }
      const r = await api.importNetwork(bundle);
      const nets = await api.networks();
      setNetworks(nets.networks);
      onSelect(r.id);
      setNote(t("netz.imported", { name: r.name }));
    } catch (e) {
      setNote(`${t("netz.importErr")} ${String(e)}`);
    }
  };

  if (error && !networks) return <div className="empty">{t("netz.failed")}<br />{error}</div>;
  if (!networks) return <div className="spinner">{t("netz.loading")}</div>;

  return (
    <div className="netzstudio">
      {/* ---- 1 · pick or import a network -------------------------------- */}
      <aside className="ns-list">
        <h3>{t("netz.step1")}</h3>
        <button className="ghost" style={{ width: "100%" }}
                title={t("netz.importTitle")}
                onClick={() => fileRef.current?.click()}>
          ⬆ {t("netz.import")}
        </button>
        <input ref={fileRef} type="file" multiple
               accept=".json,application/json" style={{ display: "none" }}
               onChange={(e) => {
                 const files = Array.from(e.target.files ?? []);
                 if (files.length) importFiles(files);
                 e.target.value = "";
               }} />
        {note && (
          <p className="note" style={{ fontSize: "0.75rem" }}>{note}</p>
        )}
        <div className="ns-hdr">{t("netz.library")}</div>
        {networks.filter((n) => n.source !== "user").map((n) => (
          <NetworkRow key={n.id} n={n} selected={n.id === selected}
                      onClick={() => onSelect(n.id)} />
        ))}
        {networks.some((n) => n.source === "user") && (
          <>
            <div className="ns-hdr">{t("netz.own")}</div>
            {networks.filter((n) => n.source === "user").map((n) => (
              <NetworkRow key={n.id} n={n} selected={n.id === selected}
                          onClick={() => onSelect(n.id)} />
            ))}
          </>
        )}
      </aside>

      {/* ---- 2 · preview & apply ----------------------------------------- */}
      <section className="ns-preview">
        <h3>{t("netz.step3")}</h3>
        {!selected && <div className="muted">{t("netz.pickHint")}</div>}
        {selected && netPrev && (
          <>
            <div className="kpis">
              <Kpi k={t("netz.kConsumers")} v={`${netPrev.n_consumers}`} />
              <Kpi k={t("netz.kPipes")} v={`${fmt(netPrev.pipe_km, 2)} km`} />
              <Kpi k={t("netz.kDemand")}
                   v={`${fmt(netPrev.demand_m3_per_h, 2)} m³/h`} />
              <Kpi k={t("netz.kElevation")}
                   v={`${fmt(netPrev.elevation_min_m, 0)}–${fmt(netPrev.elevation_max_m, 0)} m`} />
              <Kpi k={t("netz.kSupply")}
                   v={`${netPrev.supply.node} · ${fmt(netPrev.supply.p_bar, 1)} bar`} />
            </div>
            <div style={{ display: "flex", gap: "0.5rem", marginTop: "0.6rem" }}>
              <button className="primary" onClick={apply} disabled={busy}>
                {t("netz.apply")}
              </button>
            </div>
            {error && <p className="note">{error}</p>}
          </>
        )}
      </section>
    </div>
  );
}

function NetworkRow({ n, selected, onClick }: {
  n: NetworkListItem; selected: boolean; onClick: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className={`ns-row${selected ? " sel" : ""}`} onClick={onClick}>
      <div className="title">{n.name}</div>
      <div className="sub">
        {n.character && (
          <span className="tag">
            {t(`netz.ch_${n.character}`, { defaultValue: n.character })}
          </span>
        )}
        {n.nodes != null && (
          <span className="muted"> {t("netz.nodes", { count: n.nodes })}</span>
        )}
        {n.pipe_km != null && (
          <span className="muted"> · {fmt(n.pipe_km, 2)} km</span>
        )}
      </div>
    </div>
  );
}

function Kpi({ k, v, tone, title }: {
  k: string; v: string; tone?: "ok" | "warn" | "bad"; title?: string;
}) {
  const color = tone === "bad" ? "#ef4444" : tone === "warn" ? "#f59e0b"
    : tone === "ok" ? "#22c55e" : undefined;
  return (
    <div className="kpi" title={title}>
      <div className="v" style={color ? { color } : undefined}>{v}</div>
      <div className="k">{k}</div>
    </div>
  );
}
