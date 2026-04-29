@echo off
setlocal
set PYTHONIOENCODING=utf-8
set PROJ=%~dp0
set LOG=%PROJ%logs\executor_%date:~10,4%%date:~4,2%%date:~7,2%.log

if not exist "%PROJ%logs" mkdir "%PROJ%logs"

echo [%date% %time%] executor starting >> "%LOG%"
py -3 "%PROJ%entry_executor.py" >> "%LOG%" 2>&1
echo [%date% %time%] executor done (exit %ERRORLEVEL%) >> "%LOG%"
