"""
A2A (Agent-to-Agent) Approach — the ONLY path that exposes the
"verified / example query matched" signal as structured data.

Why this file exists (vs. test_caapi_bq_chat.py / test_caapi_bq_agent.py):

    The native `DataChatServiceClient.chat()` stream (used by the other two
    scripts) does NOT surface a discrete "this answer reused one of your
    authored example_queries" signal. When a question matches a verified
    query, the only trace on the native path is inside the model's THOUGHT
    prose ("...Example Query 3... it's a direct hit... I will execute this
    exact example query") — there is no typed field to key off, and the
    `SystemMessage.example_queries` message is never emitted for agent chat.

    The A2A streaming endpoint (`.../v1/message:stream`) DOES emit it, as two
    dedicated message parts tagged via `metadata.gda_message.subType`:
        - "matched_query_question"  -> the verified query's NL question
        - "matched_query_sql"       -> the verified query's authored SQL
    These are exactly what the BigQuery Conversational Analytics UI renders as
    the little "verified" checkmark. So if you need to programmatically detect
    a verified-query hit, this is the path to use.

This script:
    1. Creates (or reuses) a persistent Data Agent whose published context
       carries ONE authored `ExampleQuery` (a "verified query").
    2. Fetches the agent's public A2A AgentCard to discover its stream URL.
    3. Sends a question that matches the verified query over A2A.
    4. Parses the stream and highlights the matched_query_* signal.

Run (same setup as the other scripts — ADC via `gcloud auth application-default login`):
    pip install google-cloud-geminidataanalytics requests
    python3 test_caapi_bq_a2a.py
"""
import json
import uuid

import requests
import google.auth
from google.auth.transport.requests import Request as AuthRequest
from google.cloud import geminidataanalytics

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
billing_project = "kenly-dev-auto-240320261552"
location = "global"
agent_id = "sf-trees-analyst-a2a"  # distinct from the other scripts' agent

# A question that should MATCH the verified query we author below.
question = "Which species of tree is most prevalent?"

# The verified / example query we attach to the agent. Because the NL question
# closely matches `question` above, the CA engine should treat it as a "hit"
# and emit the matched_query_* signal over A2A.
VERIFIED_NLQ = "Which species of tree is most prevalent?"
VERIFIED_SQL = (
    "SELECT species, COUNT(*) AS tree_count\n"
    "FROM `bigquery-public-data.san_francisco.street_trees`\n"
    "GROUP BY species\n"
    "ORDER BY tree_count DESC\n"
    "LIMIT 10"
)

print("=" * 60)
print("A2A Approach (captures the 'verified query matched' signal)")
print("=" * 60)

# ---------------------------------------------------------------------------
# 1. Create (or reuse) a persistent agent WITH a verified query
# ---------------------------------------------------------------------------
data_agent_client = geminidataanalytics.DataAgentServiceClient()

bq_ref = geminidataanalytics.BigQueryTableReference()
bq_ref.project_id = "bigquery-public-data"
bq_ref.dataset_id = "san_francisco"
bq_ref.table_id = "street_trees"

datasource_references = geminidataanalytics.DatasourceReferences()
datasource_references.bq.table_references = [bq_ref]

context = geminidataanalytics.Context()
context.datasource_references = datasource_references
context.system_instruction = (
    "You are an expert urban forester in San Francisco. Analyze tree data accurately."
)
# THE KEY BIT: an authored/verified example query. Without at least one of
# these on the agent, there is nothing for the engine to "match", and the
# matched_query_* signal will never fire.
context.example_queries = [
    geminidataanalytics.ExampleQuery(
        natural_language_question=VERIFIED_NLQ,
        sql_query=VERIFIED_SQL,
    )
]

agent_name = f"projects/{billing_project}/locations/{location}/dataAgents/{agent_id}"
try:
    print(f"\n--- Creating agent with 1 verified query: {agent_id} ---")
    analytics_agent = geminidataanalytics.DataAnalyticsAgent()
    analytics_agent.published_context = context

    data_agent = geminidataanalytics.DataAgent()
    data_agent.display_name = "SF Trees Analyst (A2A + verified query)"
    data_agent.description = "Agent with an authored example_query, for A2A match-signal demo"
    data_agent.data_analytics_agent = analytics_agent

    data_agent_client.create_data_agent(
        request=geminidataanalytics.CreateDataAgentRequest(
            parent=f"projects/{billing_project}/locations/{location}",
            data_agent_id=agent_id,
            data_agent=data_agent,
        )
    )
    print(f"Created agent: {agent_name}")
except Exception as e:  # noqa: BLE001
    if "already exists" in str(e).lower():
        # NOTE: create is idempotent-by-reuse here, but it will NOT update the
        # published context. If you change VERIFIED_SQL above, update the agent
        # (data_agent_client.update_data_agent) or use a fresh agent_id.
        print(f"Agent {agent_id} already exists. Reusing it!")
    else:
        raise


# ---------------------------------------------------------------------------
# 2. Discover the A2A stream URL from the agent's public AgentCard
# ---------------------------------------------------------------------------
def _adc_token() -> str:
    """Mint a bearer token from Application Default Credentials."""
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(AuthRequest())
    return creds.token


card_url = (
    "https://geminidataanalytics.googleapis.com/v1beta/a2a/"
    f"projects/{billing_project}/locations/{location}/dataAgents/{agent_id}/v1/card"
)
headers = {"Authorization": f"Bearer {_adc_token()}", "Content-Type": "application/json"}

print(f"\n--- Fetching A2A AgentCard ---\n{card_url}")
card = requests.get(card_url, headers=headers, timeout=30)
card.raise_for_status()
stream_url = card.json()["url"].rstrip("/") + "/v1/message:stream"
print(f"Stream endpoint: {stream_url}")


# ---------------------------------------------------------------------------
# 3. Send the question over A2A and stream the response
# ---------------------------------------------------------------------------
payload = {
    "request": {
        "messageId": str(uuid.uuid4()),
        "role": "ROLE_USER",
        "content": {"text": question},
    }
}
print(f"\n--- Asking over A2A ---\nQ: {question}")
resp = requests.post(stream_url, json=payload, headers=headers, timeout=300)
resp.raise_for_status()
envelopes = json.loads(resp.content.decode("utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# 4. Parse the stream and pull out the match signal
# ---------------------------------------------------------------------------
def parts_from_envelope(env: dict) -> list:
    """A2A envelopes come in three shapes; content parts can live in any."""
    if "statusUpdate" in env:
        return env["statusUpdate"].get("status", {}).get("message", {}).get("content", []) or []
    if "task" in env:
        return env["task"].get("status", {}).get("message", {}).get("content", []) or []
    if "artifactUpdate" in env:
        return env["artifactUpdate"].get("artifact", {}).get("parts", []) or []
    return []


def sub_type(part: dict) -> str:
    return ((part.get("metadata") or {}).get("gda_message") or {}).get("subType", "")


def task_error(env: dict) -> str | None:
    """Return the error text if an envelope reports a FAILED task, else None."""
    status = (env.get("statusUpdate", {}).get("status")
              or env.get("task", {}).get("status") or {})
    if status.get("state") == "TASK_STATE_FAILED":
        parts = status.get("message", {}).get("content", []) or []
        return " ".join(p.get("text", "") for p in parts).strip() or "<failed, no detail>"
    return None


matched_question = None
matched_sql = None
final_answer_parts = []
subtypes_seen = {}
error_text = None

for env in envelopes:
    error_text = error_text or task_error(env)
    for part in parts_from_envelope(env):
        st = sub_type(part)
        subtypes_seen[st] = subtypes_seen.get(st, 0) + 1
        text = part.get("text", "") or ""
        if st == "matched_query_question":
            matched_question = text.strip()
        elif st == "matched_query_sql":
            matched_sql = text.strip().removeprefix("```sql").removesuffix("```").strip()
        elif st == "final_response":
            if text.strip():
                final_answer_parts.append(text.strip())

print("\n" + "=" * 60)
print("RESULT")
print("=" * 60)
print(f"subTypes seen: {subtypes_seen}")

if error_text:
    print(f"\n❌ Stream reported a FAILED task:\n   {error_text}")
    if "cloudaicompanion" in error_text:
        print("\n   Hint: the A2A stream creates a server-side conversation, which needs the")
        print("   Gemini for Google Cloud API. Enable it, then retry:")
        print(f"     gcloud services enable cloudaicompanion.googleapis.com --project={billing_project}")
    raise SystemExit(1)

verified_query_matched = matched_question is not None
print(f"\n✅ verified_query_matched: {verified_query_matched}")
if verified_query_matched:
    print(f"   matched_query_question: {matched_question!r}")
    print(f"   matched_query_sql:\n{matched_sql}")
else:
    print("   (No matched_query_* parts — the question did not hit a verified query,")
    print("    or the agent has no example_queries. This is exactly the case where")
    print("    the native chat() path also gives you no structured signal.)")

print(f"\nFinal answer:\n{chr(10).join(final_answer_parts) or '<none>'}")
