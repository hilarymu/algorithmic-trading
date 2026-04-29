@echo off
setlocal
set PYTHONIOENCODING=utf-8
set PROJ=%~dp0
set LOG=%PROJ%logs\rsi_loop_%date:~10,4%%date:~4,2%%date:~7,2%.log

if not exist "%PROJ%logs" mkdir "%PROJ%logs"

echo [%date% %time%] RSI loop starting >> "%LOG%"
py -3 "%PROJ%rsi_loop\rsi_main.py" --no-screener >> "%LOG%" 2>&1
echo [%date% %time%] RSI loop done (exit %ERRORLEVEL%) >> "%LOG%"
