@echo off
cd /d "%~dp0"
set QT_PLUGIN_PATH=
set QT_QPA_PLATFORM_PLUGIN_PATH=%~dp0.venv\Lib\site-packages\PyQt5\Qt5\plugins\platforms
set PATH=%~dp0.venv\Lib\site-packages\PyQt5\Qt5\bin;%PATH%
".venv\Scripts\python.exe" main.py
pause
