@echo off
chcp 65001 >nul
title Input Receiver (Sub PC)
cd /d "%~dp0"

echo ==============================
echo   Input Receiver - Sub PC
echo ==============================
echo.

echo [0] Configuring firewall ...
netsh advfirewall firewall show rule name="InputDisplay-WS" >nul 2>&1 || (
    netsh advfirewall firewall add rule name="InputDisplay-WS" dir=in action=allow protocol=TCP localport=8888 >nul
    echo     Added rule: port 8888 (WebSocket)
)
netsh advfirewall firewall show rule name="InputDisplay-HTTP" >nul 2>&1 || (
    netsh advfirewall firewall add rule name="InputDisplay-HTTP" dir=in action=allow protocol=TCP localport=8081 >nul
    echo     Added rule: port 8081 (HTTP)
)
echo.

echo [1/4] git fetch ...
git fetch
echo.

echo [2/4] git pull ...
git pull
echo.

echo [3/4] Installing dependencies ...
pip install websockets >nul 2>&1
echo.

echo [4/4] Starting server ...
echo.
start http://localhost:8081/
python receiver\input_server.py --http-port 8081

echo.
echo Server stopped. Press any key to close.
pause >nul
