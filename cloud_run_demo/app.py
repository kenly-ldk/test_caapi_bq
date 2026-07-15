"""
Cloud Run demo — one CA API call, four identity models.

This is the same inline-context `chat()` as `inline_chat/main.py` (SF street
trees, one verified example query), wrapped in a tiny web app so it can be
deployed to Cloud Run. The ONLY thing that changes between deployments is *which
credentials the CA API client is built with* — selected by the `IDENTITY_MODE`
env var. Whatever identity the client uses, the CA API propagates it to BigQuery
(there is no separate data-plane identity for BigQuery).

IDENTITY_MODE:
    adc         (default) Ambient Application Default Credentials. On Cloud Run
                that is the attached runtime service account (models 1/2); locally
                it is your user ADC (model L).
    impersonate The runtime SA impersonates $TARGET_SA (model 3). Requires the
                runtime SA to hold roles/iam.serviceAccountTokenCreator on it.
    end_user    Relay the caller's forwarded OAuth token (model 4): the WHOLE
                chat() — control plane AND BigQuery data plane — runs as the end
                user. Requires the inbound request to carry the user's own
                cloud-platform-scoped bearer token in the Authorization header.

Env:
    BILLING_PROJECT   GCP project for the API `parent` + BigQuery billing (required)
    LOCATION          CA API location (default: global)
    IDENTITY_MODE     adc | impersonate | end_user   (default: adc)
    TARGET_SA         target SA email, required when IDENTITY_MODE=impersonate

Routes:
    GET /            health check
    GET /ask?q=...   run one chat() turn; returns a compact typed summary as JSON
"""
import os

import google.auth
from flask import Flask, jsonify, request
from google.auth import impersonated_credentials
from google.cloud import geminidataanalytics
from google.oauth2.credentials import Credentials as OAuthCredentials

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

BILLING_PROJECT = os.environ.get("BILLING_PROJECT", "")
LOCATION = os.environ.get("LOCATION", "global")
IDENTITY_MODE = os.environ.get("IDENTITY_MODE", "adc").strip().lower()
TARGET_SA = os.environ.get("TARGET_SA", "")

# Same verified example query as the other scripts, so the demo shares one context.
VERIFIED_NLQ = "Which species of tree is most prevalent?"
VERIFIED_SQL = (
    "SELECT species, COUNT(*) AS tree_count\n"
    "FROM `bigquery-public-data.san_francisco.street_trees`\n"
    "GROUP BY species\n"
    "ORDER BY tree_count DESC\n"
    "LIMIT 10"
)

app = Flask(__name__)


def _credentials(bearer_token: str | None):
    """Pick the credentials the CA API client is built with, per IDENTITY_MODE.

    This single function is the whole point of the demo: the identity you return
    here is the identity the CA API propagates to BigQuery.
    """
    if IDENTITY_MODE == "end_user":
        # Model 4 — relay the end user's own OAuth token. No SA→user IAM needed;
        # the user's authorization rides inside the token. Requires OAuth setup so
        # the caller arrives with a cloud-platform-scoped token.
        if not bearer_token:
            raise ValueError(
                "IDENTITY_MODE=end_user requires an 'Authorization: Bearer <token>' "
                "header carrying the end user's cloud-platform-scoped OAuth token."
            )
        return OAuthCredentials(token=bearer_token)

    if IDENTITY_MODE == "impersonate":
        # Model 3 — runtime SA mints a token AS $TARGET_SA (which holds the CA+BQ
        # roles). Runtime SA needs roles/iam.serviceAccountTokenCreator on it.
        if not TARGET_SA:
            raise ValueError("IDENTITY_MODE=impersonate requires TARGET_SA to be set.")
        source, _ = google.auth.default(scopes=SCOPES)
        return impersonated_credentials.Credentials(
            source_credentials=source,
            target_principal=TARGET_SA,
            target_scopes=SCOPES,
        )

    # Model L/1/2 — ambient ADC (your user locally; the runtime SA on Cloud Run).
    creds, _ = google.auth.default(scopes=SCOPES)
    return creds


def _preview(text, limit=200):
    s = " ".join(str(text).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _summarize(response):
    """Compact typed summary of one streamed response (subset of the decoder in
    inline_chat/main.py — enough to prove the call worked and show the identity)."""
    sm = response.system_message
    kind = sm._pb.WhichOneof("kind")
    if kind == "text":
        return {"type": f"text/{sm.text.text_type.name}",
                "preview": _preview(" | ".join(sm.text.parts))}
    if kind == "data":
        sub = sm.data._pb.WhichOneof("kind")
        if sub == "big_query_job":
            j = sm.data.big_query_job
            return {"type": "data/big_query_job",
                    "job_id": j.job_id, "location": j.location}
        if sub == "result":
            r = sm.data.result
            return {"type": "data/result", "name": r.name, "rows": len(r.data)}
        return {"type": f"data/{sub}"}
    if kind == "chart":
        return {"type": "chart"}
    if kind == "error":
        return {"type": "error", "preview": _preview(sm.error.text)}
    return {"type": kind}


@app.get("/")
def health():
    return jsonify(status="ok", identity_mode=IDENTITY_MODE,
                   billing_project=BILLING_PROJECT, location=LOCATION)


@app.get("/ask")
def ask():
    question = request.args.get("q", VERIFIED_NLQ)
    if not BILLING_PROJECT:
        return jsonify(error="BILLING_PROJECT env var is required"), 500

    bearer = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer = auth_header[len("Bearer "):].strip()

    try:
        credentials = _credentials(bearer)
    except ValueError as e:
        return jsonify(error=str(e)), 400

    client = geminidataanalytics.DataChatServiceClient(credentials=credentials)

    bq_ref = geminidataanalytics.BigQueryTableReference()
    bq_ref.project_id = "bigquery-public-data"
    bq_ref.dataset_id = "san_francisco"
    bq_ref.table_id = "street_trees"

    datasource_references = geminidataanalytics.DatasourceReferences()
    datasource_references.bq.table_references = [bq_ref]

    inline_context = geminidataanalytics.Context()
    inline_context.system_instruction = (
        "You are an expert urban forester in San Francisco. Analyze tree data accurately."
    )
    inline_context.datasource_references = datasource_references
    inline_context.example_queries = [
        geminidataanalytics.ExampleQuery(
            natural_language_question=VERIFIED_NLQ, sql_query=VERIFIED_SQL
        )
    ]

    chat_request = geminidataanalytics.ChatRequest(
        inline_context=inline_context,
        parent=f"projects/{BILLING_PROJECT}/locations/{LOCATION}",
        messages=[geminidataanalytics.Message(
            user_message=geminidataanalytics.UserMessage(text=question)
        )],
    )

    try:
        events = [_summarize(r) for r in client.chat(request=chat_request)]
    except Exception as e:  # noqa: BLE001 — surface the identity/permission error to the caller
        return jsonify(identity_mode=IDENTITY_MODE, question=question,
                       error=str(e)), 502

    return jsonify(identity_mode=IDENTITY_MODE, question=question, events=events)


if __name__ == "__main__":
    # Local dev only; on Cloud Run gunicorn serves the app (see Dockerfile).
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
