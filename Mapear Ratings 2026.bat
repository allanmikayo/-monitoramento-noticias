@echo off
if /I not "%~1"=="RUN" (
    start "Mapear Ratings 2026" cmd /k "%~f0" RUN
    exit /b
)
title Mapear Ratings 2026 - Moody's Local / S^&P / Fitch
cd /d "%~dp0"

echo ============================================================
echo   Mapeamento de acoes de rating local Brasil - 2026
echo   Moody's Local, S^&P Global Ratings Brasil e Fitch Ratings
echo ============================================================
echo.
echo Isso e' SEPARADO do dashboard de monitoramento -- nao mexe no
echo banco de dados dele. Coleta TODAS as acoes de rating do MERCADO
echo INTEIRO (nao so' as empresas que voce cobre) publicadas em 2026
echo pelas 3 agencias, abre cada uma pra pegar rating anterior/atual
echo e perspectiva anterior/atual, e gera uma planilha .xlsx na pasta
echo "data".
echo.
echo IMPORTANTE: isso pode demorar BASTANTE (sao centenas de acoes,
echo e cada uma precisa abrir um PDF ou pagina pra ler o detalhe --
echo pode levar de 1 a varias horas dependendo da sua internet). Se
echo fechar essa janela no meio, so' rodar de novo -- ele retoma de
echo onde parou (nao recomeca do zero).
echo.
echo Tudo que aparece nesta janela tambem fica salvo em
echo data\mapear_ratings_2026_log.txt -- se nao souber dizer se
echo funcionou, so' me mandar esse arquivo que eu leio direto.
echo.
echo Se quiser testar rapido antes de rodar tudo (poucas acoes por
echo agencia, so' pra ver se esta funcionando), digite T. Para rodar
echo tudo (2026 inteiro, as 3 agencias), digite C. Para so' ATUALIZAR
echo a planilha que voce ja tem (emissor/setor/nivel da acao/ratings
echo mais corretos), SEM acessar a internet e em segundos, digite R.
echo Qualquer outra tecla cancela.
echo.
choice /C TCRN /M "Teste rapido (T), Completo (C), Reprocessar sem internet (R) ou cancelar (N)"
set ESCOLHA=%errorlevel%

echo.
echo [debug] opcao registrada: %ESCOLHA% (1=Teste, 2=Completo, 3=Reprocessar, 4=Cancelar)
echo.

if "%ESCOLHA%"=="4" (
    echo.
    echo Cancelado.
    pause
    exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
    echo ERRO: nao encontrei a instalacao do programa nesta pasta.
    echo Abra "Abrir Monitoramento.bat" pelo menos uma vez antes de
    echo rodar este arquivo -- ele instala o Python/dependencias.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo.
echo Conferindo dependencias novas (pandas, pdfplumber)...
pip install -r requirements.txt --quiet

if "%ESCOLHA%"=="2" (
    echo.
    echo ------------------------------------------------------------
    echo RODADA COMPLETA: 2026 inteiro, as 3 agencias. Isso demora.
    echo ------------------------------------------------------------
    python -m scripts.mapear_ratings_2026
) else if "%ESCOLHA%"=="3" (
    echo.
    echo ------------------------------------------------------------
    echo REPROCESSANDO -- sem internet -- o que ja foi coletado...
    echo ------------------------------------------------------------
    python -m scripts.mapear_ratings_2026 --reprocessar
) else (
    echo.
    echo ------------------------------------------------------------
    echo TESTE RAPIDO: so' as 5 primeiras acoes de cada agencia.
    echo ------------------------------------------------------------
    python -m scripts.mapear_ratings_2026 --limite 5
)

echo.
echo ------------------------------------------------------------
echo Pronto (ou parou por erro -- veja as mensagens acima). A
echo planilha, se foi gerada, esta na pasta "data" deste projeto.
echo ------------------------------------------------------------
pause
