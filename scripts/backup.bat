@echo off
REM backup.bat - timestamped backup of the experiment state (ATELIOS_BUILD.md §14).
REM Zips experiment.db + sandbox/tools/ into %ATELIOS_BACKUP_DIR%, 30-day retention.
REM The Mnemos tenant-atelios DB lives in the Mnemos repo and is backed up there;
REM this script covers what THIS repo owns. Run manually anytime, or via Task
REM Scheduler every 6h (see README).
REM
REM Requires: %ATELIOS_BACKUP_DIR% set (falls back to D:\backups\atelios).

setlocal enabledelayedexpansion

set "REPO_ROOT=%~dp0.."
if "%ATELIOS_BACKUP_DIR%"=="" set "ATELIOS_BACKUP_DIR=D:\backups\atelios"

if not exist "%ATELIOS_BACKUP_DIR%" mkdir "%ATELIOS_BACKUP_DIR%"

REM Timestamp YYYYMMDD_HHMMSS (locale-independent via wmic).
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"

set "OUT=%ATELIOS_BACKUP_DIR%\atelios_%TS%.zip"

echo Backing up to %OUT%
powershell -NoProfile -Command ^
  "$items = @(); ^
   if (Test-Path '%REPO_ROOT%\experiment.db') { $items += '%REPO_ROOT%\experiment.db' }; ^
   if (Test-Path '%REPO_ROOT%\sandbox\tools') { $items += '%REPO_ROOT%\sandbox\tools' }; ^
   if ($items.Count -gt 0) { Compress-Archive -Path $items -DestinationPath '%OUT%' -Force } ^
   else { Write-Host 'Nothing to back up yet.' }"

REM Retention: delete backups older than 30 days.
powershell -NoProfile -Command ^
  "Get-ChildItem '%ATELIOS_BACKUP_DIR%\atelios_*.zip' -ErrorAction SilentlyContinue | ^
   Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-30) } | Remove-Item -Force"

echo Done.
endlocal
