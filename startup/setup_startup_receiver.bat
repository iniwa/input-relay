@echo off
chcp 65001 > nul
echo [INFO] Registering start_receiver.bat in Windows Startup...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Startup') + '\InputReceiver SubPC.lnk');" ^
  "$lnk.TargetPath = '%~dp0..\start_receiver.bat';" ^
  "$lnk.WorkingDirectory = '%~dp0..';" ^
  "$lnk.Save();"

if %errorlevel% equ 0 (
    echo [OK] Shortcut created. start_receiver.bat will auto-start at next login.
) else (
    echo [ERROR] Failed to create shortcut.
)
pause
