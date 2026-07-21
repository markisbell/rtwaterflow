# Compliance-Katalog (M4)

Jede Prüfung des Regelwerks-Passes (`src/rtwaterflow/compliance/engine.py`)
mit ihrer Regelquelle und dem TF-Abschnitt (`../TECHNICAL_FOUNDATIONS.md`).
Die Findings erscheinen pro Frame auf dem Wire (`findings`, Wahrheits-Layer
— im Strict Mode entfernt), unter `GET /findings` und in `findings.csv`
jeder Aufzeichnung.

| check | Regel | TF | Schwellen | Schwere |
|---|---|---|---|---|
| `p_min` | DVGW W 400-1 (Mindestversorgungsdruck am Hausanschluss) | §2 | 2,0 bar (EG) + 0,35 bar je Geschoss; Kurzzeitband 0,5 bar | Warnung im Kurzzeitband, Verletzung darunter oder ≥ 1 h anhaltend |
| `p_rest` | DVGW W 400-1 (max. Ruhedruck / PN 10) | §2 | 8 bar Richtwert **nur an Verbraucherknoten**, 10 bar Bauteilgrenze (alle Knoten) | Warnung > 8 bar, Verletzung > 10 bar |
| `v_max` | DVGW W 400-1 (max. Fließgeschwindigkeit) | §2 | 2,0 m/s Normalbetrieb (2,5 m/s nur Brandfall — M5) | Warnung kurzzeitig, Verletzung ≥ 1 h anhaltend |
| `stagnation` (Hygiene) | DVGW W 400-1 (Hygiene-Minimum) | §2 | Stundenmittel ≥ 0,005 m/s je Leitung | Warnung (braucht 1 h Historie; ausgesetzt für Leitungen an der Druckseite eines ausgeschalteten Pumpwerks) |
| `stagnation` (Selbstreinigung) | DVGW W 400-1 (Selbstreinigung) | §2 | Tagesspitze ≥ 0,3 m/s | **eine** aggregierte Warnung „N Leitungen ohne Selbstreinigung" (statt einer je Leitung; braucht 1 Tag Historie) |
| `tank_reserve` | DVGW W 405 / W 300-1 (Löschwasserreserve) | §2, §4 | Nutzvolumen ≥ Löschreserve | Verletzung |
| `tank_empty` | DVGW W 300-1 (Mindeststand) | §4 | Behälter am Mindeststand und liefernd | Verletzung |
| `tank_overflow` | DVGW W 300-1 (Überlauf) | §4 | Überlauf aktiv | Warnung |
| `tank_turnover` | DVGW W 300-1 (Wasseraustausch) | §4 | Tagesmittel Nutzvolumen / Tagesmittel |Austausch| ≤ 24 h | Warnung (braucht 1 Tag Historie; auch bei ~null Austausch) |
| `fire_flow` | DVGW W 405 (Löschwasserentnahme) | §2 | Fließdruck am ziehenden Hydranten ≥ 1,5 bar; Lieferung ≥ 90 % des Sollstroms | Verletzung (M5 Emitter) |
| `water_right` | WHG §§8–10 (Entnahmeerlaubnis) | §5 | Tages-/Jahresmenge ≤ Wasserrecht | Warnung (Tag) / Verletzung (Jahr) — **Compliance, kein hydraulischer Ausfall** (M6) |
| `well_ageing` | DVGW W 130 (Brunnenalterung) | §5 | spez. Ergiebigkeit-Verlust ≤ 10 % | Warnung (M6) |
| `solver` | Modellhinweis (kein Regelwerk) | §8 | Reibungs-Fallback / Betriebspunkt nicht konvergiert / Regelprüfung ausgefallen / PDA nicht konvergiert | Info |

## Geltungsbereich & Ehrlichkeit (M4-Review)

- **`p_rest` nur an Verbraucherknoten** für die 8-bar-Warnung: eine
  Pumpwerks-Druckseite bzw. der Fuß einer Steigleitung fährt im Betrieb
  regulär höher (Musterdorf-Knoten `ws` ~8,6 bar) — das ist kein Ruhedruck,
  sondern Betriebsdruck einer Transportleitung. Die 10-bar-Verletzung
  (PN-10-Bauteilgrenze) gilt weiter für **jeden** Knoten, mit angepasstem
  Text an Transportknoten.
- **Selbstreinigung aggregiert**: fire-capable dimensionierte Landnetze
  haben viele Zweigleitungen dauerhaft unter 0,3 m/s Tagesspitze. Statt
  einer Meldung je Leitung (≈ 28/Tick auf dem gesunden Musterdorf) gibt es
  **eine** Sammelmeldung mit Leitungsliste — die gesäten Anomalien heben
  sich so vom grünen Grundzustand ab.
- **Hygiene-Stagnation ausgesetzt an Off-Pumpwerk-Steigleitungen**: eine
  Steigleitung wird bei jedem Pumpzyklus gespült; ihre Stunden ohne
  Durchfluss sind kein Hygienemangel.
- **Laufzeit-Verbraucher** (POST /consumer) werden mit EG-Mindestdruck
  (2,0 bar) registriert — nie stumm von der p_min-Prüfung ausgenommen.
- **Messsicht & Strict Mode**: Findings stammen aus der Wahrheitsschicht.
  In der Messsicht und im Strict Mode zeigt die Alarmzentrale einen
  Ehrlichkeits-Hinweis statt Alarme — die Messwert-Alarme folgen mit dem
  Beobachter (M7).
- **Export-Kaltstart**: `BulkExporter.prepare_replay` setzt die
  Rolling-Fenster zurück. Ein Export eines Mitten-in-der-Session-Tages
  startet seine Anhalte-/Stagnations-/Austausch-Uhren bei null — seine
  `findings.csv` entspricht daher nicht dem Live-Alarmbild desselben Tages
  (bewusst: der Export ist ein deterministischer Replay ab Mitternacht).
  Ebenso setzt der Replay die Rohwasserseite zurück (voller Grundwasser-
  stand), sodass `water_right`/`well_ageing`-Meldungen eines Export-Tages
  nicht dem gealterten Live-Zustand entsprechen.

Noch nicht prüfbar (kommen mit späteren Meilensteinen, Roadmap §4.9):
Verluste qVR/ILI/MNF-Trend als Kennzahl (M5 liefert die Leckage-Emitter;
die qVR-Klassifizierung folgt), W 303-Druckstoß-Hinweis bei Pumpenausfall
(Hinweis, nicht gerechnet), Wasseralter-Proxy (M9 Post-Processing).
