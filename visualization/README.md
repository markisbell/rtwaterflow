# Visualization stack (InfluxDB + Grafana)

`docker compose up -d` starts the full stack; the collector polls
`GET /state` every 0.5 s, dedupes on `(day, step)` and writes the hydraulic
wire into InfluxDB bucket `waterflow` (org `rtwaterflow`).

Grafana: http://localhost:3002 (admin / rtwaterflow-admin), dashboard
"rtwaterflow — Realtime Drinking Water" (file-provisioned from
`grafana/dashboards/rtwaterflow.json`): min service pressure (Schlechtpunkt)
vs the 2.0 bar DVGW threshold, feed/demand/delivered mass flow, per-consumer
pressures, solve time, solver status.

InfluxDB: http://localhost:8088 (dev credentials in docker-compose.yml —
change for anything public).