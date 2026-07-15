#!/usr/bin/env bash
# Grant the IAM the demo needs. Idempotent (add-iam-policy-binding is safe to
# re-run). Config from ./config.env; --project passed explicitly everywhere.
#
# Grants, by identity model:
#   Control plane : roles/geminidataanalytics.dataAgentStatelessUser  (call chat)
#   Data plane    : roles/bigquery.user + roles/bigquery.dataViewer   (read BQ)
#   Invoke        : roles/run.invoker on the service, for INVOKER_MEMBER
#   Impersonation : roles/iam.serviceAccountTokenCreator on TARGET_SA (model 3)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${HERE}/config.env"

: "${PROJECT_ID:?set PROJECT_ID in config.env}"
: "${IDENTITY_MODE:=adc}"

# The principal that actually reads BigQuery depends on the model:
#   adc/impersonate -> a service account; end_user -> the human end user.
if [[ "${IDENTITY_MODE}" == "impersonate" ]]; then
  DATA_PRINCIPAL="serviceAccount:${TARGET_SA:?set TARGET_SA for impersonate mode}"
elif [[ "${IDENTITY_MODE}" == "end_user" ]]; then
  DATA_PRINCIPAL="${INVOKER_MEMBER:?set INVOKER_MEMBER (the end user) for end_user mode}"
else
  DATA_PRINCIPAL="serviceAccount:${RUNTIME_SA:?set RUNTIME_SA for adc mode}"
fi

echo "Granting control + data plane roles to ${DATA_PRINCIPAL}..."
for ROLE in \
  roles/geminidataanalytics.dataAgentStatelessUser \
  roles/bigquery.user \
  roles/bigquery.dataViewer; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="${DATA_PRINCIPAL}" --role="${ROLE}" \
    --project="${PROJECT_ID}" --condition=None --quiet
done

if [[ -n "${INVOKER_MEMBER:-}" && -n "${SERVICE_NAME:-}" && -n "${REGION:-}" ]]; then
  echo "Granting roles/run.invoker on ${SERVICE_NAME} to ${INVOKER_MEMBER}..."
  gcloud run services add-iam-policy-binding "${SERVICE_NAME}" \
    --member="${INVOKER_MEMBER}" --role="roles/run.invoker" \
    --project="${PROJECT_ID}" --region="${REGION}" --quiet
fi

if [[ "${IDENTITY_MODE}" == "impersonate" ]]; then
  echo "Granting roles/iam.serviceAccountTokenCreator on ${TARGET_SA} to ${RUNTIME_SA}..."
  gcloud iam service-accounts add-iam-policy-binding "${TARGET_SA}" \
    --member="serviceAccount:${RUNTIME_SA:?set RUNTIME_SA}" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --project="${PROJECT_ID}" --quiet
fi

echo "Done."
