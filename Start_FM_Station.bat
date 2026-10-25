@echo off
title FM // 48KHZ BROADCAST CONSOLE
cd /d "%~dp0aether-fm\backend"

echo ========================================================
echo   FM // BROADCAST ENGINE STARTING
echo   Audio Vault : E:\FM_Audio_Vault (500 GB Cap)
echo   Studio UI   : http://localhost:8000/studio
echo ========================================================
echo.

:: Open the PC Broadcaster Studio UI in the default browser after 3 seconds
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8000/studio"

:: Run the FM Broadcast Server
".\.venv\Scripts\python.exe" main.py
pause
