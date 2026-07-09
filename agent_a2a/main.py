"""
A2A (Agent-to-Agent) Approach — exposes the "verified / example query matched"
signal as dedicated top-level message parts.

Why this file exists (vs. inline_chat/main.py / agent_stateless/main.py):

    The A2A streaming endpoint (`.../v1/message:stream`) is a different transport
    (raw HTTP JSON) that surfaces a verified-query hit as two dedicated message
    parts tagged via `metadata.gda_message.subType`:
        - "matched_query_question"  -> the verified query's NL question
        - "matched_query_sql"       -> the verified query's authored SQL
    These are exactly what the BigQuery Conversational Analytics UI renders as
    the little "verified" checkmark.

    NOTE: the native `DataChatServiceClient.chat()` path (used by the other
    scripts) ALSO surfaces the match as structured data — a `data.matched_query`
    message plus a `citation` with `source_type` + byte anchors — so A2A is not
    the *only* structured path, just the flattest, SDK-free one. Pick A2A when
    you want plain JSON parts without the SDK; pick native when you want the
    citation to also tell you *which span* of the answer the query backs.

This script:
    1. Creates (or reuses) a persistent Data Agent whose published context
       carries ONE authored `ExampleQuery` (a "verified query").
    2. Fetches the agent's public A2A AgentCard to discover its stream URL.
    3. Sends a question that matches the verified query over A2A.
    4. Prints the A2A stream response — raw verbatim by default, or a compact
       typed summary with `--parse`.

Run (same setup as the other scripts — ADC via `gcloud auth application-default login`):
    pip install google-cloud-geminidataanalytics requests
    python3 agent_a2a/main.py            # raw verbatim JSON
    python3 agent_a2a/main.py --parse    # typed, one-line-per-part summary
"""
import json
import sys
import uuid

import requests
import google.auth
from google.auth.transport.requests import Request as AuthRequest
from google.cloud import geminidataanalytics

# Output mode:
#   (default)  -> print the raw A2A JSON stream VERBATIM
#   --parse    -> decode each envelope/part into a compact, typed summary
#                 (task lifecycle + gda_message.subType parts)
PARSE = "--parse" in sys.argv

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
print(f"\n--- Asking over A2A ---\nQ: {question}{'  [--parse]' if PARSE else ''}")
resp = requests.post(stream_url, json=payload, headers=headers, timeout=300)
resp.raise_for_status()
raw = resp.content.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 4. Emit the A2A stream — raw verbatim, or a typed summary with --parse
# ---------------------------------------------------------------------------
def _preview(text, limit=140):
    s = " ".join(str(text).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _describe_part(part: dict) -> str:
    """One A2A content/artifact part -> 'subType: value'. Parts carry their type
    in metadata.gda_message.subType and hold either `text` or structured `data`."""
    st = ((part.get("metadata") or {}).get("gda_message") or {}).get("subType", "?")
    text = part.get("text")
    if text:
        return f"{st}: {_preview(text)}"
    if "data" in part:
        data = (part.get("data") or {}).get("data", {})
        if {"projectId", "datasetId", "tableId"} <= set(data):  # bigquery_table_reference
            return f"{st}: {data['projectId']}.{data['datasetId']}.{data['tableId']}"
        if "jobId" in data:
            return f"{st}: job_id={data.get('jobId')} location={data.get('location')}"
        return f"{st}: {_preview(json.dumps(data)) if data else '(empty)'}"
    return f"{st}: (empty)"


def _describe_envelope(env: dict) -> list:
    """A2A envelopes come in three shapes: task / statusUpdate / artifactUpdate."""
    lines = []
    if "task" in env:
        t = env["task"]
        lines.append(f"TASK: state={t.get('status', {}).get('state')} id={t.get('id')}")
    elif "statusUpdate" in env:
        su = env["statusUpdate"]
        status = su.get("status", {})
        msg = status.get("message")
        if msg:
            for part in msg.get("content", []) or []:
                lines.append(f"STATUS {_describe_part(part)}")
        else:
            fin = " (final)" if su.get("final") else ""
            lines.append(f"STATUS: state={status.get('state')}{fin}")
    elif "artifactUpdate" in env:
        art = env["artifactUpdate"].get("artifact", {})
        name = art.get("name")
        for part in art.get("parts", []) or []:
            lines.append(f"ARTIFACT[{name}] {_describe_part(part)}")
    return lines


print("\n" + "=" * 60)
print(f"A2A RESPONSE (HTTP {resp.status_code}){'  [--parse]' if PARSE else ''}")
print("=" * 60)
if PARSE:
    for env in json.loads(raw):
        for line in _describe_envelope(env):
            print(line)
else:
    print(raw)
