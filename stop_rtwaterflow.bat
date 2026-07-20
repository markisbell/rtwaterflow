@echo off
setlocal
rem ============================================================
rem  rtwaterflow Stopper: beendet Backend, UI und verwaiste
rem  Hintergrundprozesse (node/esbuild/python) aus DIESEM Repo.
rem  Ein parallel laufendes netzsim/rtpowerflow (Ports 8000/5173)
rem  bleibt unberuehrt.
rem ============================================================
cd /d "%~dp0"

echo === rtwaterflow Stopper ===

rem ---------- Serverfenster schliessen (samt Kindprozessen) ----------
taskkill /FI "WINDOWTITLE eq rtwaterflow Backend*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq rtwaterflow UI*" /T /F >nul 2>&1

rem ---------- Besitzer der rtwaterflow-Ports 8002/5175 beenden ----------
powershell -NoProfile -Command "foreach ($p in 8002,5175) { Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } }"

rem ---------- verwaiste Prozesse dieses Projekts beenden ----------
rem Erkennung ueber die KOMMANDOZEILE, nicht den Exe-Pfad: Vite laeuft als
rem "C:\Program Files\nodejs\node.exe <repo>\ui\...\vite.js" und das Backend
rem als relatives ".venv\Scripts\python.exe -m rtwaterflow.main" - beide
rem wuerden einem Exe-Pfad-Filter entgehen (auch Reste aus geloeschten
rem Agent-Worktrees und Server auf abgewanderten Ports werden so gefunden).
powershell -NoProfile -Command "$root = (Get-Location).Path; Get-CimInstance Win32_Process | Where-Object { ($_.Name -in 'node.exe','esbuild.exe','python.exe') -and ( $_.CommandLine -like ('*' + $root + '*') -or $_.CommandLine -like '*rtwaterflow.main*' ) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

rem ---------- Ergebnis pruefen ----------
powershell -NoProfile -Command "$busy = @(8002,5175 | Where-Object { Get-NetTCPConnection -LocalPort $_ -State Listen -ErrorAction SilentlyContinue }); if ($busy.Count) { Write-Host ('WARNUNG: Port(s) noch belegt: ' + ($busy -join ', ')) } else { Write-Host 'Alle rtwaterflow-Dienste beendet - Ports 8002/5175 sind frei.' }"
ping -n 4 127.0.0.1 >nul
endlocal
