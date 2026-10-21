@echo off
cd /d "%~dp0"
set ARABIC_RELAY_DATA_DIR=%~dp0.local
.venv\Scripts\python -m arabic_relay --browser
pause
