@echo off
setlocal
set "APP_ROOT=%~dp0"
set "DATA_ROOT=%LOCALAPPDATA%\SamsungReceipt"
if not exist "%DATA_ROOT%" mkdir "%DATA_ROOT%"
set "SAMSUNG_RECEIPT_DATA_DIR=%DATA_ROOT%"

start "三星回单核验台" "%APP_ROOT%SamsungReceipt\SamsungReceipt.exe"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:5001"
endlocal
