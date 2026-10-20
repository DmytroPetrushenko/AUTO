@echo off
setlocal
cd /d "%~dp0"
echo Close AUTO before updating. Local data will be backed up before schema changes.
".venv\Scripts\python.exe" -c "from auto.runtime import RunningLock; from auto.db import db_path; g=RunningLock(db_path()); g.close()"
if errorlevel 1 goto failed
git diff --quiet
if errorlevel 1 goto failed
git diff --cached --quiet
if errorlevel 1 goto failed
git pull --ff-only
if errorlevel 1 goto failed
call install.cmd
exit /b %errorlevel%
:failed
echo Update stopped. Close AUTO, preserve local code changes and check Git connection.
exit /b 1
