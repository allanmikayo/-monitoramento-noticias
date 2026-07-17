@echo off
title Reiniciar Monitoramento
cd /d "%~dp0"

echo ============================================================
echo   Reiniciar Monitoramento de Noticias
echo ============================================================
echo.
echo USE ESTE ARQUIVO sempre que eu disser que corrigi algo no
echo codigo e pedir pra voce testar de novo. Só fechar a janela
echo preta e abrir "Abrir Monitoramento.bat" de novo NAO reinicia
echo o programa de verdade -- ele detecta que a porta ja esta em
echo uso e so abre o navegador de novo na versao ANTIGA, ainda
echo rodando em segundo plano. Este arquivo encerra essa versao
echo antiga primeiro, garantindo que a atualizacao mais recente
echo entre em vigor.
echo.
echo Encerrando a versao antiga (se estiver aberta em segundo plano)...

powershell -NoProfile -Command ^
  "$conns = Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue; " ^
  "if ($conns) { $conns | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { try { Stop-Process -Id $_ -Force -ErrorAction Stop; Write-Host 'Processo antigo encerrado.' } catch {} } } else { Write-Host 'Nenhuma versao antiga rodando -- ok.' }"

rem tambem fecha qualquer janela "Monitoramento de Noticias - Credito Privado"
rem que tenha sobrado aberta (cinto de seguranca, caso a porta ja tivesse
rem sido liberada mas a janela preta ainda estivesse aberta por outro motivo)
taskkill /FI "WINDOWTITLE eq Monitoramento de Noticias - Credito Privado*" /T /F >nul 2>nul

timeout /t 2 /nobreak >nul

echo.
echo Iniciando a versao atualizada...
echo.

call "Abrir Monitoramento.bat"
