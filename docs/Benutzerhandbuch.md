# rtwaterflow — Benutzerhandbuch

rtwaterflow ist eine Lehr- und Forschungsplattform, die ein kommunales
**Trinkwassernetz in beschleunigter Echtzeit** simuliert: ein
Simulationsschritt entspricht einer Minute Netzzeit. Auf einer Karte werden
Drücke und Fließgeschwindigkeiten sichtbar; fallende Behälterstände und
Druckabfälle an Hausanschlüssen sind die zentralen Lehreffekte.

Das Projekt ist der Wasser-Zwilling von
[rtheatflow](https://github.com/markisbell/rtheatflow) (Fernwärme) und nutzt
denselben Plattformkern (pandapipes 0.14.0, FastAPI, React/Leaflet).

## 1. Schnellstart

**Windows (empfohlen):** `start_rtwaterflow.bat` startet Backend
(Port 8002) und Oberfläche (Port 5175) in zwei Konsolen und öffnet den
Browser. `stop_rtwaterflow.bat` beendet alles wieder.

**Docker Compose:** `docker compose up -d` startet Backend (8002),
Oberfläche (8082), InfluxDB (8088) und Grafana (3002).

Portschema der Geschwisterprojekte: netzsim 8000/5173 · rtheatflow
8001/5174 · **rtwaterflow 8002/5175**.

## 2. Das Netz

Ein Netzbündel besteht aus fünf JSON-Dateien: `network_structure.json`
(Knoten mit **Geländehöhe** `elevation_m`), `pipes.json` (Rohre mit
Innendurchmesser und integraler Rauheit k), `consumers.json` (Verbraucher,
in M0 mit fester Entnahme), `supply.json` (Einspeisung — in M0 genau ein
Festdruckknoten, z. B. ein Hochbehälter-Wasserspiegel) und
`environment.json` (Zeithorizont und Umgebungsdaten).

Das mitgelieferte Netz **Tutorial Hanglage** reproduziert das offizielle
pandapipes-Höhenbeispiel: Einspeisung mit 0,5 bar auf 400 m Geländehöhe —
am 54 m tiefer gelegenen Abnehmer stehen ~5,78 bar an (≈ 1 bar je 10 m,
abzüglich Reibung). Der **Schlechtpunkt** (niedrigster Versorgungsdruck)
liegt am höchstgelegenen Abnehmer.

## 3. Die drei Sichten

| Sicht | Inhalt |
|---|---|
| Realität | die vollständige Physik (jeder Knotendruck, jede Geschwindigkeit) |
| Gemessen | nur was Wasserzähler, Drucksensoren und die Leitwarte wirklich liefern |
| Schätzung | in M0 deaktiviert (der Wasser-Beobachter folgt in einem späteren Meilenstein) |

Wasserzähler an Verbrauchern liefern Durchfluss und lokalen Druck;
Drucksensoren an Knoten liefern den Knotendruck; die Einspeisung ist als
Leitwarten-Telemetrie immer gemessen. Im **Standard-Modus** liefern Zähler
15-Minuten-Mittelwerte (ehrlicher Kaltstart: bis zum ersten
Fensterabschluss zeigt ein neuer Zähler nichts an).

## 4. Karte und Ebenen

Die Ebene **Druck** färbt Knoten und Verbraucher nach dem lokalen Druck
(rot unter 2,0 bar Mindestversorgungsdruck, grün im Sollband 4–6 bar,
gelb ab 8 bar Ruhedruck); die Ebene **Geschwindigkeit** färbt Rohre
(Warnung ab 2,0 m/s). Die Einspeisung trägt eine eigene Markierung — ihr
niedriger Behälterdruck ist kein Netzfehler.

## 5. Aufzeichnung und Export

Wie im Schwesterprojekt: ⏺ Aufzeichnung schreibt jeden veröffentlichten
Schritt als CSV-Paket; „Tage exportieren" spielt die aktuelle Konfiguration
offline durch — byte-kompatibel zur Live-Aufzeichnung.

## 6. Grenzen des Modells — bitte lesen

* **Quasistatisch:** Jeder Schritt ist eine stationäre Hydraulikrechnung;
  Druckstöße (DVGW W 303) werden nicht berechnet.
* **Feste Entnahmen (M0):** Verbraucher entnehmen ihren Sollwert unabhängig
  vom Druck; druckabhängige Minderversorgung (PDA) folgt in M5 der Roadmap.
* **Keine Behälterdynamik (M0):** Der Hochbehälter ist ein Festdruckknoten;
  Füllstandsdynamik, Pumpen und Brunnen folgen ab M2/M6.
* **Keine Wassergüte:** Wasseralter/Stagnation werden erst als spätere
  Auswertung ergänzt.

## 7. Einstellungen

| Variable | Standard | Bedeutung |
|---|---|---|
| RTWATERFLOW_PORT | 8002 | Backend-Port (Schwester-Schema) |
| RTWATERFLOW_DEFAULT_NETWORK | tutorial_hillside | Startnetz |
| RTWATERFLOW_STEP_INTERVAL_SECONDS | 1.0 | Wandzeit je Simulationsminute |
| RTWATERFLOW_STEPS_PER_DAY | 1440 | Schritte je Simulationstag |
| RTWATERFLOW_AUTOSTART | true | Engine beim Start laufen lassen |
| RTWATERFLOW_EXPOSE_GROUND_TRUTH | true | false = strikter Modus (nur Messwerte auf dem Draht) |
| RTWATERFLOW_SOLVER_ITER | 100 | Basis-Iterationen der Solver-Leiter |
| RTWATERFLOW_RECORD | false | Daueraufzeichnung |