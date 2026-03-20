@echo off
chcp 65001 >nul

:: Require admin (games run as admin; pynput hooks need equal or higher privilege)
net session >nul 2>&1
if %errorLevel% neq 0 (
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

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

echo [3/4] Installing dependencies ...
pip install pynput websockets pygame >nul 2>&1
echo.

echo [4/4] Configuring firewall and starting sender ...
netsh advfirewall firewall delete rule name="InputSender GUI HTTP" >nul 2>&1
netsh advfirewall firewall add rule name="InputSender GUI HTTP" dir=in action=allow protocol=TCP localport=8082 >nul 2>&1
netsh advfirewall firewall delete rule name="InputSender Monitor WS" >nul 2>&1
netsh advfirewall firewall add rule name="InputSender Monitor WS" dir=in action=allow protocol=TCP localport=8083 >nul 2>&1
echo.

start "" http://localhost:8082/
python sender\input_sender.py

echo.
echo Sender stopped. Press any key to close.
pause >nul
