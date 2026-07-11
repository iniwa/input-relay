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

:: Defaults; overridden below only by a validated numeric port (1-65535)
:: read from the real local sender_config.json. Corrupt/missing/malformed
:: config silently leaves these defaults untouched. Config is only ever
:: parsed as data (ConvertFrom-Json), never evaluated/executed.
set "HTTP_PORT=8082"
set "MONITOR_PORT=8083"
if exist "config\sender_config.json" (
    for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "try { $j = Get-Content -Raw -Path 'config\sender_config.json' | ConvertFrom-Json -ErrorAction Stop; $n = 0; if ([int]::TryParse([string]$j.http_port, [ref]$n) -and $n -ge 1 -and $n -le 65535) { Write-Output $n } } catch {}"`) do set "HTTP_PORT=%%P"
    for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "try { $j = Get-Content -Raw -Path 'config\sender_config.json' | ConvertFrom-Json -ErrorAction Stop; $n = 0; if ([int]::TryParse([string]$j.monitor_port, [ref]$n) -and $n -ge 1 -and $n -le 65535) { Write-Output $n } } catch {}"`) do set "MONITOR_PORT=%%P"
)
echo.

echo [4/4] Configuring firewall and starting sender ...
netsh advfirewall firewall delete rule name="InputSender GUI HTTP" >nul 2>&1
netsh advfirewall firewall add rule name="InputSender GUI HTTP" dir=in action=allow protocol=TCP localport=%HTTP_PORT% >nul 2>&1
netsh advfirewall firewall delete rule name="InputSender Monitor WS" >nul 2>&1
netsh advfirewall firewall add rule name="InputSender Monitor WS" dir=in action=allow protocol=TCP localport=%MONITOR_PORT% >nul 2>&1
echo.

start "" http://localhost:%HTTP_PORT%/
python sender\input_sender.py

echo.
echo Sender stopped. Press any key to close.
pause >nul
