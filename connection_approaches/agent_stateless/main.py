import sys
from google.cloud import geminidataanalytics
from google.protobuf import field_mask_pb2

# Configuration
billing_project = "kenly-dev-auto-240320261552"
location = "global"
agent_id = "sf-trees-analyst-test"

# Output mode:
#   (default)  -> print each streamed response VERBATIM (raw proto text)
#   --parse    -> decode every message kind into a compact, typed summary
#                 (text / data / schema / chart / analysis / error + citations)
PARSE = "--parse" in sys.argv

# Multi-turn mode:
#   --followup -> run TWO turns. Because this path is STATELESS chat, the server
#                 keeps no history, so we replay the whole transcript ourselves in
#                 the `messages` list (user turns + the system messages we got
#                 back). Turn 2 is deliberately REFERENTIAL ("that top species"),
#                 so it can only be answered if the history really was carried.
FOLLOWUP = "--followup" in sys.argv

# Question (same as the A2A script)
question = "Which species of tree is most prevalent?"
# Turn 2 refers back to turn 1 ("that top species"); turn 3 refers back to BOTH
# ("those trees" = that species + that caretaker) — a multi-hop memory test.
FOLLOWUP_QUESTIONS = [
    "For that top species, which caretaker manages the most trees?",
    "And what is the average DBH of those trees?",
]

# Verified / example query (same as the A2A script)
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
print(f"Persistent Agent Approach (vs Statless Chat API){'  [--parse]' if PARSE else ''}")
print("="*40)

# Clients
data_agent_client = geminidataanalytics.DataAgentServiceClient()
data_chat_client = geminidataanalytics.DataChatServiceClient()

# Data Source Reference (same as the A2A script)
bigquery_table_reference_1 = geminidataanalytics.BigQueryTableReference()
bigquery_table_reference_1.project_id = "bigquery-public-data"
bigquery_table_reference_1.dataset_id = "san_francisco"
bigquery_table_reference_1.table_id = "street_trees"

datasource_references = geminidataanalytics.DatasourceReferences()
datasource_references.bq.table_references = [bigquery_table_reference_1]

# Context Definition (Saved to the server) — same system instruction + verified
# query as the A2A script.
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

# 1. Create Data Agent (Persistent Configuration)
try:
    print(f"\n--- Creating Persistent Data Agent: {agent_id} ---")

    analytics_agent = geminidataanalytics.DataAnalyticsAgent()
    analytics_agent.published_context = context # Save it as published context

    data_agent = geminidataanalytics.DataAgent()
    data_agent.display_name = "San Francisco Trees Analyst"
    data_agent.description = "Persistent agent for analyzing SF urban forest metrics"
    data_agent.data_analytics_agent = analytics_agent

    request = geminidataanalytics.CreateDataAgentRequest(
        parent=f"projects/{billing_project}/locations/{location}",
        data_agent_id=agent_id,
        data_agent=data_agent,
    )

    print("Calling create_data_agent...")
    operation = data_agent_client.create_data_agent(request=request)
    print("Agent created or checked. Operation running...")
    print(f"Agent Name: {agent_name}")

except Exception as e:
    # If it already exists, reuse it — but push the current context so the saved
    # config (incl. the verified example query) matches this script exactly.
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
        print(f"Agent Name: {agent_name}")
    else:
        print(f"Error creating agent: {e}")
        sys.exit(1)

# 2. Stateful Chat (Using the persistent Agent)
try:
    print(f"\n--- Chatting with Agent: {agent_id} ---")
    print(f"Question: {question}")

    # Define reference to the AGENT, not the datasource!
    agent_context = geminidataanalytics.DataAgentContext()
    agent_context.data_agent = agent_name
    agent_context.context_version = geminidataanalytics.DataAgentContext.ContextVersion.PUBLISHED

    # `history` IS the conversation. The server stores nothing on this path, so
    # whatever we put in `messages` is the agent's entire memory for the turn.
    history = []

    def send_turn(text):
        """Append a user turn, send the WHOLE transcript, and fold the streamed
        system messages back into the history so the next turn can see them."""
        history.append(geminidataanalytics.Message(
            user_message=geminidataanalytics.UserMessage(text=text)
        ))

        # We send the request WITHOUT inline source references! The server uses the saved agent config.
        chat_request = geminidataanalytics.ChatRequest()
        chat_request.parent = f"projects/{billing_project}/locations/{location}"
        chat_request.data_agent_context = agent_context
        chat_request.messages = history          # <-- full client-side transcript

        replies = []
        for response in data_chat_client.chat(request=chat_request):
            emit(response)
            replies.append(response.system_message)

        # Carry the agent's replies forward as history for the next turn.
        for sm in replies:
            history.append(geminidataanalytics.Message(system_message=sm))

    print("Calling chat with persistent agent...")
    send_turn(question)

    if FOLLOWUP:
        # Turns 2 and 3 are REFERENTIAL — resolvable only from the transcript we
        # replay in `messages` on every call.
        for i, q in enumerate(FOLLOWUP_QUESTIONS, start=2):
            print(f"\n--- Turn {i} (history replayed client-side: "
                  f"{len(history)} messages) ---")
            print(f"Question: {q}")
            send_turn(q)

    # NOTE: `DataAgentContext` is STATELESS chat against a stateful (published)
    # agent — the server keeps no history. Multi-turn still works (see
    # --followup above), but it is YOUR job: replay the transcript in `messages`
    # on every call. This needs only `geminidataanalytics.googleapis.com`;
    # NO `cloudaicompanion` enablement.
    # For server-managed multi-turn history, see agent_stateful/main.py
    # (which uses `ConversationReference` and DOES require cloudaicompanion).

except Exception as e:
    print(f"Error chatting with agent: {e}")
