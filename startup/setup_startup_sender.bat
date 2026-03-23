@echo off
chcp 65001 > nul
echo [INFO] Registering start_sender.bat in Windows Startup...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$lnk = $ws.CreateShortcut([Environment]::GetFolderPath('Startup') + '\InputSender MainPC.lnk');" ^
  "$lnk.TargetPath = '%~dp0..\start_sender.bat';" ^
  "$lnk.WorkingDirectory = '%~dp0..';" ^
  "$lnk.Save();"

if %errorlevel% equ 0 (
    echo [OK] Shortcut created. start_sender.bat will auto-start at next login.
) else (
    echo [ERROR] Failed to create shortcut.
)
pause
