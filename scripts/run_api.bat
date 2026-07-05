@echo off
REM run_api.bat - start the read-only dashboard API over experiment.db (§11).
REM Serves http://127.0.0.1:8080. Poll-based, no websocket. Safe to run while
REM the loop is writing (opens the DB read-only).

set "REPO_ROOT=%~dp0.."
set "PYTHONIOENCODING=utf-8"
"%REPO_ROOT%\.venv\Scripts\python.exe" -m uvicorn dashboard.api:app --host 127.0.0.1 --port 8080
