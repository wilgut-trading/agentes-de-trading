#!/bin/bash
# Guarda tu clave de FRED como secreto de GitHub y vuelve a desplegar. No se muestra ni queda en el historial.
# Ojo: desplegar cierra cualquier vista previa abierta.
set -euo pipefail
REPO=wilgut-trading/agentes-de-trading
read -r -s -p "Pega tu clave de FRED y pulsa Enter (no se verá): " CLAVE; echo
[ -n "$CLAVE" ] || { echo "ERROR: no escribiste nada."; exit 1; }
printf '%s' "$CLAVE" | gh secret set FRED_API_KEY --repo "$REPO" --env produccion
gh workflow run desplegar.yml --repo "$REPO" --ref main 2>/dev/null || echo "(Se aplicará en el próximo despliegue.)"
echo "LISTO: clave guardada."
