#!/usr/bin/env bash
# EMPIRICAL PROOF: for BigQuery, control-plane and data-plane identity are COUPLED
# to the caller — you cannot inject a separate BigQuery data identity via the
# ChatRequest.credentials field (that field is Looker-only).
#
# It provisions throwaway resources, runs four controls, and REVERTS everything on
# exit (trap). This MUTATES your project (creates SAs, a dataset/table, IAM
# bindings) — get sign-off before running. Requires: the operator identity able to
# create SAs and hold roles/iam.serviceAccountTokenCreator on the two test SAs (to
# mint their tokens via impersonation).
#
#   Control | caller of chat() | credentials field  | expected
#   --------|------------------|--------------------|---------
#     A     | P-read           | (none)             | SUCCEED   (positive control)
#     B     | P-none           | (none)             | DENIED    (BQ enforces the caller)
#     C     | P-none           | P-read's token     | DENIED    (credentials != BQ identity -> NO SPLIT)
#     D     | P-read           | P-none's token     | SUCCEED   (BQ ignores credentials for BQ)
#
# A✓ B✗ C✗ D✓ is the decisive result.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${HERE}/config.env"

: "${PROJECT_ID:?set PROJECT_ID in config.env}"
: "${LOCATION:=global}"

DATASET="caapi_test"
TABLE="private_trees"
FQ_TABLE="${PROJECT_ID}.${DATASET}.${TABLE}"
SA_NONE="caapi-pnone@${PROJECT_ID}.iam.gserviceaccount.com"
SA_READ="caapi-pread@${PROJECT_ID}.iam.gserviceaccount.com"

CREATED_DATASET=0
CREATED_SA_NONE=0
CREATED_SA_READ=0

cleanup() {
  echo
  echo "--- Reverting all test resources ---"
  # dataViewer on the dataset for P-read is dropped with the dataset.
  [[ "${CREATED_DATASET}" == "1" ]] && bq rm -r -f -d "${PROJECT_ID}:${DATASET}" || true
  for SA in "${SA_NONE}:${CREATED_SA_NONE}" "${SA_READ}:${CREATED_SA_READ}"; do
    EMAIL="${SA%%:*}"; MADE="${SA##*:}"
    if [[ "${MADE}" == "1" ]]; then
      # Project-level role bindings are removed, then the SA is deleted.
      for ROLE in roles/geminidataanalytics.dataAgentStatelessUser roles/bigquery.user roles/bigquery.dataViewer; do
        gcloud projects remove-iam-policy-binding "${PROJECT_ID}" \
          --member="serviceAccount:${EMAIL}" --role="${ROLE}" \
          --project="${PROJECT_ID}" --quiet >/dev/null 2>&1 || true
      done
      gcloud iam service-accounts delete "${EMAIL}" \
        --project="${PROJECT_ID}" --quiet >/dev/null 2>&1 || true
    fi
  done
  echo "Revert complete."
}
trap cleanup EXIT

echo "--- Creating private dataset + table ${FQ_TABLE} ---"
if ! bq --project_id="${PROJECT_ID}" show -d "${PROJECT_ID}:${DATASET}" >/dev/null 2>&1; then
  bq --project_id="${PROJECT_ID}" mk -d --location=US "${PROJECT_ID}:${DATASET}"
  CREATED_DATASET=1
fi
bq --project_id="${PROJECT_ID}" query --use_legacy_sql=false \
  "CREATE OR REPLACE TABLE \`${FQ_TABLE}\` AS
   SELECT species FROM \`bigquery-public-data.san_francisco.street_trees\` LIMIT 100"

echo "--- Creating test service accounts ---"
gcloud iam service-accounts create caapi-pnone --project="${PROJECT_ID}" \
  --display-name="CA API proof: no dataViewer" --quiet && CREATED_SA_NONE=1
gcloud iam service-accounts create caapi-pread --project="${PROJECT_ID}" \
  --display-name="CA API proof: has dataViewer" --quiet && CREATED_SA_READ=1

echo "--- Granting roles (both: CA + jobs.create; only P-read: dataViewer) ---"
for SA in "${SA_NONE}" "${SA_READ}"; do
  for ROLE in roles/geminidataanalytics.dataAgentStatelessUser roles/bigquery.user; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${SA}" --role="${ROLE}" \
      --project="${PROJECT_ID}" --condition=None --quiet >/dev/null
  done
done
# Only P-read can read the table data.
bq --project_id="${PROJECT_ID}" add-iam-policy-binding \
  --member="serviceAccount:${SA_READ}" --role="roles/bigquery.dataViewer" \
  "${PROJECT_ID}:${DATASET}.${TABLE}" >/dev/null 2>&1 || \
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_READ}" --role="roles/bigquery.dataViewer" \
    --project="${PROJECT_ID}" --condition=None --quiet >/dev/null

echo "--- Waiting for IAM propagation (30s) ---"
sleep 30

mint() {  # mint an access token AS the given SA (needs tokenCreator on it)
  gcloud auth print-access-token --impersonate-service-account="$1" \
    --scopes="https://www.googleapis.com/auth/cloud-platform" --project="${PROJECT_ID}"
}
TOKEN_NONE="$(mint "${SA_NONE}")"
TOKEN_READ="$(mint "${SA_READ}")"

run() {  # run <label> <caller-token> [creds-token]; returns python exit code
  python3 "${HERE}/validate_identity.py" \
    --caller-token "$2" --creds-token "${3:-}" \
    --project "${PROJECT_ID}" --location "${LOCATION}" \
    --table "${FQ_TABLE}" --label "$1" || return $?
}

echo; echo "=== Running controls ==="
declare -A GOT
run "A" "${TOKEN_READ}" ""             ; GOT[A]=$?
run "B" "${TOKEN_NONE}" ""             ; GOT[B]=$?
run "C" "${TOKEN_NONE}" "${TOKEN_READ}"; GOT[C]=$?
run "D" "${TOKEN_READ}" "${TOKEN_NONE}"; GOT[D]=$?

echo; echo "=== Result (0=SUCCEED, 3=DENIED) ==="
printf "A=%s B=%s C=%s D=%s\n" "${GOT[A]}" "${GOT[B]}" "${GOT[C]}" "${GOT[D]}"
if [[ "${GOT[A]}" == "0" && "${GOT[B]}" == "3" && "${GOT[C]}" == "3" && "${GOT[D]}" == "0" ]]; then
  echo "PROVEN: BigQuery identity is coupled to the caller; credentials field cannot inject a BQ data identity."
else
  echo "UNEXPECTED: results did not match A✓ B✗ C✗ D✓ — inspect above."
fi
