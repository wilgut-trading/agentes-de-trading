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
echo "Modelos que ofrece tu plan:"
TRADINGAGENTS_DATA_DIR=./data docker compose run --rm --entrypoint python tradingagents \
  -c "from tradingagents.llm_clients.chatgpt_auth import list_models; [print(' ', m[1]) for m in list_models()]" || echo "  (no se pudieron listar)"
gcloud storage cp data/chatgpt-auth.json "gs://$BK/private/tradingagents/chatgpt-auth.json" --no-user-output-enabled
gcloud storage ls "gs://$BK/private/tradingagents/" | grep -q chatgpt-auth.json   # sin leerla: tu cuenta no puede
rm -rf data
echo "LISTO: sesion guardada en la nube y copia local borrada."
