@echo off
title Corrigir vinculos de empresa
cd /d "%~dp0"

echo ============================================================
echo   Corrigir vinculos de empresa nas noticias ja salvas
echo ============================================================
echo.
echo Isso reprocessa TODAS as noticias ja guardadas com a logica
echo mais recente de casamento de palavra-chave, e corrige empresas
echo erradas que ficaram vinculadas por bugs antigos (ja consertados
echo no codigo, mas que nao se auto-corrigem nas noticias que ja
echo foram salvas antes do conserto).
echo.
echo Isso NAO apaga nenhuma noticia -- so corrige quais empresas
echo estao marcadas em cada uma.
echo.
echo IMPORTANTE: feche a janela do Monitoramento (se estiver aberta)
echo antes de continuar, pra evitar os dois mexerem no banco ao
echo mesmo tempo.
echo.
pause

if not exist ".venv\Scripts\python.exe" (
    echo ERRO: nao encontrei a instalacao do programa nesta pasta.
    echo Abra "Abrir Monitoramento.bat" pelo menos uma vez antes de
    echo rodar este arquivo.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo.
echo Passo 1 de 2: mostrando o que vai mudar (nada e salvo ainda)...
echo ------------------------------------------------------------
python -m scripts.rebuild_company_links --dry-run
echo ------------------------------------------------------------
echo.
echo Confira a lista acima. Se fizer sentido, digite S para aplicar
echo de verdade. Qualquer outra tecla cancela sem mudar nada.
choice /C SN /M "Aplicar as correcoes agora"
if errorlevel 2 (
    echo.
    echo Cancelado -- nada foi alterado no banco.
    pause
    exit /b 0
)

echo.
echo Passo 2 de 2: aplicando de verdade...
echo ------------------------------------------------------------
python -m scripts.rebuild_company_links
echo ------------------------------------------------------------
echo.
echo Pronto. Pode abrir o Monitoramento normalmente agora.
pause
