@echo off
rem ------------------------------------------------------------------
rem  Find a Python to run.
rem  Prefer the copy bundled in this folder (python\python.exe) so that
rem  the PC needs no installation and no administrator rights.
rem  Sets PYEXE and PYARG for the caller. Returns 1 when nothing is found.
rem ------------------------------------------------------------------
set "PYEXE="
set "PYARG="
if exist "%~dp0..\python\python.exe" (
  set "PYEXE=%~dp0..\python\python.exe"
  goto ok
)
py -3 -c "" >nul 2>&1
if not errorlevel 1 (
  set "PYEXE=py"
  set "PYARG=-3"
  goto ok
)
python -c "" >nul 2>&1
if not errorlevel 1 (
  set "PYEXE=python"
  goto ok
)
exit /b 1
:ok
exit /b 0
