#!/usr/bin/env bash
# Deploy the demo to Cloud Run. Config comes from ./config.env (copy from
# config.env.example). Every gcloud call passes --project explicitly — no gcloud
# active-config or workstation coupling is baked in.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${HERE}/config.env"

: "${PROJECT_ID:?set PROJECT_ID in config.env}"
: "${REGION:?set REGION in config.env}"
: "${SERVICE_NAME:?set SERVICE_NAME in config.env}"
: "${BILLING_PROJECT:=$PROJECT_ID}"
: "${LOCATION:=global}"
: "${IDENTITY_MODE:=adc}"

ENV_VARS="BILLING_PROJECT=${BILLING_PROJECT},LOCATION=${LOCATION},IDENTITY_MODE=${IDENTITY_MODE}"
if [[ -n "${TARGET_SA:-}" ]]; then
  ENV_VARS="${ENV_VARS},TARGET_SA=${TARGET_SA}"
fi

SA_ARG=()
if [[ -n "${RUNTIME_SA:-}" ]]; then
  SA_ARG=(--service-account="${RUNTIME_SA}")
fi

echo "Deploying ${SERVICE_NAME} to ${PROJECT_ID}/${REGION} (IDENTITY_MODE=${IDENTITY_MODE})..."
gcloud run deploy "${SERVICE_NAME}" \
  --source "${HERE}" \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --no-allow-unauthenticated \
  --set-env-vars="${ENV_VARS}" \
  "${SA_ARG[@]}"

echo
echo "Deployed. Invoke it (caller needs roles/run.invoker on the service):"
echo "  URL=\$(gcloud run services describe ${SERVICE_NAME} --project=${PROJECT_ID} --region=${REGION} --format='value(status.url)')"
echo "  curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" \"\$URL/ask?q=Which+species+of+tree+is+most+prevalent%3F\""
