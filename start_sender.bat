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

:: Derive the per-user persistent settings root using the same helper as the
:: Python sender. The later load copies legacy settings only when no primary
:: or recoverable backup exists.
for /f "usebackq delims=" %%P in (`python -c "from input_common.persistent_config import get_config_root; print(get_config_root())"`) do set "PERSISTENT_CONFIG=%%P"

:: Defaults; overridden below only by a validated numeric port (1-65535)
:: read from the persistent sender_config.json. The same helper restores the
:: newest valid backup when the primary is corrupt or missing. Invalid/missing
:: port values leave these defaults untouched; config is parsed only as data.
set "HTTP_PORT=8082"
set "MONITOR_PORT=8083"
if defined PERSISTENT_CONFIG (
    for /f "usebackq delims=" %%P in (`python -c "from pathlib import Path; import os; from input_common.persistent_config import load_object_json; cfg = load_object_json(Path(os.environ['PERSISTENT_CONFIG']) / 'sender_config.json', {}, legacy_config_dir=Path('config')); v = cfg.get('http_port'); s = str(v).strip() if type(v) is not bool else ''; print(s if s.isascii() and s.isdigit() and int(s) in range(1, 65536) else '')"`) do set "HTTP_PORT=%%P"
    for /f "usebackq delims=" %%P in (`python -c "from pathlib import Path; import os; from input_common.persistent_config import load_object_json; cfg = load_object_json(Path(os.environ['PERSISTENT_CONFIG']) / 'sender_config.json', {}, legacy_config_dir=Path('config')); v = cfg.get('monitor_port'); s = str(v).strip() if type(v) is not bool else ''; print(s if s.isascii() and s.isdigit() and int(s) in range(1, 65536) else '')"`) do set "MONITOR_PORT=%%P"
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
