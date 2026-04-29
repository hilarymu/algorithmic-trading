@echo off
setlocal
set PYTHONIOENCODING=utf-8

:: Project root is one level up from this scripts/ directory
pushd "%~dp0.."
set PROJ=%CD%

:: Log file
set LOG=%PROJ%\logs\options_loop_%date:~10,4%%date:~4,2%%date:~7,2%.log
if not exist "%PROJ%\logs" mkdir "%PROJ%\logs"

echo [%date% %time%] options loop starting >> "%LOG%"
py -3 "%PROJ%\options_main.py" --post-close >> "%LOG%" 2>&1
echo [%date% %time%] options loop done (exit %ERRORLEVEL%) >> "%LOG%"

popd
