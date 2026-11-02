@echo off
cd /d "%~dp0"
python nomad_server.py --app
pause
