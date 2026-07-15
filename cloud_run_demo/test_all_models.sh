#!/usr/bin/env bash
# ============================================================================
# Manual e2e tests for the three deployed Cloud Run identity models.
# All services are AUTHENTICATED (--no-allow-unauthenticated), so every call
# needs a valid invocation credential.
#
# KEY FACTS (verified 2026-07-15):
#   * Cloud Run's invocation check needs an OIDC **ID token** whose audience ==
#     the service URL. A plain OAuth **access token** is NOT accepted for
#     invocation (returns 401). No auth at all returns 403.
#   * A user credential can't set the ID-token audience, so we mint the ID token
#     by IMPERSONATING a service account that holds roles/run.invoker.
#   * Invocation is done with `curl`; the tokens are minted with `gcloud`.
#
# Per-model transport:
#   Model 2 (sf-trees-sa, IDENTITY_MODE=adc)         -> Authorization: <ID token>
#   Model 3 (sf-trees-impersonate, impersonate)      -> Authorization: <ID token>
#       (for 2 & 3 the app IGNORES Authorization for data; it's only Cloud Run's
#        invocation check. Data identity = runtime SA / impersonated target SA.)
#   Model 4 (sf-trees-enduser, end_user)             -> TWO headers:
#       X-Serverless-Authorization: <ID token>        (Cloud Run invocation)
#       Authorization:              <end-user token>  (app relays to CA API/BQ)
#
# PORTABILITY / CLAUDE.md rule 3: plain gcloud + explicit --project. On THIS
# workstation set USE_GC=1 to route through the ADC wrapper `gc admin--<proj>`
# (needed so tokens are minted as admin@kenly, the identity that holds the roles).
#   USE_GC=1 ./test_all_models.sh
# ============================================================================
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-kenly-dev-auto-240320261552}"
REGION="${REGION:-us-central1}"
PROJECT_NUMBER="${PROJECT_NUMBER:-80243709419}"
CALLER="ca-demo-caller@${PROJECT_ID}.iam.gserviceaccount.com"   # invoker SA (has run.invoker)
Q="Which+species+of+tree+is+most+prevalent%3F"

g() {
  if [[ "${USE_GC:-0}" == "1" ]]; then
    # shellcheck disable=SC1090
    source ~/.bashrc; gc "admin--${PROJECT_ID}" gcloud "$@"
  else
    gcloud "$@"
  fi
}
url()   { echo "https://$1-${PROJECT_NUMBER}.${REGION}.run.app"; }
id_tok(){ g auth print-identity-token --impersonate-service-account="${CALLER}" \
            --audiences="$1" --include-email --project="${PROJECT_ID}" 2>/dev/null; }
usr_tok(){ g auth print-access-token --project="${PROJECT_ID}" 2>/dev/null; }

summarize() {  # reads JSON from stdin, prints identity_mode + event types + BQ job
  python3 -c "import sys,json
d=json.load(sys.stdin)
print('   identity_mode =', d.get('identity_mode'))
for e in d.get('events', []):
    tag = e.get('job_id','') or e.get('name','')
    print('     -', e.get('type'), tag)
if 'error' in d: print('   ERROR:', str(d['error'])[:300])"
}

echo "############ Model 2 — custom runtime SA (sf-trees-sa) ############"
U=$(url sf-trees-sa); T=$(id_tok "$U")
echo "curl -H 'Authorization: Bearer <ID_TOKEN>' '$U/ask?q=...'"
curl -s --max-time 150 -H "Authorization: Bearer $T" "$U/ask?q=$Q" | summarize
echo "   (data identity = ca-demo-runtime SA)"

echo; echo "############ Model 3 — impersonation (sf-trees-impersonate) ############"
U=$(url sf-trees-impersonate); T=$(id_tok "$U")
echo "curl -H 'Authorization: Bearer <ID_TOKEN>' '$U/ask?q=...'"
curl -s --max-time 150 -H "Authorization: Bearer $T" "$U/ask?q=$Q" | summarize
echo "   (runtime SA ca-demo-caller has NO data roles; impersonates ca-demo-target)"

echo; echo "############ Model 4 — end-user OAuth (sf-trees-enduser) ############"
U=$(url sf-trees-enduser); IDT=$(id_tok "$U"); UT=$(usr_tok)
echo "-- negative: no auth -> 403 (edge closed)"
curl -s -o /dev/null -w "   HTTP %{http_code}\n" --max-time 60 "$U/ask?q=test"
echo "-- negative: only access token (no ID token) -> 401 (not an invocation credential)"
curl -s -o /dev/null -w "   HTTP %{http_code}\n" --max-time 60 -H "Authorization: Bearer $UT" "$U/ask?q=test"
echo "-- positive: two headers -> 200 (invocation via ID token; data via end-user token)"
echo "curl -H 'X-Serverless-Authorization: Bearer <ID_TOKEN>' -H 'Authorization: Bearer <END_USER_TOKEN>' '$U/ask?q=...'"
curl -s --max-time 150 \
  -H "X-Serverless-Authorization: Bearer $IDT" \
  -H "Authorization: Bearer $UT" \
  "$U/ask?q=$Q" | summarize
echo "   (data identity = the forwarded end user; runtime SA has NO data roles)"
