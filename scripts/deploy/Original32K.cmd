@echo off
powershell.exe -NoProfile -File "%~dp0Deploy.ps1" -Mode Original32K
set "result=%ERRORLEVEL%"
echo.
pause
exit /b %result%
