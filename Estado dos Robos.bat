@echo off
title Estado dos Robos
cd /d "%~dp0"

echo ============================================================
echo   Estado dos Robos
echo ============================================================
echo.

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m scripts.estado_dos_robos
) else (
  python -m scripts.estado_dos_robos
)

echo.
pause
