"""
Fully Stateful Chat via ConversationReference.

This is the counterpart to agent_stateless/main.py:

    - agent_stateless/main.py uses `DataAgentContext` -> stateful AGENT, stateless
      CHAT. The server remembers the agent's saved schema/config but keeps NO
      conversation history. Needs only `geminidataanalytics.googleapis.com`.

    - THIS script uses `ConversationReference` -> stateful AGENT + stateful CHAT.
      The server persists conversation history in a named `Conversation` object,
      so follow-up turns remember earlier ones.

      >>> This path additionally REQUIRES the `cloudaicompanion.googleapis.com`
          (Gemini for Google Cloud) API to be enabled:
              gcloud services enable cloudaicompanion.googleapis.com \\
                  --project=<PROJECT_ID>

Both talk to the SAME persistent agent (same agent_id as agent_stateless/main.py);
this script ensures that agent exists, then chats through a Conversation.

Run:
    python3 agent_stateful/main.py            # raw verbatim proto dump
    python3 agent_stateful/main.py --parse    # typed, one-line summary
"""
import sys
from google.cloud import geminidataanalytics
from google.protobuf import field_mask_pb2

# Configuration
billing_project = "kenly-dev-auto-240320261552"
location = "global"
agent_id = "sf-trees-analyst-test"      # same persistent agent as the agent script
convo_id = "sf-trees-convo-test"

# Output mode:
#   (default)  -> print each streamed response VERBATIM (raw proto text)
#   --parse    -> decode every message kind into a compact, typed summary
PARSE = "--parse" in sys.argv

# Question (same as the other scripts)
question = "Which species of tree is most prevalent?"

# Verified / example query (same as the other scripts)
VERIFIED_NLQ = "Which species of tree is most prevalent?"
VERIFIED_SQL = (
    "SELECT species, COUNT(*) AS tree_count\n"
    "FROM `bigquery-public-data.san_francisco.street_trees`\n"
    "GROUP BY species\n"
    "ORDER BY tree_count DESC\n"
    "LIMIT 10"
)


# ---------------------------------------------------------------------------
# Parser: turn one streamed `response` into a compact, typed summary.
# (Identical decoder to agent_stateless/main.py / inline_chat/main.py.)
# ---------------------------------------------------------------------------
def _preview(text, limit=140):
    s = " ".join(str(text).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _describe(response):
    """Human-readable summary of a single streamed response, covering every
    SystemMessage kind plus the (separate) citation field."""
    sm = response.system_message
    prefix = f"[grp {sm.group_id}]"
    kind = sm._pb.WhichOneof("kind")
    lines = []

    if kind == "text":
        ttype = sm.text.text_type.name  # THOUGHT / FINAL_RESPONSE / FOLLOWUP_QUESTIONS
        parts = list(sm.text.parts)
        if ttype == "FOLLOWUP_QUESTIONS":
            lines.append(f"{prefix} TEXT/{ttype} ({len(parts)} part(s)):")
            for p in parts:
                lines.append(f"    - {_preview(p)}")
        else:
            lines.append(f"{prefix} TEXT/{ttype} ({len(parts)} part(s)): {_preview(' | '.join(parts))}")

    elif kind == "data":
        sub = sm.data._pb.WhichOneof("kind")
        if sub == "query":
            ds = []
            for d in sm.data.query.datasources:
                if "bigquery_table_reference" in d:
                    b = d.bigquery_table_reference
                    ds.append(f"{b.project_id}.{b.dataset_id}.{b.table_id}")
                else:
                    ds.append("<non-bigquery datasource>")
            lines.append(f"{prefix} DATA/query: datasources={ds}")
        elif sub == "generated_sql":
            lines.append(f"{prefix} DATA/generated_sql: {_preview(sm.data.generated_sql)}")
        elif sub == "matched_query":
            eq = sm.data.matched_query.example_query
            lines.append(
                f"{prefix} DATA/matched_query: NLQ={eq.natural_language_question!r} "
                f"sql={_preview(eq.sql_query)!r}"
            )
        elif sub == "big_query_job":
            j = sm.data.big_query_job
            lines.append(f"{prefix} DATA/big_query_job: job_id={j.job_id} location={j.location}")
        elif sub == "result":
            r = sm.data.result
            cols = [f"{f.name}:{getattr(f, 'type_', '')}" for f in r.schema.fields]
            lines.append(f"{prefix} DATA/result: name={r.name!r} rows={len(r.data)} cols={cols}")
        else:
            lines.append(f"{prefix} DATA/{sub}")

    elif kind == "schema":
        lines.append(f"{prefix} SCHEMA/{sm.schema._pb.WhichOneof('kind')}")

    elif kind == "chart":
        sub = sm.chart._pb.WhichOneof("kind")
        if sub == "query":
            lines.append(f"{prefix} CHART/query: data_result_name={sm.chart.query.data_result_name!r}")
        elif sub == "result":
            vc = dict(sm.chart.result.vega_config)
            try:
                img_bytes = len(sm.chart.result.image.data)
            except Exception:  # noqa: BLE001
                img_bytes = 0
            lines.append(
                f"{prefix} CHART/result: mark={vc.get('mark')!r} "
                f"title={_preview(vc.get('title', ''))!r} image_bytes={img_bytes}"
            )
        else:
            lines.append(f"{prefix} CHART/{sub}")

    elif kind == "analysis":
        lines.append(f"{prefix} ANALYSIS/{sm.analysis._pb.WhichOneof('kind')}")

    elif kind == "error":
        lines.append(f"{prefix} ERROR: {_preview(sm.error.text)}")

    elif kind == "example_queries":
        lines.append(f"{prefix} EXAMPLE_QUERIES")

    else:
        lines.append(f"{prefix} <unknown kind: {kind}>")

    # `citation` is a top-level field (sibling of the kind oneof) and usually
    # rides along with a FINAL_RESPONSE text message.
    if "citation" in sm and sm.citation.sources:
        for src in sm.citation.sources:
            # source_type is a protobuf ONEOF: uri | example_query | glossary_term
            stype = src._pb.WhichOneof("source_type")
            lines.append(f"    ↳ citation: id={src.id!r} source_type={stype!r} title={_preview(src.title)!r}")
        for a in sm.citation.anchors:
            tm = a.text_message_anchor
            lines.append(
                f"    ↳ anchor: bytes[{tm.start_offset_bytes}:{tm.end_offset_bytes}] "
                f"-> {list(tm.source_ids)}"
            )

    return "\n".join(lines)


def emit(response):
    print(_describe(response) if PARSE else response)


print("="*40)
print(f"Stateful Conversation Approach (ConversationReference){'  [--parse]' if PARSE else ''}")
print("="*40)

# Clients
data_agent_client = geminidataanalytics.DataAgentServiceClient()
data_chat_client = geminidataanalytics.DataChatServiceClient()

# Data Source + Context (same as the other scripts)
bigquery_table_reference_1 = geminidataanalytics.BigQueryTableReference()
bigquery_table_reference_1.project_id = "bigquery-public-data"
bigquery_table_reference_1.dataset_id = "san_francisco"
bigquery_table_reference_1.table_id = "street_trees"

datasource_references = geminidataanalytics.DatasourceReferences()
datasource_references.bq.table_references = [bigquery_table_reference_1]

context = geminidataanalytics.Context()
context.datasource_references = datasource_references
context.system_instruction = "You are an expert urban forester in San Francisco. Analyze tree data accurately."
context.example_queries = [
    geminidataanalytics.ExampleQuery(
        natural_language_question=VERIFIED_NLQ,
        sql_query=VERIFIED_SQL,
    )
]

agent_name = f"projects/{billing_project}/locations/{location}/dataAgents/{agent_id}"

# 1. Ensure the persistent Data Agent exists (create, or update its context on reuse)
try:
    print(f"\n--- Ensuring Persistent Data Agent: {agent_id} ---")
    analytics_agent = geminidataanalytics.DataAnalyticsAgent()
    analytics_agent.published_context = context

    data_agent = geminidataanalytics.DataAgent()
    data_agent.display_name = "San Francisco Trees Analyst"
    data_agent.description = "Persistent agent for analyzing SF urban forest metrics"
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
        print(f"Agent {agent_id} already exists. Updating its published context to match...")
        update_agent = geminidataanalytics.DataAgent()
        update_agent.name = agent_name
        update_agent.data_analytics_agent.published_context = context
        data_agent_client.update_data_agent(
            request=geminidataanalytics.UpdateDataAgentRequest(
                data_agent=update_agent,
                update_mask=field_mask_pb2.FieldMask(
                    paths=["data_analytics_agent.published_context"]
                ),
            )
        )
    else:
        print(f"Error ensuring agent: {e}")
        sys.exit(1)

agent_context = geminidataanalytics.DataAgentContext()
agent_context.data_agent = agent_name
agent_context.context_version = geminidataanalytics.DataAgentContext.ContextVersion.PUBLISHED

# 2. Ensure the server-side Conversation exists (this is what holds history)
try:
    print(f"\n--- Ensuring Conversation: {convo_id} ---")
    convo_name = f"projects/{billing_project}/locations/{location}/conversations/{convo_id}"

    convo_obj = geminidataanalytics.Conversation()
    convo_obj.agents = [agent_name]

    data_chat_client.create_conversation(
        request=geminidataanalytics.CreateConversationRequest(
            parent=f"projects/{billing_project}/locations/{location}",
            conversation_id=convo_id,
            conversation=convo_obj,
        )
    )
    print(f"Created Conversation: {convo_name}")
except Exception as e:  # noqa: BLE001
    if "already exists" in str(e).lower():
        print(f"Conversation {convo_id} already exists. Reusing it!")
    else:
        raise

# 3. Chat through the Conversation (server persists history across turns)
try:
    print(f"\n--- Chatting via ConversationReference ---")
    print(f"Question: {question}")

    convo_ref = geminidataanalytics.ConversationReference()
    convo_ref.conversation = convo_name
    convo_ref.data_agent_context = agent_context

    chat_request = geminidataanalytics.ChatRequest()
    chat_request.parent = f"projects/{billing_project}/locations/{location}"
    chat_request.conversation_reference = convo_ref
    chat_request.messages = [geminidataanalytics.Message(
        user_message=geminidataanalytics.UserMessage(text=question)
    )]

    print("Calling chat with ConversationReference...")
    for response in data_chat_client.chat(request=chat_request):
        emit(response)

except Exception as e:  # noqa: BLE001
    print(f"Error chatting with persistent conversation: {e}")
    if "cloudaicompanion" in str(e):
        print("\n   Hint: ConversationReference needs the Gemini for Google Cloud API. Enable it, then retry:")
        print(f"     gcloud services enable cloudaicompanion.googleapis.com --project={billing_project}")
