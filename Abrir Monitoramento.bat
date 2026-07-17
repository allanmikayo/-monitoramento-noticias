@echo off
title Monitoramento de Noticias - Credito Privado
cd /d "%~dp0"

echo ============================================================
echo   Monitoramento de Noticias - Credito Privado
echo ============================================================
echo.

rem --- se ja tem um Monitoramento rodando nesta porta, so abre o navegador
rem     nele em vez de tentar subir um segundo servidor por cima (isso
rem     causava erro de porta ocupada e confundia qual janela estava valendo)
powershell -NoProfile -Command "try { $c = New-Object System.Net.Sockets.TcpClient('localhost',8765); $c.Close(); exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
    echo O Monitoramento ja esta rodando em outra janela.
    echo Abrindo o navegador...
    start "" http://localhost:8765
    echo.
    echo Se quiser reiniciar do zero ^(por exemplo, depois de uma atualizacao^),
    echo feche TODAS as janelas pretas do Monitoramento primeiro, depois
    echo abra este arquivo de novo.
    echo.
    pause
    exit /b 0
)

rem --- localizar o Python instalado no computador -----------------------
set "PY_CMD="
where python >nul 2>nul
if %errorlevel%==0 set "PY_CMD=python"
if not defined PY_CMD (
    where py >nul 2>nul
    if %errorlevel%==0 set "PY_CMD=py -3"
)
if not defined PY_CMD (
    echo ERRO: Nao encontrei o Python instalado neste computador.
    echo.
    echo Baixe em https://www.python.org/downloads/ e, na instalacao,
    echo marque a caixa "Add python.exe to PATH" antes de clicar em Install.
    echo Depois, de dois cliques neste arquivo de novo.
    echo.
    pause
    exit /b 1
)

rem --- preparar o ambiente -------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo Primeira vez rodando neste computador. Preparando tudo, aguarde
    echo alguns minutos - isso so acontece uma vez...
    echo.
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo ERRO ao criar o ambiente Python. Veja a mensagem acima.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

rem instala/atualiza dependencias sempre - e rapido quando ja esta tudo
rem instalado, e resolve o caso de uma instalacao anterior ter falhado
rem no meio do caminho.
python -m pip install --upgrade pip >nul 2>nul
echo Verificando dependencias... isso pode demorar alguns minutos na
echo primeira vez.
pip install -r requirements.txt -q
if errorlevel 1 (
    echo ERRO ao instalar as dependencias. Veja a mensagem acima.
    pause
    exit /b 1
)

rem --- navegador do Playwright (usado pelos coletores de rating da S&P e
rem     da Moody's Local, que soh mostram os dados depois de rodar JavaScript
rem     na pagina) - baixa uma vez (uns 150-300MB) e marca com um arquivo para
rem     nao tentar de novo toda vez que o programa abre.
if not exist ".venv\.playwright_ok" (
    echo.
    echo Preparando o navegador usado para coletar as acoes de rating
    echo ^(S^&P e Moody's Local^) - isso acontece so uma vez e pode demorar
    echo alguns minutos dependendo da internet...
    python -m playwright install chromium
    if errorlevel 1 (
        echo AVISO: nao consegui preparar o navegador de coleta de ratings.
        echo As demais fontes de noticias continuam funcionando normalmente;
        echo tento de novo na proxima vez que voce abrir o programa.
    ) else (
        echo ok > ".venv\.playwright_ok"
    )
)

rem --- Chrome "de verdade" (nao o Chromium generico do Playwright) - a S&P
rem     bloqueia o Chromium padrao na entrada (Access Denied, deteccao de
rem     robo da Akamai); o coletor da S&P tenta usar este Chrome primeiro,
rem     que se passa por um navegador comum com mais chance de nao ser
rem     bloqueado. Se essa instalacao falhar, o coletor cai de volta pro
rem     Chromium padrao sozinho (nao trava o programa).
if not exist ".venv\.playwright_chrome_ok" (
    echo.
    echo Preparando o Chrome usado para coletar da S^&P Global...
    python -m playwright install chrome
    if errorlevel 1 (
        echo AVISO: nao consegui preparar o Chrome para a S^&P. O coletor da
        echo S^&P vai tentar de novo com o navegador padrao.
    ) else (
        echo ok > ".venv\.playwright_chrome_ok"
    )
)

rem --- arquivo de configuracao (.env) -------------------------------------
if not exist ".env" (
    copy ".env.example" ".env" >nul
)

rem --- banco de dados: prepara/atualiza sempre (e rapido e seguro repetir,
rem     so insere o que ainda nao existe - protege contra reinicios apos
rem     algum erro ter interrompido a preparacao no meio do caminho) --------
echo.
echo Preparando o banco de dados - setores, empresas e fontes...
python -m scripts.seed
if errorlevel 1 (
    echo.
    echo ERRO ao preparar o banco de dados. Veja a mensagem acima.
    pause
    exit /b 1
)
echo.
echo ============================================================
echo   Acesso:
echo   E-mail:  allancruz078@gmail.com
echo   Senha:   a que voce configurou (ou troque-esta-senha, se ainda
echo            nao tiver trocado - troque em "Minha conta" apos entrar)
echo ============================================================
echo.

rem --- abre o navegador so quando o site estiver realmente pronto ---------
rem (espera ate 3 minutos verificando a porta 8765 - evita abrir cedo
rem  demais na primeira vez, quando a instalacao pode demorar)
start "" powershell -NoProfile -WindowStyle Hidden -Command "$deadline=(Get-Date).AddSeconds(180); $up=$false; while((Get-Date) -lt $deadline){ try { $c=New-Object System.Net.Sockets.TcpClient('localhost',8765); if($c.Connected){$c.Close(); $up=$true; break} } catch { Start-Sleep -Milliseconds 500 } }; if($up){ Start-Process 'http://localhost:8765' }"

echo.
echo Iniciando o site... NAO FECHE esta janela enquanto estiver usando o
echo Monitoramento. Fechar esta janela desliga o site.
echo O navegador vai abrir sozinho assim que o site estiver pronto.
echo Se depois de alguns minutos o navegador nao abrir, veja se apareceu
echo algum erro em texto nesta janela.
echo.

python run.py

pause
