@echo off
setlocal
set PYTHONIOENCODING=utf-8
set PROJ=%~dp0
set LOG=%PROJ%logs\screener_%date:~10,4%%date:~4,2%%date:~7,2%.log

if not exist "%PROJ%logs" mkdir "%PROJ%logs"

echo [%date% %time%] screener starting >> "%LOG%"
py -3 "%PROJ%screener.py" >> "%LOG%" 2>&1
echo [%date% %time%] screener done (exit %ERRORLEVEL%) >> "%LOG%"
