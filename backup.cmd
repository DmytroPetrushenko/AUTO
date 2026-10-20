@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" -m auto.cli backup %*
exit /b %errorlevel%
