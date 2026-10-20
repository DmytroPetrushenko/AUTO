@echo off
setlocal
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: restore.cmd "C:\path\to\backup-folder"
  exit /b 1
)
echo Close AUTO first. Current database and attachments will be replaced.
set "AUTO_RESTORE_CONFIRM="
set /p "AUTO_RESTORE_CONFIRM=Type RESTORE to confirm: "
if not "%AUTO_RESTORE_CONFIRM%"=="RESTORE" exit /b 1
".venv\Scripts\python.exe" -m auto.cli restore "%~1" --confirm
exit /b %errorlevel%
