@echo off
title Trocar senha de usuario
cd /d "%~dp0"

echo ============================================================
echo   Trocar senha de um usuario do Monitoramento
echo ============================================================
echo.
echo Troca a senha de qualquer usuario direto no banco de dados,
echo sem precisar saber a senha antiga -- use se esquecer a senha
echo de login em http://localhost:8765/login (ou no site hospedado).
echo.
echo IMPORTANTE: isso mexe no banco que estiver configurado agora no
echo seu arquivo .env (DATABASE_URL). Se essa linha estiver vazia ou
echo comentada, mexe no banco LOCAL deste computador. Se estiver
echo apontando pro Supabase, mexe no banco da NUVEM (o mesmo que o
echo site hospedado usa).
echo.

if not exist ".venv\Scripts\python.exe" (
    echo ERRO: nao encontrei a instalacao do programa nesta pasta.
    echo Abra "Abrir Monitoramento.bat" pelo menos uma vez antes de
    echo rodar este arquivo.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"
python -m scripts.reset_password

pause
