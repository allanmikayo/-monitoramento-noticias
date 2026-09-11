@echo off
title Limpeza Pontual
cd /d "%~dp0"

echo ============================================================
echo   Limpeza Pontual
echo ============================================================
echo.

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m scripts.limpeza_pontual
) else (
  python -m scripts.limpeza_pontual
)

echo.
pause
