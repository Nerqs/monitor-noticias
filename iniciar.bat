@echo off
rem Inicia o Monitor de noticias a partir do codigo-fonte (precisa de Python).
rem Quem nao tem Python deve baixar o executavel pronto em:
rem https://github.com/Nerqs/monitor-noticias/releases/latest
setlocal EnableDelayedExpansion
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
    set "PY="
    where py >nul 2>nul && set "PY=py -3"
    if not defined PY (
        python -c "import sys" >nul 2>nul && set "PY=python"
    )
    if not defined PY (
        echo.
        echo  Python nao foi encontrado neste computador.
        echo.
        echo  Voce nao precisa dele: baixe o programa pronto ^(MonitorNoticias-windows.zip^) em
        echo  https://github.com/Nerqs/monitor-noticias/releases/latest
        echo.
        start "" https://github.com/Nerqs/monitor-noticias/releases/latest
        pause
        exit /b 1
    )
    echo Preparando o ambiente pela primeira vez...
    !PY! -m venv .venv || goto falhou
    .venv\Scripts\python -m pip install --upgrade pip || goto falhou
    .venv\Scripts\python -m pip install -r requirements.txt || goto falhou
)
start "" .venv\Scripts\pythonw.exe run.py %*
exit /b 0

:falhou
echo.
echo  Nao foi possivel preparar o ambiente. Apague a pasta .venv e tente de novo,
echo  ou baixe o programa pronto em https://github.com/Nerqs/monitor-noticias/releases/latest
if exist .venv rmdir /s /q .venv
pause
exit /b 1
