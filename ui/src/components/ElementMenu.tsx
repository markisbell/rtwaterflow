import { useEffect } from "react";
import { useTranslation } from "react-i18next";

/** What was right-clicked on the map. Every target sits at a network node,
 *  so placement actions are always offered. */
export interface MenuTarget {
  kind: "node" | "consumer" | "producer";
  id: number | string;
  name: string;
  node: string;
  x: number; // viewport coordinates of the click
  y: number;
  // element context for the remove/config labels
  consumerKind?: "consumer";
  producerKind?: "slack";
}

export type MenuAction =
  | { type: "addConsumer"; mdot?: number }
  | { type: "removeConsumer" }
  // sensor placement
  | { type: "placeMeter" }
  | { type: "removeMeter" }
  | { type: "placeNodeSensor" }
  | { type: "removeNodeSensor" };

/** Context menu on a clicked map element: element-specific actions first
 *  (pin details, meter/sensor, remove), then the placement items. The M2+
 *  water assets (tanks, pump stations, hydrants) re-grow their pages here. */
export default function ElementMenu({
  target, onAction, onPin, onClose, metered, nodeSensored,
}: {
  target: MenuTarget;
  onAction: (a: MenuAction) => void;
  onPin: () => void;
  onClose: () => void;
  /** does this consumer already carry a water meter? */
  metered?: boolean;
  /** does this element's node already carry a pressure sensor? */
  nodeSensored?: boolean;
}) {
  const { t } = useTranslation();
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [onClose]);

  const x = Math.max(4, Math.min(target.x, window.innerWidth - 250));
  const y = Math.max(4, Math.min(target.y, window.innerHeight - 320));
  const item = (key: string, label: string, fn: () => void, close = true) => (
    <button key={key} className="menu-item"
            onClick={() => { fn(); if (close) onClose(); }}>{label}</button>
  );
  const act = (a: MenuAction) => () => onAction(a);

  const specific: JSX.Element[] = [
    item("pin", `📌 ${t("menu.pin")}`, onPin),
  ];
  if (target.kind === "consumer") {
    // water meter (Wasserzähler) at the consumer
    specific.push(metered
      ? item("rmM", `📟 ${t("menu.removeMeter")}`,
             act({ type: "removeMeter" }))
      : item("addM", `📟 ${t("menu.placeMeter")}`,
             act({ type: "placeMeter" })));
    specific.push(item("rmC", `🗑️ ${t("menu.removeConsumer")}`,
                       act({ type: "removeConsumer" })));
  } else if (target.kind === "node" || target.kind === "producer") {
    // pressure sensor at the node
    specific.push(nodeSensored
      ? item("rmS", `🌡️ ${t("menu.removeSensor")}`,
             act({ type: "removeNodeSensor" }))
      : item("addS", `🌡️ ${t("menu.placeSensor")}`,
             act({ type: "placeNodeSensor" })));
  }
  const placement = [
    <div key="hdr-place" className="menu-hdr">{t("menu.placeHdr")}</div>,
    item("cons", `🏠 ${t("menu.addConsumer")}`,
         act({ type: "addConsumer", mdot: 0.05 })),
  ];
  const body = [...specific, ...placement];

  return (
    <>
      <div className="menu-overlay" onClick={onClose}
           onContextMenu={(e) => { e.preventDefault(); onClose(); }} />
      <div className="el-menu" style={{ left: x, top: y }}>
        <div className="menu-title">{target.name}</div>
        {body}
      </div>
    </>
  );
}
