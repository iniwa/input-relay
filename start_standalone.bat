@echo off
chcp 65001 >nul
title Input Display (Standalone)
cd /d "%~dp0"

echo ==============================
echo   Input Display - Standalone
echo ==============================
echo.

echo [1/4] git fetch ...
git fetch
echo.

echo [2/4] git pull ...
git pull
echo.

echo [3/4] Installing dependencies ...
pip install websockets pynput >nul 2>&1
echo.

echo [4/4] Starting server (standalone) ...
echo.
start http://localhost:8081/
python receiver\input_server.py --http-port 8081 --standalone

echo.
echo Server stopped. Press any key to close.
pause >nul
