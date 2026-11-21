@echo off
setlocal
set "CAMPUS_DATA_DIR=%~dp0data"
if not exist "%~dp0src-tauri\target\release\campus-job-tracker.exe" (
  echo Run npm run desktop:build first.
  pause
  exit /b 1
)
start "" "%~dp0src-tauri\target\release\campus-job-tracker.exe"
