@echo off
title Limpar links antigos da Vortx
cd /d "%~dp0"
echo Apaga os documentos da Vortx gravados com link que vence (antes de 04/11/2026).
echo A proxima coleta grava cada um de novo, com link que nao vence.
echo.
pause
if not exist ".venv\Scripts\python.exe" (
    echo ERRO: abra "Abrir Monitoramento.bat" pelo menos uma vez antes.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m scripts.limpar_links_vortx --dry-run
".venv\Scripts\python.exe" -m scripts.limpar_links_vortx
pause
