@echo off
chcp 65001 >nul
title Input Sender (Main PC)
cd /d "%~dp0"

echo ==============================
echo   Input Sender - Main PC
echo ==============================
echo.

echo [1/3] git fetch ...
git fetch
echo.

echo [2/3] git pull ...
git pull
echo.

echo [3/3] Installing dependencies ...
pip install pynput websockets pygame >nul 2>&1
echo.

echo [4/4] Starting sender ...
echo.

python sender\input_sender.py

echo.
echo Sender stopped. Press any key to close.
pause >nul
