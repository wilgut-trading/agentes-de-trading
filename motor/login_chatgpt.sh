#!/bin/bash
# Inicia tu sesion de ChatGPT, la sube al bucket y borra la copia local.
# Lo ejecuta el dueno. Nunca muestra el contenido del archivo.
set -euo pipefail
cd "$(dirname "$0")/.."
BK=agentes-de-trading-wg.firebasestorage.app
touch .env
mkdir -p data
TRADINGAGENTS_DATA_DIR=./data docker compose run --rm tradingagents login chatgpt
[ -s data/chatgpt-auth.json ] || { echo "ERROR: no se creo la sesion."; exit 1; }
gcloud storage cp data/chatgpt-auth.json "gs://$BK/private/tradingagents/chatgpt-auth.json" --no-user-output-enabled
gcloud storage ls "gs://$BK/private/tradingagents/chatgpt-auth.json" >/dev/null
rm -rf data
echo "LISTO: sesion guardada en la nube y copia local borrada."
