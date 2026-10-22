#!/usr/bin/env bash
# Inicia o Monitor de notícias no Linux. Na primeira vez, instala as dependências.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
    echo "Preparando o ambiente pela primeira vez..."
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
fi
exec .venv/bin/python run.py "$@"
