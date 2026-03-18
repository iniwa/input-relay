@echo off
chcp 65001 >nul
title Input Receiver (Sub PC)
cd /d "%~dp0"

echo ==============================
echo   Input Receiver - Sub PC
echo ==============================
echo.

echo [1/3] git fetch ...
git fetch
echo.

echo [2/3] git pull ...
git pull
echo.

echo [3/3] Starting server ...
echo.
start http://localhost:8080/
python receiver\input_server.py

echo.
echo Server stopped. Press any key to close.
pause >nul
