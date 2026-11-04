#!/bin/bash
# Prepara Google Cloud y GitHub. Lo ejecuta el dueno, una vez, con su propia sesion.
# Solo da permisos a las cuentas de esta app y solo sobre este proyecto.
#   web y despliegue: pueden usar el bucket, MENOS la sesion de ChatGPT.
#   motor:            es la unica cuenta que puede leer esa sesion.
#   GitHub:           entra sin claves, solo desde este repo y sus dos ambientes.
set -euo pipefail
P=agentes-de-trading-wg
REPO=wilgut-trading/agentes-de-trading
BK=$P.firebasestorage.app
N=$(gcloud projects describe "$P" --format='value(projectNumber)')
D=agentes-despliegue@$P.iam.gserviceaccount.com
WEB=agentes-web@$P.iam.gserviceaccount.com
MOT=agentes-motor@$P.iam.gserviceaccount.com
CORREO=$(gcloud config get-value account)

rol() { gcloud projects add-iam-policy-binding "$P" --member="serviceAccount:$1" --role="$2" --condition=None --quiet >/dev/null; echo "  $2 -> ${1%%@*}"; }
echo "1/5 Permisos del proyecto"
for r in roles/run.jobsExecutor roles/firebaseauth.admin; do rol "$WEB" "$r"; done
rol "$MOT" roles/run.jobsExecutor
for r in roles/run.admin roles/artifactregistry.writer roles/firebasehosting.admin roles/firebaserules.admin \
         roles/firebasestorage.viewer roles/serviceusage.serviceUsageConsumer roles/firebase.viewer; do rol "$D" "$r"; done

echo "2/5 Permisos del bucket"
COND='expression=!resource.name.endsWith("chatgpt-auth.json"),title=sin-sesion-chatgpt'
for c in "$WEB" "$D"; do gcloud storage buckets add-iam-policy-binding "gs://$BK" --member="serviceAccount:$c" --role=roles/storage.objectUser --condition="$COND" >/dev/null; done
gcloud storage buckets add-iam-policy-binding "gs://$BK" --member="serviceAccount:$MOT" --role=roles/storage.objectUser --condition=None >/dev/null
for c in "$WEB" "$MOT"; do gcloud iam service-accounts add-iam-policy-binding "$c" --project "$P" --member="serviceAccount:$D" --role=roles/iam.serviceAccountUser --quiet >/dev/null; done

echo "3/5 Acceso sin claves desde GitHub"
gcloud iam workload-identity-pools describe github --project "$P" --location=global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create github --project "$P" --location=global --display-name=GitHub
gcloud iam workload-identity-pools providers describe repo --project "$P" --location=global --workload-identity-pool=github >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc repo --project "$P" --location=global --workload-identity-pool=github \
    --issuer-uri=https://token.actions.githubusercontent.com \
    --attribute-mapping='google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.environment=assertion.environment' \
    --attribute-condition="assertion.repository == \"$REPO\" && assertion.environment in [\"produccion\", \"vista-previa\"]"
gcloud iam service-accounts add-iam-policy-binding "$D" --project "$P" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$N/locations/global/workloadIdentityPools/github/attribute.repository/$REPO" --quiet >/dev/null

echo "4/5 App web de Firebase"
firebase apps:list WEB --project "$P" --json | grep -q appId && [ "$(firebase apps:list WEB --project "$P" --json | python3 -c 'import json,sys;print(len(json.load(sys.stdin)["result"]))')" != 0 ] \
  || firebase apps:create WEB "Agentes de Trading" --project "$P" >/dev/null
APP=$(firebase apps:list WEB --project "$P" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"][0]["appId"])')
KEY=$(firebase apps:sdkconfig WEB "$APP" --project "$P" --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["sdkConfig"]["apiKey"])')

echo "5/5 GitHub: ambientes y variables"
gh api -X PUT "repos/$REPO/actions/permissions" -F enabled=true -f allowed_actions=all
gh api -X PUT "repos/$REPO/actions/permissions/workflow" -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false
YO=$(gh api user --jq .id)
echo "{\"reviewers\":[{\"type\":\"User\",\"id\":$YO}]}" | gh api -X PUT "repos/$REPO/environments/vista-previa" --input - >/dev/null
echo '{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}' | gh api -X PUT "repos/$REPO/environments/produccion" --input - >/dev/null
gh api -X POST "repos/$REPO/environments/produccion/deployment-branch-policies" -f name=main -f type=branch >/dev/null 2>&1 || true
gh variable set GCP_PROYECTO --repo "$REPO" --body "$P"
gh variable set GCP_WIF_PROVIDER --repo "$REPO" --body "projects/$N/locations/global/workloadIdentityPools/github/providers/repo"
gh variable set GCP_CUENTA_DESPLIEGUE --repo "$REPO" --body "$D"
gh variable set AGENTES_BUCKET --repo "$REPO" --body "$BK"
gh variable set AGENTES_ANALISTAS --repo "$REPO" --body "market,social,news,fundamentals"
gh variable set AGENTES_ORIGENES --repo "$REPO" --body "https://$P.web.app,https://$P.firebaseapp.com"
gh variable set FIREBASE_APIKEY --repo "$REPO" --body "$KEY"
gh secret set AGENTES_CORREOS --repo "$REPO" --env produccion --body "$CORREO"
echo "LISTO: preparacion terminada."
