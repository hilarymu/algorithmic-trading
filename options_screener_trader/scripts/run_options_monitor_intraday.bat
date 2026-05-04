@echo off
setlocal
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1

:: Project root is one level up from this scripts/ directory
pushd "%~dp0.."
set PROJ=%CD%

:: Log file
set LOG=%PROJ%\logs\options_intraday_%date:~10,4%%date:~4,2%%date:~7,2%.log
if not exist "%PROJ%\logs" mkdir "%PROJ%\logs"

echo [%date% %time%] intraday monitor starting >> "%LOG%"
py -3 "%PROJ%\options_main.py" --intraday >> "%LOG%" 2>&1
echo [%date% %time%] intraday monitor done (exit %ERRORLEVEL%) >> "%LOG%"

popd
