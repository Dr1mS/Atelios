@echo off
REM run_loop.bat - start the Atelios continuous loop (loop.py) with the main venv.
REM Refuses to start unless MIND+qwen3.5:9b, AUX+embed model, and Mnemos health
REM are all up (boot-check A6). Ctrl+C stops cleanly.

set "REPO_ROOT=%~dp0.."
set "PYTHONIOENCODING=utf-8"
"%REPO_ROOT%\.venv\Scripts\python.exe" -m atelios.loop
