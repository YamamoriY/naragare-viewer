@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Ortho Ingest
set "PYCMD=py -3"
%PYCMD% -c "" >nul 2>&1
if errorlevel 1 set "PYCMD=python"
%PYCMD% -c "" >nul 2>&1
if errorlevel 1 goto nopy
%PYCMD% tools\_msg.py ingest
if errorlevel 1 goto nopy
pause
%PYCMD% tools\ingest_ortho.py %*
echo.
pause
exit /b

:nopy
echo.
type "docs\_python_not_found.txt"
echo.
pause
exit /b
