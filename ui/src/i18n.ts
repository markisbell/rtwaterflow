import i18n from "i18next";
import { initReactI18next } from "react-i18next";

// Bilingual UI — German is the authoring language (Schlechtpunkt,
// Hochbehälter, Wasserzähler...), English is the fallback. Feature-prefixed
// keys, one inline resource file (blueprint convention).

const de = {
  app: {
    consumersShort: "Abnehmer",
    trenchKm: "{{km}} km Leitung",
    noNetwork: "kein Netz",
  },

  mbar: {
    file: "Datei",
    view: "Ansicht",
    layerHdr: "Kartenebene",
    basemapHdr: "Karte",
    sightHdr: "Sicht",
    tabsHdr: "Bereich",
    tabLive: "Live",
    tabStudio: "NetzStudio",
    segTruth: "Realität",
    segObserved: "Gemessen",
    segEst: "Schätzung",
    sightTruth: "Reale Systemsicht (Simulation)",
    sightObserved: "Nur was Wasserzähler und Drucksensoren liefern",
    sightEst:
      "Berechnete Betreibersicht (in M0 deaktiviert — der Wasser-Beobachter " +
      "folgt in einem späteren Meilenstein)",
    help: "Hilfe",
    manual: "Benutzerhandbuch",
    apiDocs: "API-Dokumentation (Swagger)",
    source: "Quellcode auf GitHub",
  },

  file: {
    save: "Szenario speichern…",
    savePrompt: "Name des Szenarios:",
    scenariosHdr: "Szenarien",
    none: "keine gespeicherten Szenarien",
    delete: "Szenario löschen",
  },

  rec: {
    record: "Aufzeichnung starten",
    recordStop: "Aufzeichnung beenden",
    steps: "Schritte",
    recChip: "REC {{n}}",
    recordings: "Aufzeichnungen",
    none: "keine gespeicherten Aufzeichnungen",
    delete: "Aufzeichnung löschen",
    exportDaysDots: "Tage exportieren…",
    exportTitle: "Tage exportieren",
    exportRunning: "Export läuft",
    expChip: "Export {{pct}} %",
    days: "Anzahl Tage",
    exportHint:
      "Simuliert ganze Tage des aktuellen Aufbaus so schnell wie möglich " +
      "offline und legt sie als Aufzeichnung (CSV-Paket) ab — quasistatisch " +
      "wie der Live-Betrieb.",
    exportStart: "Export starten",
    cancel: "Abbrechen",
    error: "Fehler",
  },

  menu: {
    placeHdr: "Hier platzieren",
    addConsumer: "Abnehmer (0,05 kg/s)",
    pin: "Details anheften",
    removeConsumer: "Abnehmer entfernen",
    placeMeter: "Wasserzähler setzen",
    removeMeter: "Wasserzähler entfernen",
    placeSensor: "Drucksensor setzen",
    removeSensor: "Drucksensor entfernen",
  },

  meas: {
    heading: "Messstellen",
    modeHdr: "Zähler-Modus",
    modeFull: "Live",
    modeStd: "Standard 15 min",
    modeFullTitle: "Jeder Messwert in jedem Simulationsschritt (Telemetrie)",
    modeStdTitle:
      "Lastgang-Zähler: 15-Minuten-Mittelwerte, leer bis das erste Fenster " +
      "schließt — ehrlicher Kaltstart",
    meters: "Wasserzähler",
    sensors: "Drucksensoren",
    none: "keine Messstellen — das Netz ist für den Betreiber unsichtbar",
    presetHdr: "Vorlagen",
    preset_all_consumers: "Alle Abnehmer",
    preset_plant_only: "Nur Einspeisung",
    preset_key_points: "Schlüsselstellen",
    preset_clear: "Alles entfernen",
    preset_all_consumersTitle: "Zähler an jedem Abnehmer",
    preset_plant_onlyTitle: "Nur die Leitwarte + Druck am Einspeiseknoten",
    preset_key_pointsTitle:
      "Einspeisung + Netzenden + Zähler am aktuell bekannten Schlechtpunkt",
    preset_clearTitle: "Alle Messgeräte entfernen (Blindflug)",
    stdHint:
      "Standard-Lastgang: Werte erscheinen erst mit dem ersten vollen " +
      "15-min-Fenster und bleiben Fenstermittel.",
    placeHint:
      "Einzelne Zähler/Sensoren: Rechtsklick auf Abnehmer oder Knoten in " +
      "der Karte.",
    strict:
      "Strict Mode: der Server liefert nur die Messwerte — die Realität " +
      "bleibt verborgen.",
  },

  wp: {
    heading: "Schlechtpunkt (Mindestdruck)",
    yTitle: "p min / bar",
    observed: "Schlechtpunkt (gemessen)",
    truth: "Schlechtpunkt (Realität)",
    blind: "blind — kein Messwert",
    blindSpot:
      "Blinder Fleck: der wahre Schlechtpunkt hat keinen Zähler — sichtbar " +
      "ist nur der schlechteste GEMESSENE Wert.",
    blindSpotNoMeter:
      "Blind: kein Druck-Messwert — der Schlechtpunkt ist unbekannt.",
    hint:
      "Der Knoten mit dem niedrigsten Versorgungsdruck. DVGW W 400-1: " +
      "mindestens 2,0 bar (Erdgeschoss) + 0,35 bar je Geschoss.",
  },

  pin: {
    demanded: "Bedarf (Soll)",
    delivered: "Geliefert",
    pressure: "Druck",
    feed: "Einspeisung",
    unpin: "Lösen",
    noData: "noch keine Live-Daten",
  },

  netz: {
    loading: "Lade Netzbibliothek…",
    failed: "Netzbibliothek konnte nicht geladen werden:",
    step1: "1 · Netz wählen",
    step3: "2 · Prüfen & starten",
    library: "Bibliothek",
    pickHint: "Links ein Netz wählen.",
    nodes: "{{count}} Knoten",
    ch_rural: "ländlich",
    ch_suburban: "Vorort",
    ch_urban: "städtisch",
    ch_abstract: "abstrakt",
    ch_tutorial: "Tutorial",
    own: "Eigene Netze",
    import: "Netz importieren…",
    importTitle:
      "Fünf-Dateien-Kontrakt hochladen: network_structure, pipes, consumers, " +
      "supply, environment (fünf .json-Dateien oder eine Bündel-Datei)",
    imported: "„{{name}}“ importiert.",
    importErr: "Import fehlgeschlagen:",
    importNeedFive:
      "Es fehlen Kontrakt-Dateien — erwartet werden network_structure, " +
      "pipes, consumers, supply und environment",
    kConsumers: "Abnehmer",
    kPipes: "Leitungslänge",
    kDemand: "Bedarf",
    kElevation: "Geländehöhe",
    kSupply: "Einspeisung",
    apply: "Übernehmen & Live",
  },

  layer: {
    pressure: "Druck",
    velocity: "Tempo",
    pressureTitle: "Versorgungsdruck je Knoten (DVGW-Ampel)",
    velocityTitle: "Strömungsgeschwindigkeit (Kapazität)",
  },

  map: {
    dark: "🌙 Dunkel",
    light: "☀ Hell",
    cbPressure: "Druck bar (rot < {{min}})",
    cbVelocity: "m/s",
  },

  tip: {
    trench: "Leitung {{from}} → {{to}}",
    consumer: "Abnehmer {{name}}",
    plant: "🏔️ {{name}}",
    prv: "Druckminderer {{name}}",
    station: "Pumpwerk {{name}}",
    tank: "Behälter {{name}}",
    node: "Netzknoten {{name}} ({{elev}} m ü. NN) — Rechtsklick: Messstellen",
  },

  pop: {
    trench: "Leitung {{from}} → {{to}}",
    mdot: "Massenstrom",
    velocity: "Geschwindigkeit",
    pressure: "Druck",
    demanded: "Bedarf",
    delivered: "geliefert",
    feed: "Einspeisung",
    length: "Länge",
    designDemand: "Anschlusswert",
    prvIn: "Eingang",
    prvOut: "Ausgang",
    prvSet: "Soll",
    prvAbnormal: "nicht druckmindernd — physikalisch unplausibel (M2 schließt)",
    unobserved: "keine Messung — unbekannt",
    noData: "noch keine Live-Daten",
    coldStart: "Standard-Zähler: warte auf das erste 15-min-Fenster",
    windowed: "15-min-Mittelwerte (Standard-Lastgang)",
  },

  hgl: {
    heading: "Drucklinie",
    target: "Pfad zum Abnehmer",
    targetWorst: "Schlechtpunkt (automatisch)",
    hgl: "Drucklinie (Gelände + Druckhöhe)",
    terrain: "Gelände",
    noData: "kein Pfad / noch keine Live-Daten",
    hint:
      "Der Abstand zwischen Drucklinie und Gelände IST der örtliche " +
      "Versorgungsdruck (10 m ≈ 1 bar). Am Druckminderer springt die " +
      "Linie sichtbar nach unten.",
  },

  tank: {
    heading: "Behälter & Pumpwerke",
    durchlauf: "Durchlaufbehälter",
    gegen: "Gegenbehälter",
    level: "Füllstand",
    volume: "Nutzinhalt",
    inflow: "füllt sich",
    outflow: "entleert sich",
    balanced: "ausgeglichen",
    buffer: "Reichweite",
    bufferTitle:
      "Stunden bis der Nutzinhalt beim aktuellen Bezug aufgebraucht ist",
    overflow: "Überlauf! Behälter voll — Zulauf läuft über",
    empty: "Behälter leer — Mindeststand erreicht",
    fireReserve:
      "Löschwasserreserve angebrochen ({{m3}} m³ vorgeschrieben)",
    fireBand: "Löschreserve",
    running: "läuft",
    stopped: "steht",
    cvClosed:
      "Rückschlagklappe zu — Gegendruck über Förderhöhe, Pumpe fördert nicht",
    modeAuto: "Auto",
    modeOn: "Ein",
    modeOff: "Aus",
    modeAutoTitle: "Zweipunktregelung nach Behälterstand (Regel entscheidet)",
    modeOnTitle: "Hand: Pumpe dauerhaft ein (an der Regel vorbei)",
    modeOffTitle: "Hand: Pumpe dauerhaft aus (an der Regel vorbei)",
    band: "Schaltband {{on}}–{{off}} m",
    noTanks: "keine Behälter in diesem Netz",
    hint:
      "Pumpen starten unter dem Einschalt- und stoppen über dem " +
      "Ausschaltstand (Zweipunktregelung, DVGW W 300-1). Die " +
      "Löschreserve liegt direkt über dem Mindeststand.",
  },

  live: {
    loadingNet: "Lade Netz…",
    failedNet: "Netz konnte nicht geladen werden:",
    day: "Tag {{day}} · {{time}}",
    netInfo: "{{name}} · {{consumers}} Abnehmer · WS: {{ws}}",
    play: "▶ Start",
    pause: "⏸ Pause",
    time: "Uhrzeit {{time}}",
    stepDur: "Takt",
    stepDurTitle: "Wanduhr-Sekunden pro Simulationsminute",
    dayLabel: "Tag",
    dayTitle: "Simulationstag wählen",
    notConverged: "⚠ nicht konvergiert",
    selectHint:
      "Klick auf Leitung, Abnehmer oder Einspeisung öffnet die Live-Werte. " +
      "Kartenebene über Ansicht oder den Umschalter rechts oben.",
    truthHidden: "Der Server verbirgt die Realität (Strict Mode) — Anzeige gemessener Werte.",
  },

  ov: {
    heading: "Übersicht",
    groundTruth: "Reale Systemsicht",
    observedCaption: "Messwerte (Wasserzähler)",
    feedIn: "Einspeisung",
    demand: "Bedarf",
    delivered: "Geliefert",
    demandMetered: "Bedarf (gemessen)",
    sourcePressure: "Druck Einspeisung",
    worstPoint: "Schlechtpunkt p min",
    solver: "Solver",
    solverOk: "ok",
    solverDegraded: "degradiert",
    solverFailed: "ausgefallen",
    solveTime: "Rechenzeit",
    coverage: "Messabdeckung",
    na: "n. v.",
    observedNote: "Aggregiert nur über bemessene Elemente — die Sicht des Betreibers.",
    estCaption: "Schätzung (in M0 deaktiviert)",
    estQuality: "Schätzgüte",
    estErrMdot: "max |Δṁ| an Messstellen",
    estErrDp: "max |Δp| an Messstellen",
    estAge: "Stand der Schätzung",
    estAgeNow: "aktuell (#{{seq}})",
    estAgeMin: "vor {{min}} min (#{{seq}})",
    estSolve: "Beobachter-Rechenzeit",
    estNote:
      "Zweites Netzmodell, angetrieben nur von Messwerten und " +
      "Erwartungsprofilen — folgt in einem späteren Meilenstein.",
  },
};

const en: typeof de = {
  app: {
    consumersShort: "consumers",
    trenchKm: "{{km}} km pipes",
    noNetwork: "no network",
  },

  mbar: {
    file: "File",
    view: "View",
    layerHdr: "Map layer",
    basemapHdr: "Basemap",
    sightHdr: "Perspective",
    tabsHdr: "Area",
    tabLive: "Live",
    tabStudio: "NetzStudio",
    segTruth: "Reality",
    segObserved: "Measured",
    segEst: "Estimated",
    sightTruth: "Ground-truth system view (simulation)",
    sightObserved: "Only what water meters and pressure sensors deliver",
    sightEst:
      "Calculated operator view (disabled in M0 — the water observer " +
      "arrives in a later milestone)",
    help: "Help",
    manual: "User manual (German)",
    apiDocs: "API documentation (Swagger)",
    source: "Source on GitHub",
  },

  file: {
    save: "Save scenario…",
    savePrompt: "Scenario name:",
    scenariosHdr: "Scenarios",
    none: "no saved scenarios",
    delete: "Delete scenario",
  },

  rec: {
    record: "Start recording",
    recordStop: "Stop recording",
    steps: "steps",
    recChip: "REC {{n}}",
    recordings: "Recordings",
    none: "no stored recordings",
    delete: "Delete recording",
    exportDaysDots: "Export days…",
    exportTitle: "Export days",
    exportRunning: "Export running",
    expChip: "Export {{pct}} %",
    days: "Number of days",
    exportHint:
      "Simulates whole days of the current setup offline, as fast as " +
      "possible, and stores them as a recording (CSV pack) — quasi-static " +
      "like the live loop.",
    exportStart: "Start export",
    cancel: "Cancel",
    error: "Error",
  },

  menu: {
    placeHdr: "Place here",
    addConsumer: "Consumer (0.05 kg/s)",
    pin: "Pin details",
    removeConsumer: "Remove consumer",
    placeMeter: "Install water meter",
    removeMeter: "Remove water meter",
    placeSensor: "Install pressure sensor",
    removeSensor: "Remove pressure sensor",
  },

  meas: {
    heading: "Measurement points",
    modeHdr: "Meter mode",
    modeFull: "Live",
    modeStd: "Standard 15 min",
    modeFullTitle: "Every reading at every simulation step (telemetry)",
    modeStdTitle:
      "Load-profile meters: 15-minute means, empty until the first window " +
      "closes — honest cold start",
    meters: "Water meters",
    sensors: "Pressure sensors",
    none: "no measurement points — the network is invisible to the operator",
    presetHdr: "Presets",
    preset_all_consumers: "All consumers",
    preset_plant_only: "Source only",
    preset_key_points: "Key points",
    preset_clear: "Remove all",
    preset_all_consumersTitle: "A meter at every consumer",
    preset_plant_onlyTitle: "Only the SCADA + pressure at the source node",
    preset_key_pointsTitle:
      "Source + network ends + a meter at the currently known worst point",
    preset_clearTitle: "Remove every device (flying blind)",
    stdHint:
      "Standard load profile: values appear only with the first complete " +
      "15-min window and stay window means.",
    placeHint:
      "Individual meters/sensors: right-click a consumer or node on the map.",
    strict:
      "Strict mode: the server serves only the measurements — reality stays " +
      "hidden.",
  },

  wp: {
    heading: "Worst point (minimum pressure)",
    yTitle: "p min / bar",
    observed: "Worst point (measured)",
    truth: "Worst point (reality)",
    blind: "blind — no reading",
    blindSpot:
      "Blind spot: the TRUE worst point carries no meter — only the worst " +
      "MEASURED value is visible.",
    blindSpotNoMeter:
      "Blind: no pressure reading — the worst point is unknown.",
    hint:
      "The node with the lowest service pressure. DVGW W 400-1: at least " +
      "2.0 bar (ground floor) + 0.35 bar per storey.",
  },

  pin: {
    demanded: "Demand (setpoint)",
    delivered: "Delivered",
    pressure: "Pressure",
    feed: "Feed",
    unpin: "Unpin",
    noData: "no live data yet",
  },

  netz: {
    loading: "Loading network library…",
    failed: "Failed to load the network library:",
    step1: "1 · Pick a network",
    step3: "2 · Check & start",
    library: "Library",
    pickHint: "Pick a network on the left.",
    nodes: "{{count}} nodes",
    ch_rural: "rural",
    ch_suburban: "suburban",
    ch_urban: "urban",
    ch_abstract: "abstract",
    ch_tutorial: "tutorial",
    own: "Own networks",
    import: "Import network…",
    importTitle:
      "Upload the five-file contract: network_structure, pipes, consumers, " +
      "supply, environment (five .json files or one bundle file)",
    imported: "Imported “{{name}}”.",
    importErr: "Import failed:",
    importNeedFive:
      "Contract files missing — expected network_structure, pipes, " +
      "consumers, supply and environment",
    kConsumers: "Consumers",
    kPipes: "Pipe length",
    kDemand: "Demand",
    kElevation: "Elevation",
    kSupply: "Supply",
    apply: "Apply & go live",
  },

  layer: {
    pressure: "Pressure",
    velocity: "Speed",
    pressureTitle: "Service pressure per node (DVGW traffic light)",
    velocityTitle: "Flow velocity (capacity)",
  },

  map: {
    dark: "🌙 Dark",
    light: "☀ Light",
    cbPressure: "Pressure bar (red < {{min}})",
    cbVelocity: "m/s",
  },

  tip: {
    trench: "Pipe {{from}} → {{to}}",
    consumer: "Consumer {{name}}",
    plant: "🏔️ {{name}}",
    prv: "Pressure-reducing valve {{name}}",
    station: "Pump station {{name}}",
    tank: "Tank {{name}}",
    node: "Network node {{name}} ({{elev}} m a.s.l.) — right-click: sensors",
  },

  pop: {
    trench: "Pipe {{from}} → {{to}}",
    mdot: "Mass flow",
    velocity: "Velocity",
    pressure: "Pressure",
    demanded: "Demand",
    delivered: "delivered",
    feed: "Feed",
    length: "Length",
    designDemand: "Design demand",
    prvIn: "Inlet",
    prvOut: "Outlet",
    prvSet: "set",
    prvAbnormal: "not reducing — physically implausible (M2 closes the valve)",
    unobserved: "no measurement — unknown",
    noData: "no live data yet",
    coldStart: "standard meter: waiting for the first 15-min window",
    windowed: "15-min means (standard load profile)",
  },

  hgl: {
    heading: "Hydraulic grade line",
    target: "Path to consumer",
    targetWorst: "Worst point (automatic)",
    hgl: "Grade line (terrain + pressure head)",
    terrain: "Terrain",
    noData: "no path / no live data yet",
    hint:
      "The gap between the grade line and the terrain IS the local " +
      "service pressure (10 m ≈ 1 bar). At the PRV the line visibly " +
      "steps down.",
  },

  tank: {
    heading: "Tanks & pump stations",
    durchlauf: "Flow-through tank",
    gegen: "Counter tank",
    level: "Level",
    volume: "Usable volume",
    inflow: "filling",
    outflow: "draining",
    balanced: "balanced",
    buffer: "Buffer time",
    bufferTitle:
      "Hours until the usable volume is gone at the current draw",
    overflow: "Overflow! Tank full — inflow spilling",
    empty: "Tank empty — minimum level reached",
    fireReserve:
      "Fire-fighting reserve breached ({{m3}} m³ required)",
    fireBand: "Fire reserve",
    running: "running",
    stopped: "stopped",
    cvClosed:
      "Check valve shut — head above shutoff lift, pump delivers nothing",
    modeAuto: "Auto",
    modeOn: "On",
    modeOff: "Off",
    modeAutoTitle: "Two-point tank-level control (the rule decides)",
    modeOnTitle: "Manual: pump forced on (past the rule)",
    modeOffTitle: "Manual: pump forced off (past the rule)",
    band: "Switching band {{on}}–{{off}} m",
    noTanks: "no tanks in this network",
    hint:
      "Pumps start below the on-level and stop above the off-level " +
      "(two-point control, DVGW W 300-1). The fire reserve sits right " +
      "above the minimum level.",
  },

  live: {
    loadingNet: "Loading network…",
    failedNet: "Failed to load the network:",
    day: "Day {{day}} · {{time}}",
    netInfo: "{{name}} · {{consumers}} consumers · WS: {{ws}}",
    play: "▶ Start",
    pause: "⏸ Pause",
    time: "Time {{time}}",
    stepDur: "Tick",
    stepDurTitle: "Wall-clock seconds per simulated minute",
    dayLabel: "Day",
    dayTitle: "Pick the simulation day",
    notConverged: "⚠ not converged",
    selectHint:
      "Click a pipe, consumer or source for live values. " +
      "Pick the map layer via View or the switch at the top right.",
    truthHidden: "The server withholds ground truth (strict mode) — showing measured values.",
  },

  ov: {
    heading: "Overview",
    groundTruth: "Ground-truth system view",
    observedCaption: "Measured values (water meters)",
    feedIn: "Feed",
    demand: "Demand",
    delivered: "Delivered",
    demandMetered: "Demand (metered)",
    sourcePressure: "Source pressure",
    worstPoint: "Worst point p min",
    solver: "Solver",
    solverOk: "ok",
    solverDegraded: "degraded",
    solverFailed: "failed",
    solveTime: "Solve time",
    coverage: "Meter coverage",
    na: "n/a",
    observedNote: "Aggregated over metered elements only — the operator's view.",
    estCaption: "Estimate (disabled in M0)",
    estQuality: "Estimate quality",
    estErrMdot: "max |Δṁ| at sensors",
    estErrDp: "max |Δp| at sensors",
    estAge: "Estimate age",
    estAgeNow: "current (#{{seq}})",
    estAgeMin: "{{min}} min ago (#{{seq}})",
    estSolve: "Observer solve time",
    estNote:
      "A second network model driven only by measurements and expected " +
      "profiles — arrives in a later milestone.",
  },
};

i18n.use(initReactI18next).init({
  resources: { de: { translation: de }, en: { translation: en } },
  lng: "de",
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

export default i18n;
