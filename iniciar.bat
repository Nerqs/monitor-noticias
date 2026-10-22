@echo off
rem Inicia o Monitor de noticias no Windows. Na primeira vez, instala as dependencias.
cd /d "%~dp0"
if not exist .venv (
    echo Preparando o ambiente pela primeira vez...
    py -3 -m venv .venv || python -m venv .venv
    .venv\Scripts\python -m pip install --upgrade pip
    .venv\Scripts\python -m pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw.exe run.py %*
