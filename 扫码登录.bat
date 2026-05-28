@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m native_drama_uploader.cli login
pause
