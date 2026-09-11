@echo off
title Diagnostico do Banco
cd /d "%~dp0"

echo ============================================================
echo   Diagnostico do banco (Supabase) -- SOMENTE LEITURA
echo ============================================================
echo.
echo Este arquivo NAO altera nada no banco. Ele so le estatisticas
echo do Postgres para descobrir o que esta consumindo recurso.
echo.
echo Pode demorar de 10 a 60 segundos.
echo.

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m scripts.diagnostico_banco
) else (
  python -m scripts.diagnostico_banco
)

echo.
echo ============================================================
echo   Pronto. O relatorio foi salvo em data\diagnostico_banco.txt
echo ============================================================
pause
