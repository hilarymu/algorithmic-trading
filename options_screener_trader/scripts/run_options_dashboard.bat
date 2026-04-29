@echo off
setlocal
:: Project root is one level up from this scripts/ directory
pushd "%~dp0.."
set PROJ=%CD%

powershell.exe -ExecutionPolicy Bypass -WindowStyle Normal ^
  -File "%PROJ%\options_dashboard_server.ps1"

popd
