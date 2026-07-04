@echo off
REM run_smoke.bat - run the Phase 0 acceptance gate (smoke_phase0.py).
REM Uses the main venv. Requires Mnemos up on MNEMOS_URL and network access.

set "REPO_ROOT=%~dp0.."
set "PYTHONIOENCODING=utf-8"
"%REPO_ROOT%\.venv\Scripts\python.exe" "%REPO_ROOT%\scripts\smoke_phase0.py"
