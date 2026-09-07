@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Naragare - Publish
call "%~dp0tools\_findpy.bat"
if errorlevel 1 goto nopy
"%PYEXE%" %PYARG% tools\publish.py
echo.
pause
exit /b

:nopy
echo.
type "docs\_python_not_found.txt"
echo.
pause
exit /b
