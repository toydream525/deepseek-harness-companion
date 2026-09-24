@echo off
powershell.exe -NoProfile -File "%~dp0Deploy.ps1" -Mode UncensoredDirect32K
set "result=%ERRORLEVEL%"
echo.
pause
exit /b %result%
