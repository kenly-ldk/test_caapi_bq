"""
One empirical control for the "is BigQuery identity coupled to the caller?" proof.

Runs a single inline-context chat() against a PRIVATE BigQuery table, where:
    --caller-token   the OAuth token the CA API client is BUILT with (the caller /
                     control-plane identity, which the API propagates to BigQuery)
    --creds-token    (optional) a token stuffed into ChatRequest.credentials — the
                     field the docs describe for LOOKER. If BigQuery honored it we
                     could inject a separate data-plane identity; the proof is that
                     it does NOT.

Exit code 0 = the query SUCCEEDED (BigQuery let the effective principal read).
Exit code 3 = the query was DENIED by BigQuery (permission error surfaced).
Exit code 1 = an unexpected error (misconfig, etc.).

Called once per control (A–D) by validate_identity.sh.
"""
import argparse
import sys

from google.cloud import geminidataanalytics
from google.oauth2.credentials import Credentials as OAuthCredentials

DENY_MARKERS = ("permission denied", "access denied", "does not have",
                "bigquery.tables.getdata", "bigquery.jobs.create", "403")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--caller-token", required=True)
    ap.add_argument("--creds-token", default="")
    ap.add_argument("--project", required=True)
    ap.add_argument("--location", default="global")
    ap.add_argument("--table", required=True,
                    help="private table as project.dataset.table")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    project_id, dataset_id, table_id = args.table.split(".")

    client = geminidataanalytics.DataChatServiceClient(
        credentials=OAuthCredentials(token=args.caller_token)
    )

    bq_ref = geminidataanalytics.BigQueryTableReference()
    bq_ref.project_id = project_id
    bq_ref.dataset_id = dataset_id
    bq_ref.table_id = table_id

    datasource_references = geminidataanalytics.DatasourceReferences()
    datasource_references.bq.table_references = [bq_ref]

    inline_context = geminidataanalytics.Context()
    inline_context.system_instruction = "Analyze the table accurately."
    inline_context.datasource_references = datasource_references

    chat_request = geminidataanalytics.ChatRequest(
        inline_context=inline_context,
        parent=f"projects/{args.project}/locations/{args.location}",
        messages=[geminidataanalytics.Message(
            user_message=geminidataanalytics.UserMessage(
                text="How many rows are in the table?"
            )
        )],
    )

    # Optionally set the (Looker-shaped) credentials field. Per the REST schema:
    # Credentials.oauth (OAuthCredentials) -> token (TokenBased) -> access_token.
    if args.creds_token:
        creds = geminidataanalytics.Credentials()
        creds.oauth.token.access_token = args.creds_token
        chat_request.credentials = creds

    denied = False
    try:
        for response in client.chat(request=chat_request):
            sm = response.system_message
            if sm._pb.WhichOneof("kind") == "error":
                text = sm.error.text.lower()
                if any(m in text for m in DENY_MARKERS):
                    denied = True
                    print(f"[{args.label}] DENIED (stream error): {sm.error.text[:200]}")
    except Exception as e:  # noqa: BLE001
        text = str(e).lower()
        if any(m in text for m in DENY_MARKERS):
            print(f"[{args.label}] DENIED (exception): {str(e)[:200]}")
            return 3
        print(f"[{args.label}] UNEXPECTED ERROR: {str(e)[:300]}")
        return 1

    if denied:
        return 3
    print(f"[{args.label}] SUCCEEDED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
