@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Naragare - Publish (port 8766)
rem このPCでは 8765 を GrapeZone が使っているので 8766 で動かす
set "NARAGARE_PORT=8766"
set "PYTHONIOENCODING=utf-8"
set "PYEXE=%APPDATA%\uv\python\cpython-3.12.14-windows-x86_64-none\python.exe"
if not exist "%PYEXE%" call "%~dp0tools\_findpy.bat"
"%PYEXE%" %PYARG% tools\publish.py
echo.
pause
