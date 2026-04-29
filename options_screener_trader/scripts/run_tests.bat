@echo off
setlocal
set PYTHONIOENCODING=utf-8

:: Project root is one level up from this scripts/ directory
pushd "%~dp0.."
set PROJ=%CD%

echo.
echo ============================================================
echo  Options Screener Trader -- Unit Tests
echo ============================================================
echo.

py -3 -m pytest "%PROJ%\tests" -v

echo.
echo Done. Exit code: %ERRORLEVEL%

popd
