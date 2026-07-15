#!/usr/bin/env bash
# ============================================================================
# EXACT commands used to deploy + validate the three Cloud Run identity models
# in project kenly-dev-auto-240320261552 on 2026-07-15. Re-runnable end-to-end.
#
# Models (see ../README.md#-deployment--identity-models):
#   2  custom runtime SA          -> service sf-trees-sa           (IDENTITY_MODE=adc)
#   3  impersonated target SA     -> service sf-trees-impersonate  (IDENTITY_MODE=impersonate)
#   4  end-user OAuth (forwarded) -> service sf-trees-enduser      (IDENTITY_MODE=end_user)
#
# PORTABILITY / CLAUDE.md rule 3: these are plain `gcloud` with explicit
# --project, no workstation coupling. On THIS workstation each command was run
# through the ADC-isolating wrapper, i.e. prefixed with:
#       gc admin--kenly-dev-auto-240320261552 <command>
# To reproduce that exactly, set USE_GC=1 (uses the `gc <profile>` wrapper).
# ============================================================================
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-kenly-dev-auto-240320261552}"
REGION="${REGION:-us-central1}"
PROJECT_NUMBER="${PROJECT_NUMBER:-80243709419}"
DEPLOYER="${DEPLOYER:-admin@kenly.altostrat.com}"          # principal running the deploy
SOURCE_DIR="${SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

RUNTIME="ca-demo-runtime@${PROJECT_ID}.iam.gserviceaccount.com"   # model 2 runtime (holds CA+BQ)
TARGET="ca-demo-target@${PROJECT_ID}.iam.gserviceaccount.com"     # model 3 impersonation target (holds CA+BQ)
CALLER="ca-demo-caller@${PROJECT_ID}.iam.gserviceaccount.com"     # model 3/4 runtime (NO data roles)
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"  # Cloud Build source builder

# `g` wraps gcloud: plain by default, or through the `gc` profile wrapper if USE_GC=1.
g() {
  if [[ "${USE_GC:-0}" == "1" ]]; then
    # shellcheck disable=SC1090
    source ~/.bashrc; gc "admin--${PROJECT_ID}" gcloud "$@"
  else
    gcloud "$@"
  fi
}

echo "### 1. Enable APIs"
g services enable \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  geminidataanalytics.googleapis.com bigquery.googleapis.com iamcredentials.googleapis.com \
  --project="${PROJECT_ID}"

echo "### 2. Create service accounts"
for SA in ca-demo-runtime ca-demo-target ca-demo-caller; do
  g iam service-accounts create "${SA}" --project="${PROJECT_ID}" \
    --display-name="CA demo: ${SA}" || true   # ignore 'already exists'
done

echo "### 3. Data/control-plane roles on runtime (model 2) + target (model 3)"
for SA in "${RUNTIME}" "${TARGET}"; do
  for ROLE in roles/geminidataanalytics.dataAgentStatelessUser roles/bigquery.user; do
    g projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${SA}" --role="${ROLE}" --condition=None --quiet
  done
done

echo "### 4. Impersonation: caller may mint tokens AS target (model 3)"
g iam service-accounts add-iam-policy-binding "${TARGET}" \
  --member="serviceAccount:${CALLER}" --role="roles/iam.serviceAccountTokenCreator" \
  --project="${PROJECT_ID}" --quiet

echo "### 5. Deployer may actAs the runtime SAs (needed to deploy with them)"
for SA in "${RUNTIME}" "${CALLER}"; do
  g iam service-accounts add-iam-policy-binding "${SA}" \
    --member="user:${DEPLOYER}" --role="roles/iam.serviceAccountUser" \
    --project="${PROJECT_ID}" --quiet
done

echo "### 6. Cloud Build source-deploy needs the compute SA to have builder roles"
for ROLE in roles/cloudbuild.builds.builder roles/storage.objectViewer \
            roles/artifactregistry.writer roles/logging.logWriter; do
  g projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${COMPUTE_SA}" --role="${ROLE}" --condition=None --quiet
done

echo "### 7a. Deploy model 2 — custom runtime SA (IDENTITY_MODE=adc)"
g run deploy sf-trees-sa \
  --source "${SOURCE_DIR}" --project="${PROJECT_ID}" --region="${REGION}" \
  --service-account="${RUNTIME}" --no-allow-unauthenticated \
  --set-env-vars="BILLING_PROJECT=${PROJECT_ID},LOCATION=global,IDENTITY_MODE=adc" --quiet

echo "### 7b. Deploy model 3 — impersonation (runtime=caller has NO data roles)"
g run deploy sf-trees-impersonate \
  --source "${SOURCE_DIR}" --project="${PROJECT_ID}" --region="${REGION}" \
  --service-account="${CALLER}" --no-allow-unauthenticated \
  --set-env-vars="BILLING_PROJECT=${PROJECT_ID},LOCATION=global,IDENTITY_MODE=impersonate,TARGET_SA=${TARGET}" --quiet

echo "### 7c. Deploy model 4 — end-user OAuth (runtime=caller has NO data roles)"
# Authenticated: Cloud Run's invocation ID token rides in X-Serverless-Authorization,
# leaving the Authorization header free for the END USER's token, which the app
# relays to the CA API / BigQuery. See test_all_models.sh for the two-header call.
g run deploy sf-trees-enduser \
  --source "${SOURCE_DIR}" --project="${PROJECT_ID}" --region="${REGION}" \
  --service-account="${CALLER}" --no-allow-unauthenticated \
  --set-env-vars="BILLING_PROJECT=${PROJECT_ID},LOCATION=global,IDENTITY_MODE=end_user" --quiet

echo "### 8. Invoker IAM for the (now all authenticated) services"
for SVC in sf-trees-sa sf-trees-impersonate sf-trees-enduser; do
  g run services add-iam-policy-binding "${SVC}" \
    --member="user:${DEPLOYER}"           --role="roles/run.invoker" \
    --project="${PROJECT_ID}" --region="${REGION}" --quiet
  g run services add-iam-policy-binding "${SVC}" \
    --member="serviceAccount:${CALLER}"   --role="roles/run.invoker" \
    --project="${PROJECT_ID}" --region="${REGION}" --quiet
done
# Deployer may mint ID tokens AS caller (used to invoke authenticated services below)
g iam service-accounts add-iam-policy-binding "${CALLER}" \
  --member="user:${DEPLOYER}" --role="roles/iam.serviceAccountTokenCreator" \
  --project="${PROJECT_ID}" --quiet

cat <<EOF

### DONE. Service URLs:
$(g run services list --project="${PROJECT_ID}" --region="${REGION}" \
   --format='value(metadata.name, status.url)')

### All three services are AUTHENTICATED. Run the full test suite with:
###     USE_GC=1 ./test_all_models.sh      (this workstation)
###     ./test_all_models.sh               (elsewhere, if already authed)
###
### Manual crib (invocation needs an ID token, NOT an access token):
### Models 2 & 3 (app ignores Authorization for data — it's only the invoke check):
#   URL=https://sf-trees-sa-${PROJECT_NUMBER}.${REGION}.run.app
#   IDT=\$(gcloud auth print-identity-token --impersonate-service-account=${CALLER} \\
#           --audiences="\$URL" --include-email --project=${PROJECT_ID})
#   curl -H "Authorization: Bearer \$IDT" "\$URL/ask?q=Which+species+of+tree+is+most+prevalent%3F"
### Model 4 (two headers: ID token invokes; end-user access token reads BQ):
#   URL=https://sf-trees-enduser-${PROJECT_NUMBER}.${REGION}.run.app
#   IDT=\$(gcloud auth print-identity-token --impersonate-service-account=${CALLER} \\
#           --audiences="\$URL" --include-email --project=${PROJECT_ID})
#   UT=\$(gcloud auth print-access-token --project=${PROJECT_ID})
#   curl -H "X-Serverless-Authorization: Bearer \$IDT" -H "Authorization: Bearer \$UT" \\
#        "\$URL/ask?q=Which+species+of+tree+is+most+prevalent%3F"
#   # no auth -> 403 ; only access token -> 401
EOF
