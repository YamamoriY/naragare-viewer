@echo off
chcp 65001 >nul
cd /d "%~dp0"
title First Time Setup
call "%~dp0tools\_findpy.bat"
if errorlevel 1 goto nopy
"%PYEXE%" %PYARG% tools\_msg.py setup
pause
"%PYEXE%" %PYARG% tools\build_forest.py
if errorlevel 1 goto err
"%PYEXE%" %PYARG% tools\seed_public_records.py
"%PYEXE%" %PYARG% tools\fetch_basemap.py
"%PYEXE%" %PYARG% tools\_msg.py setup_done
pause
exit /b

:err
"%PYEXE%" %PYARG% tools\_msg.py error
pause
exit /b

:nopy
echo.
type "docs\_python_not_found.txt"
echo.
pause
exit /b
