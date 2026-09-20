@echo off
rem stealth-ops wrapper (Windows). Uses %STEALTH_OPS_PYTHON%, else backend\.venv, else python on PATH.
set "PY=%STEALTH_OPS_PYTHON%"
if "%PY%"=="" if exist "%~dp0..\..\backend\.venv\Scripts\python.exe" set "PY=%~dp0..\..\backend\.venv\Scripts\python.exe"
if "%PY%"=="" set "PY=python"
"%PY%" "%~dp0ops.py" %*
