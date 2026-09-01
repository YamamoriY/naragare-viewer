@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Naragare Viewer
set "PYCMD=py -3"
%PYCMD% -c "" >nul 2>&1
if errorlevel 1 set "PYCMD=python"
%PYCMD% -c "" >nul 2>&1
if errorlevel 1 goto nopy
%PYCMD% server.py
if errorlevel 1 (
  echo.
  echo   [ERROR] See the message above.
  pause
)
exit /b

:nopy
echo.
type "docs\_python_not_found.txt"
echo.
pause
exit /b
