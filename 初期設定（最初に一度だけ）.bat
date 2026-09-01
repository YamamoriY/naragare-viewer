@echo off
chcp 65001 >nul
cd /d "%~dp0"
title First Time Setup
set "PYCMD=py -3"
%PYCMD% -c "" >nul 2>&1
if errorlevel 1 set "PYCMD=python"
%PYCMD% -c "" >nul 2>&1
if errorlevel 1 goto nopy
%PYCMD% tools\_msg.py setup
if errorlevel 1 goto nopy
pause
%PYCMD% toolsuild_forest.py
if errorlevel 1 goto err
%PYCMD% tools\seed_public_records.py
%PYCMD% toolsetch_basemap.py
%PYCMD% tools\_msg.py setup_done
pause
exit /b

:err
%PYCMD% tools\_msg.py error
pause
exit /b

:nopy
echo.
type "docs\_python_not_found.txt"
echo.
pause
exit /b
