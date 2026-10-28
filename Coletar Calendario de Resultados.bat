@echo off
REM Calendario de resultados (28/10/2026): baixa as datas de entrega de ITR/DFP
REM da CVM e grava no banco do .env. Na nuvem isso roda sozinho todo dia
REM (GitHub Actions, resultados_cvm.yml); use este arquivo so para forcar
REM uma rodada ou para a primeira carga.
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" call ".venv\Scripts\activate.bat"
python -m scripts.coletar_resultados_cvm
echo.
pause
