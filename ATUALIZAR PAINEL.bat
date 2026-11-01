@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Edital Tracker - atualizando
echo.
echo   Lendo suas notas do Obsidian e atualizando o painel...
echo.
python atualizar.py
if errorlevel 1 (
  echo.
  echo   Algo deu errado. Leia a mensagem acima.
  echo.
  pause
  exit /b 1
)
echo.
echo   Publicando no link...
python publicar.py --silencioso
echo.
echo   Pode fechar esta janela.
timeout /t 8 >nul
