@echo off
title Importar Mercado Secundario
cd /d "%~dp0"

echo ============================================================
echo   Importar export de volume - aba Mercado Secundario
echo   (o caminho normal e pela tela de Administracao do site;
echo    use este quando o arquivo for grande demais)
echo ============================================================
echo.
set "ARQ=%~1"
if "%ARQ%"=="" set /p "ARQ=Arraste o arquivo .xlsx ou .csv para esta janela e tecle Enter: "
set "ARQ=%ARQ:"=%"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m scripts.importar_mercado_secundario "%ARQ%"
) else (
  python -m scripts.importar_mercado_secundario "%ARQ%"
)

echo.
pause
