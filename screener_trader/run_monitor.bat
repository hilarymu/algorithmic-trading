@echo off
setlocal
set PYTHONIOENCODING=utf-8
set PROJ=%~dp0

:: Skip weekends
for /f %%d in ('powershell -NoProfile -Command "(Get-Date).DayOfWeek"') do set DOW=%%d
if /i "%DOW%"=="Saturday" exit /b 0
if /i "%DOW%"=="Sunday"   exit /b 0

:: Skip outside 9:25 AM - 4:05 PM (small buffer around market hours)
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format HHmm"') do set HHMM=%%t
if %HHMM% LSS 0925 exit /b 0
if %HHMM% GTR 1605 exit /b 0

set LOG=%PROJ%logs\monitor_%date:~10,4%%date:~4,2%%date:~7,2%.log
if not exist "%PROJ%logs" mkdir "%PROJ%logs"

echo [%date% %time%] monitor starting >> "%LOG%"
py -3 "%PROJ%monitor.py" >> "%LOG%" 2>&1
echo [%date% %time%] monitor done (exit %ERRORLEVEL%) >> "%LOG%"
