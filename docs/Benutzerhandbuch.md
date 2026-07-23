# rtwaterflow — Benutzerhandbuch

rtwaterflow ist eine Lehr- und Forschungsplattform, die ein kommunales
**Trinkwassernetz in beschleunigter Echtzeit** simuliert: ein
Simulationsschritt entspricht einer Minute Netzzeit. Auf einer Karte werden
Drücke und Fließgeschwindigkeiten sichtbar; fallende Behälterstände,
Druckabfälle an Hausanschlüssen, trockenfallende Brunnen und die
DVGW-Regelverstöße in der Alarmzentrale sind die zentralen Lehreffekte.

Das Projekt ist der Wasser-Zwilling von
[rtheatflow](https://github.com/markisbell/rtheatflow) (Fernwärme) und nutzt
denselben Plattformkern (pandapipes 0.14.0, FastAPI, React/Leaflet). Die
Hydraulik ist gegen EPANET/WNTR kreuzvalidiert (siehe Abschnitt 13).

## 1. Schnellstart

**Windows (empfohlen):** `start_rtwaterflow.bat` startet Backend
(Port 8002) und Oberfläche (Port 5175) in zwei Konsolen und öffnet den
Browser. `stop_rtwaterflow.bat` beendet alles wieder.

**Docker Compose:** `docker compose up -d` startet Backend (8002),
Oberfläche (8082), InfluxDB (8088) und Grafana (3002).

Portschema der Geschwisterprojekte: netzsim 8000/5173 · rtheatflow
8001/5174 · **rtwaterflow 8002/5175**. Dieses Handbuch ist in der Anwendung
unter **`/manual`** eingebettet.

## 2. Das Netz

Ein Netzbündel besteht aus fünf JSON-Dateien:

* `network_structure.json` — Knoten mit **Geländehöhe** `elevation_m` (die
  hydrostatische Kernphysik: ~0,098 bar je Höhenmeter) und optionaler
  Quellenangabe (`attribution`) für Geodaten-Netze.
* `pipes.json` — Rohre mit Innendurchmesser und integraler Rauheit k; der
  Rohrkatalog leitet k aus Material (PE/PVC 0,1 · GGG/St/AZ 0,4 · GG 1,0 mm,
  GW 303-1) und die lichte Weite aus DN bzw. der PE-d-Reihe ab.
* `consumers.json` — Verbraucher mit Archetyp (Wohnen Stadt/Dorf, Milchvieh,
  Schwein, Schule, Bad …) und Größe; daraus baut die Nachfrage-Engine (M3)
  Tages-, Wochen-, Saison- und Wetterprofile. Bei Unterversorgung entnehmen
  sie druckabhängig weniger (PDA, M5), statt unphysikalisch negativer Drücke.
* `supply.json` — Einspeisungen (Festdruckknoten/Hochbehälter), **Behälter**
  (Füllstandsdynamik), **Pumpwerke** (Q-H-Kennlinie mit Zweipunktregelung),
  **Druckminderer** (Zonengrenzen) und **Brunnenfelder** (M6).
* `environment.json` — Zeithorizont, Lufttemperatur, Tagestypen, Trockenheit
  und Jahreszeit für die Nachfrage- und Wettereffekte.

Die mitgelieferten Netze wählt man in **NetzStudio**; siehe die Bibliothek
(Abschnitt 3) und die Szenarien-Rundgänge (Abschnitt 12).

## 3. Die Netzbibliothek

| Netz | Charakter | Lehrpunkt |
|---|---|---|
| Tutorial Hanglage | Tutorial | Hydrostatik; der Schlechtpunkt liegt am **höchsten** Abnehmer |
| Musterdorf | ländlich | zwei **Druckzonen** über einen Druckminderer; Pumpe + Hochbehälter; Hitzetag |
| Mustertal | ländlich | **Gegenbehälter** — ein Leitungsabschnitt kehrt über den Tag die Richtung um |
| Lauenau | ländlich | **Brunnen & Grundwasser**; die Dürrekaskade lässt Haushalte trockenfallen |
| Alpen | real (Geodaten) | echtes OSM/EU-DEM, flacher Niederrhein, eine Schwerkraftzone |
| Neubeuern | real (Geodaten) | echtes OSM/EU-DEM, hügeliges Inntal, **PRV-Druckzonen** über ~78 m Relief |
| Kevelaer | real (Geodaten) | Stadtnetz, ~540 Knoten — der Leistungs-Stresstest (< 100 ms/Takt) |

## 4. Die drei Sichten

| Sicht | Inhalt |
|---|---|
| Realität | die vollständige Physik (jeder Knotendruck, jede Geschwindigkeit) |
| Gemessen | nur was Wasserzähler, Drucksensoren und die Leitwarte wirklich liefern |
| Schätzung | der **Digital-Twin-Beobachter** (M7): ein zweites Netz, das allein aus Leitwarten- und Zählerwerten plus Nachfrage-Erwartung rekonstruiert wird |

Wasserzähler an Verbrauchern liefern Durchfluss und lokalen Druck;
Drucksensoren an Knoten liefern den Knotendruck; die Einspeisung ist als
Leitwarten-Telemetrie immer gemessen. Im **Standard-Modus** liefern Zähler
15-Minuten-Mittelwerte (ehrlicher Kaltstart: bis zum ersten
Fensterabschluss zeigt ein neuer Zähler nichts an).

Die **Schätzung** ist der Kern der Lehre zur Beobachtbarkeit: da pandapipes
keinen Zustandsschätzer hat, ist die geschätzte Sicht ein zweites,
unabhängig gerechnetes Netz, das nur mit **Operatorwissen** gespeist wird —
Quell-/Behälter-/Pumpen-Telemetrie und die erwartete (rauschfreie) Nachfrage
für ungezählte Verbraucher. Ein Rohrbruch oder eine Leckage an einem
**ungezählten** Knoten bleibt in der Schätzung unsichtbar; dieselbe Anomalie
an einem gezählten Verbraucher schlägt durch. Die Abweichung zwischen
Schätzung und Messung an den Sensoren ist das Innovationssignal.

## 5. Karte und Ebenen

Die Ebene **Druck** färbt Knoten und Verbraucher nach dem lokalen Druck
(rot unter 2,0 bar Mindestversorgungsdruck, grün im Sollband 4–6 bar,
gelb ab 8 bar Ruhedruck); die Ebene **Geschwindigkeit** färbt Rohre
(Warnung ab 2,0 m/s). **Fließrichtungspfeile** zeigen den Durchfluss (aus im
gemessenen Modus). Eigene Markierungen tragen Einspeisungen, Druckminderer
(⧗ mit Soll-/Ist-Druck), Pumpwerke (⚙ mit Lauflampe), Hochbehälter (🗼 mit
Füllstand) und Hydranten/Rohrbrüche (🚒/💥). Die **Drucklinie** zeichnet das
Höhen- und Druckprofil entlang eines Pfades; die Stufe eines Druckminderers
ist als Sprung sichtbar. Geodaten-Netze zeigen den OSM-/EU-DEM-Quellennachweis.

## 6. Die Alarmzentrale (Compliance)

Nach jeder Rechnung prüft eine Regelpass die DVGW-Vorgaben und meldet
typisierte Befunde mit deutscher Zitatstelle (`docs/COMPLIANCE.md`):
Mindestversorgungsdruck (W 400-1: 2,0 + 0,35 bar je Geschoss), Ruhedruck
(8 bar Warnung / 10 bar PN-10-Verletzung), Geschwindigkeit (> 2,0 m/s),
Stagnation/Selbstreinigung (Hygiene), Behälterreserve/-umschlag,
Löschwasser (W 405) und Wasserrecht (WHG). Befunde tragen eine Schwere
(🔴 Verletzung / 🟡 Warnung) und einen Nachhaltigkeitszähler; gesunde Netze
lösen keine Flut aus (per-Rohr-Hygienewarnungen werden zu **einem** Befund
gebündelt). Die gemessene und die geschätzte Sicht erfinden nie ein
falsches „alles in Ordnung".

## 7. Ereignisse: Löschwasser, Rohrbruch, Leckage

Im Bereich **Ereignisse** (M5) lassen sich ein **Hydrant** (W-405-Löschfall
mit Zielentnahme), ein **Rohrbruch** (Ausfluss über eine Bruchfläche) und
eine **Leckage** anlegen; die druckabhängige Entnahme (PDA) und die
Emitter-Physik teilen sich einen Fixpunkt. Hydranten und Brüche sind
Anlagen-Telemetrie (auch im strikten Modus auf dem Draht); Hintergrund-
Leckagen sind verborgene Realität — die nächtliche Mindestwassermenge (MNF),
die der Operator erst **erkennen** muss. Der Umschalter **PDA** macht den
Kontrast „warum druckabhängige Nachfrage" sichtbar.

## 8. Behälter, Pumpen, Brunnen

**Hochbehälter** sind Festdruckknoten mit füllstandsintegrierendem Regler
(Zweipunkt-Schaltband, Löschreserve, Überlauf/Leerlauf-Flags, Pufferzeit).
**Pumpwerke** folgen einer Q-H-Kennlinie; die Plattform findet den ehrlichen
Betriebspunkt und schließt bei Rückwärtslauf ein Rückschlagorgan (EPANET-
Semantik). **Brunnenfelder** (M6) sind die Rohwasserseite: ein linearer
Grundwasserleiter (saisonale Anreicherung, Dürrefaktor), Brunnen mit
Absenkung/Alterung (W 130) und Filterschutz, ein Wasserrecht (WHG §§ 8–10)
und die Energie in kWh/m³. Der **Reinwasserbehälter** (Kind `break`)
entkoppelt die Rohwasserseite hydraulisch vom Netz.

## 9. Umwelt und Nachfrage

Die Nachfrage-Engine (M3) baut aus Archetyp und Größe realistische
Verbrauchsprofile: Tagesgang, Wochenendverhalten, Saison, Temperatur-Kopplung
(z. B. Milchviehmelken, Freibad) und die nächtliche Freibad-Rückspülung
(DIN 19643). Der
Umschalter **Hitzetag** (Temperaturaufschlag + Trockenheit) verschiebt die
Spitze in den Abend und hebt die Nachtflüsse (der Trocken-Heiß-Effekt 2018).
Die synthetische Jahresstatistik liegt im W-410-Korridor (fd/fh).

## 10. Der NetzStudio-Editor

Der **Editor** (M8) zeichnet ein Wassernetz auf **echten OpenStreetMap-
Straßen**: Quelle, Knoten und Verbraucher setzen (die EU-DEM-Höhe wird pro
Klick geholt), Rohre straßengeführt ziehen (Einrasten + Dijkstra), aus dem
Rohrkatalog wählen. Live geprüft werden die drei **W-400-1-Lastfälle**
(LF1 Maximale Förderung — Geschwindigkeit ≤ 2,0 m/s; LF2 Spitzenstunde
Maximaltag — jeder Anschluss ≥ Geschossdruck; LF3 Löschfall W 405 —
Fließdruck ≥ 1,5 bar). Erst ein bestehendes Netz lässt sich in die Bibliothek
übernehmen. Der Offline-Bündelbauer (`tools/bundle_builder`) erzeugt dieselben
Netze reproduzierbar aus einem eingefrorenen OSM-/DGM-Schnappschuss.

## 11. Aufzeichnung und Export

Wie im Schwesterprojekt: ⏺ Aufzeichnung schreibt jeden veröffentlichten
Schritt als CSV-Paket; „Tage exportieren" spielt die aktuelle Konfiguration
offline durch — byte-kompatibel zur Live-Aufzeichnung (der Export startet
seine gleitenden Fenster und die Rohwasserseite ab Mitternacht neu).

## 12. Szenarien-Rundgänge (mit erwarteten Beobachtungen)

Jeder Rundgang nennt, was zu tun ist, **was man sieht** und **warum**.

**Tutorial Hanglage — Hydrostatik.** Starten und einen Tag laufen lassen.
*Beobachtung:* Die Einspeisung („Hochbehälter", 400 m) liegt bei 0,5 bar; am
54 m tiefer gelegenen Abnehmer stehen ~5,78 bar an. Der **Schlechtpunkt** ist
nicht der tiefste, sondern der **höchste** Abnehmer (~361 m). *Lehrpunkt:*
~1 bar je 10 m Höhe — die Geländehöhe, nicht die Entfernung, bestimmt den
Versorgungsdruck.

**Musterdorf — zwei Druckzonen.** Die Drucklinie zum tiefsten Abnehmer öffnen.
*Beobachtung:* Am Druckminderer Talstraße fällt der Druck als sichtbare Stufe
auf 2,8 bar; ohne ihn läge die Tiefzone über 10 bar (PN-10). Beide Zonen-
Mediane liegen im 4–6-bar-Band. Der Hitzetag verschiebt die Spitze auf ~19:45.
*Lehrpunkt:* Druckzonen schützen die tief gelegenen Rohre vor Überdruck.

**Mustertal — Gegenbehälter.** Den Abschnitt zum Wasserturm über den Tag
beobachten. *Beobachtung:* Der Durchfluss kehrt die Richtung um (~+5,7 kg/s
tags, ~−3,2 kg/s in der Schwachlast), während das Schaltband 1,2/3,4 m
gehalten wird. *Lehrpunkt:* Ein Gegenbehälter am Netzende füllt sich nachts
und stützt tagsüber die Spitze.

**Lauenau — Dürrekaskade.** Den Dürre-Schieber hochziehen und mehrere Tage
schnell durchlaufen. *Beobachtung:* Der Grundwasserstand fällt, die Brunnen-
kapazität sinkt unter die Spitze, der Reinwasserbehälter leert sich, die
Netzpumpe fällt bei Trockenlauf aus, der Hochbehälter entleert sich — die
Haushalte fallen trocken (PDA). Am Normaltag: 0,385 kWh/m³. *Lehrpunkt:* die
Kette Grundwasser → Brunnen → Behälter → Netz und wo sie reißt.

**Alpen — reales flaches Netz.** *Beobachtung:* 182 echte Knoten, Drücke über
den Tag ~2,8–5,9 bar, Spitzengeschwindigkeit ~0,87 m/s in der Morgenspitze
(weit unter der 2,0-m/s-Warnung), keine Verletzungen; auf der Karte der OSM-/
EU-DEM-Quellennachweis. *Lehrpunkt:* aus echten Straßen + DGM synthetisierte,
solide Netze.

**Neubeuern — hügelige Druckzonen.** *Beobachtung:* 215 echte Knoten über
~78 m Relief, sechs Druckminderer teilen die Oberstadt von der Talsohle;
Drücke 2,5–6,8 bar, keine Flut. Der Löschfall-Lastcheck (LF3) **scheitert** am
Schlechtpunkt. *Lehrpunkt:* echtes Relief erzwingt echte Druckzonen — und ein
Schwerkraftnetz liefert nicht überall die Löschwassermenge.

**Kevelaer — Stadtmaßstab.** *Beobachtung:* ~540 Knoten, in-band (4,3–5,2 bar),
warmer Takt ~37 ms (Beobachter aus). *Lehrpunkt:* die Rechenzeit skaliert mit
der Zahl der Solver-Aufrufe, nicht mit der Knotenzahl.

## 13. Validierung

Die Hydraulik ist gegen die Referenz kreuzvalidiert: die pandapipes-Lösung
(Darcy-Weisbach, `swamee-jain`) der EPANET-Standardnetze **Net1** und **Net3**
stimmt mit WNTRs EpanetSimulator auf < 0,001 bar (Druck) bzw. ~0,5 %
(Durchfluss) überein (`tests/validation/`). Behälter- und Hydrantendynamik
sind gegen EPANET-Orakel geprüft; die druckabhängige Nachfrage gegen WNTRs
Wagner-PDD.

## 14. Grenzen des Modells — bitte lesen

* **Quasistatisch:** Jeder Schritt ist eine stationäre Hydraulikrechnung;
  Druckstöße (DVGW W 303) werden nicht berechnet.
* **Druckminderer ohne Zustandsautomat:** `press_control` hält einen Sollwert,
  hat aber keine echte Ventilmechanik; Zonengrenzen sind statische Schnitte.
* **Keine Wassergüte:** Wasseralter/Stagnation erscheinen nur als Hygiene-
  Befund, nicht als Stofftransport.
* **Beobachter ist nicht deterministisch:** die Schätzung nutzt eine
  Wandzeit-Drosselung und ist beim Offline-Export abgeschaltet, damit der
  Export byte-stabil bleibt.

(Die M0-Grenzen — feste Entnahmen, keine Behälterdynamik — sind seit M2–M7
aufgehoben: PDA, Behälter/Pumpen, Brunnen und der Beobachter sind aktiv.)

## 15. Einstellungen

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
