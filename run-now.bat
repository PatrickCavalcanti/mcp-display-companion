@echo off
REM Runs the service in the foreground, with a console, for debugging.
REM For normal use: scripts\install-service.ps1 (Scheduled Task).
cd /d "%~dp0"
python -u -m mcp_display_companion -c config.json
pause
