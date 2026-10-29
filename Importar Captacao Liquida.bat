@echo off
title Importar Captacao Liquida
cd /d "%~dp0"

echo ============================================================
echo   Importar exports de captacao liquida - aba Cap. Liquida
echo   (o caminho normal e pela tela de Administracao do site)
echo ============================================================
echo.
set "ARQ=%~1"
if "%ARQ%"=="" set /p "ARQ=Arraste o arquivo .csv (diario ou mensal) para esta janela e tecle Enter: "
set "ARQ=%ARQ:"=%"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m scripts.importar_captacao "%ARQ%"
) else (
  python -m scripts.importar_captacao "%ARQ%"
)

echo.
pause
