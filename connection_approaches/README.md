# 🔌 Connection Approaches — invoking a BigQuery CA agent

Four runnable scripts showing four ways to connect to and invoke a BigQuery
Conversational Analytics (CA) agent. They share the **same question and context**
(SF street trees + one verified `example_query`) so their outputs are directly
comparable. For environment setup and run commands see the
[repo README](../README.md); for the IAM model behind all of them see
[../permissions-identity/README.md](../permissions-identity/README.md).

## 🚀 Overview of Approaches

### 1. Stateless Chat (`inline_chat/main.py`)
In the stateless approach, you define the data sources and instructions **inline** with every single request. The server doesn't remember your schema or rules.

*   **Best for**: Ad-hoc queries, transient sessions, or when schema changes frequently.
*   **Drawback**: Larger payload sizes (sending schema every time).

### 2. Persistent Agent — Stateless Chat (`agent_stateless/main.py`)
Create a named **Data Agent** on the server; its schema and rules are saved in the agent's configuration. You then chat with it by reference via `DataAgentContext`. The **agent** is stateful (remembers its saved config), but the **chat** is stateless — the server keeps no conversation history.

*   **Best for**: Production portals, repeatable workflows, fixed schemas — *without* enabling any extra API.
*   **Only needs**: `geminidataanalytics.googleapis.com`. **No `cloudaicompanion`.**
*   **Drawback**: No server-side history — multi-turn still works, but you resend prior turns yourself in the `messages` list (see [Multi-turn](#-multi-turn---followup--all-four-approaches-support-it)).

### 3. Persistent Agent — Stateful Conversation (`agent_stateful/main.py`)
Same persistent agent, but you chat *through* a named **`Conversation`** object via `ConversationReference`. Now the **server persists history**, so follow-up turns remember earlier ones automatically.

*   **Best for**: Multi-turn assistants, audit logs, shared context across a workflow.
*   **Requires**: `cloudaicompanion.googleapis.com` (Gemini for Google Cloud) — see the caution below.
*   **Drawback**: Extra API enablement; server-side conversation objects to manage.

### 4. A2A Streaming (`agent_a2a/main.py`)
The three approaches above call `DataChatServiceClient.chat()`. The A2A **streaming endpoint** (`.../v1/message:stream`) is a different transport (raw HTTP JSON) that surfaces the verified-query match as **dedicated top-level message parts**, via `metadata.gda_message.subType`:

*   `matched_query_question` — the verified query's natural-language question
*   `matched_query_sql` — the verified query's authored SQL

These are exactly what the BigQuery Conversational Analytics **UI renders as the "verified" checkmark**. (The native `chat()` path *also* exposes the match — as a `data.matched_query` message plus a `citation` with `source_type` + byte anchors — so A2A is not the *only* structured path; it's just the flattest, SDK-free one. See the A2A section for the full comparison.)

*   **Best for**: Programmatically detecting when an answer was backed by a verified/example query (governance, audit, "trusted answer" badges).
*   **Drawback**: Raw HTTP (no typed SDK client); you parse A2A envelopes yourself. Requires the agent to actually carry at least one `example_query`.

---

## 🔁 Multi-turn (`--followup`) — all four approaches support it

**Every** approach can hold a multi-turn conversation. What differs is **who
stores the history**, not whether follow-ups are possible.

Run a **3-turn** conversation on any of them:

```bash
# add --parse for the typed summary; omit it for the raw verbatim stream
python3 connection_approaches/inline_chat/main.py      --parse --followup
python3 connection_approaches/agent_stateless/main.py  --parse --followup
python3 connection_approaches/agent_stateful/main.py   --parse --followup
python3 connection_approaches/agent_a2a/main.py        --parse --followup
```

Both forms are captured per folder, mirroring the single-turn convention:

| Capture | Produced by |
|---|---|
| `output.verbatim.txt` / `output.parsed.txt` | single turn (`main.py` [`--parse`]) |
| `output.followup.verbatim.txt` / `output.followup.parsed.txt` | 3-turn run (`main.py --followup` [`--parse`]) |

The turns are deliberately **referential**, so they can only be answered if
history really carried:

1. *"Which species of tree is most prevalent?"*
2. *"For **that top species**, which caretaker manages the most trees?"* → needs turn 1
3. *"And what is the average DBH of **those trees**?"* → needs turns 1 **and** 2

| Approach | Who stores history | What you send on turn N |
|---|---|---|
| `inline_chat` | **you** (client-side) | the whole transcript in `messages` + the inline context, every call |
| `agent_stateless` | **you** (client-side) | the whole transcript in `messages` (context comes from the published agent) |
| `agent_stateful` | **the server** (`Conversation`) | only the new question |
| `agent_a2a` | **the server** (via `contextId`) | only the new question + the `contextId` to rejoin |

### Client-side history (`inline_chat`, `agent_stateless`)

The server keeps nothing, so the `messages` list **is** the agent's memory: append
your question, send the full transcript, then fold the streamed replies back in.

```python
history = []

def send_turn(text):
    history.append(geminidataanalytics.Message(
        user_message=geminidataanalytics.UserMessage(text=text)))
    chat_request.messages = history          # <-- full client-side transcript
    replies = []
    for response in data_chat_client.chat(request=chat_request):
        replies.append(response.system_message)
    for sm in replies:                       # carry the agent's replies forward
        history.append(geminidataanalytics.Message(system_message=sm))
```

Note the history grows fast — in the captured runs it reaches **33 messages** by
turn 3, and every message is resent on each call. Truncation is your job.

#### Is replaying `messages` an *officially supported* mechanism? Yes.

This is the documented design for the stateless modes, not a workaround:

*   [**State management**](https://docs.cloud.google.com/gemini/data-agents/conversational-analytics-api/state-management)
    — for `DataAgentContext` and `InlineContext` the conversation history is
    *"Managed by your application"*: **"Your application must manage and provide
    the full conversation history with each request."** (For
    `ConversationReference` it is *"Managed by the API… You send only the new
    message for each turn."*)
*   [**API overview**](https://docs.cloud.google.com/gemini/data-agents/conversational-analytics-api/overview)
    — "Chat by using a data agent reference … **For multi-turn conversations, your
    application must manage and provide the conversation history with each
    request.**"
*   [**`chat` REST reference**](https://docs.cloud.google.com/gemini/data-agents/reference/rest/v1/projects.locations/chat)
    — the field is `messages[] object (Message)`, *Required. **"Content of current
    conversation."*** It is a **repeated** field describing the conversation (not
    a single message), and it is the only field in `ChatRequest` that carries
    conversational content — so it is the vehicle the sentences above refer to.

> [!NOTE]
> **What the docs do *not* pin down: the exact shape of the replayed history.**
> They mandate providing "the full conversation history" but don't prescribe
> *which* messages to include. Replaying **every** streamed `system_message`
> verbatim (as the code above does) is *our implementation choice* — it is the
> natural reading of a repeated `Message` with a `user_message` / `system_message`
> oneof, and it demonstrably works (see the proof below). Equally valid-looking
> alternatives the docs neither endorse nor forbid: replaying only the user turns
> plus each `FINAL_RESPONSE`, or summarizing older turns to bound payload growth.
> Treat the replay *strategy* as tunable, not canonical.

### Server-side history (`agent_stateful`, `agent_a2a`)

`agent_stateful` sends only the new question; the named `Conversation` holds the
rest. `agent_a2a` gets the same thing over raw HTTP by echoing back the
**`contextId`** the first response returned — and that id is literally a
conversation resource:

```
contextId = projects/<project>/locations/global/conversations/01b8c2df-6fac-…
```

> [!TIP]
> That means the A2A transport gets **server-managed** multi-turn "for free" —
> it is closer to `agent_stateful` than to the stateless paths, despite needing no
> SDK. (It also implies the same `cloudaicompanion` dependency as any other
> server-side conversation.)

### Proof that history actually carried

Captured in each folder as `output.followup.verbatim.txt` (raw stream) and
`output.followup.parsed.txt` (typed summary). The decisive signal is the
**generated SQL on turn 2**, which filters on a species string that appears
nowhere in the question — it could only come from turn 1's result:

```sql
SELECT care_taker, COUNT(*) AS tree_count
FROM `bigquery-public-data.san_francisco.street_trees`
WHERE species = 'Platanus x hispanica :: Sycamore: London Plane'
```

Turn 3 then narrows to the average DBH of *those* trees, resolving references
from **both** prior turns.

---

## 📂 Code Walkthrough & Nuances

### 🐍 Stateless Chat (`inline_chat/main.py`)

This script sets up a `BigQueryTableReference` and passes it inline.

#### Key Code Section:
```python
# Pass schema INLINE
inline_context = geminidataanalytics.Context()
inline_context.system_instruction = "You are an expert urban forester in San Francisco. Analyze tree data accurately."
inline_context.datasource_references = datasource_references
# Same verified example query as the other scripts (so all four share one context)
inline_context.example_queries = [
    geminidataanalytics.ExampleQuery(
        natural_language_question="Which species of tree is most prevalent?",
        sql_query="SELECT species, COUNT(*) AS tree_count FROM ... GROUP BY species ORDER BY tree_count DESC LIMIT 10",
    )
]

request = geminidataanalytics.ChatRequest(
    inline_context=inline_context, # Inline context passed here
    parent=f"projects/{billing_project}/locations/{location}",
    messages=messages,
)
```

#### 💡 Understanding Stream Event Types

The `chat()` API returns a stream of events representing different stages of the agent's reasoning and actions. Since the native scripts print the **raw `response` object** verbatim, you will see all these event types in your terminal output (and in the captured `output.verbatim.txt`):

1.  **Status Messages**: Insights into the agent's thinking (e.g., "Retrieving context", "Analyzing Most Common Species").
2.  **Raw SQL Queries**: The exact BigQuery queries generated by the agent.
3.  **Visualization Specifications**: Complete Vega-Lite JSON specs for charts.
4.  **Natural Language Answers**: The final markdown response summarizing findings.

By printing the raw response object, you get full visibility into the agent's work cycle (actions vs. thoughts vs. answers).

---

### 🕵️‍♂️ Persistent Agent — Stateless Chat (`agent_stateless/main.py`)

This script creates an agent once and then uses `DataAgentContext` to reference it.

#### Setup (Create or Reuse):
```python
try:
    data_agent_client.create_data_agent(
        parent=f"projects/{billing_project}/locations/{location}",
        data_agent=agent,
        data_agent_id=agent_id
    )
except Exception as e:
    if "already exists" in str(e).lower():
        print("Reusing existing agent!")
```

#### Chatting using Reference:
```python
agent_context = geminidataanalytics.DataAgentContext()
agent_context.data_agent = agent_name # projects/.../locations/.../dataAgents/my-agent

chat_request = geminidataanalytics.ChatRequest(
    parent=...,
    data_agent_context=agent_context, # Uses saved schema!
    messages=[...]
)
```

#### 🚨 Critical Nuance: `DataAgentContext` vs `ConversationReference`

There are **two ways to chat with the same persistent agent**, split across two
scripts here:

1.  **`DataAgentContext`** (`agent_stateless/main.py`): Stateful Agent + **Stateless Chat**. The agent remembers its saved schema/config, but the server keeps **no** chat history.
2.  **`ConversationReference`** (`agent_stateful/main.py`): Stateful Agent + **Stateful Chat**. The server persists chat history in a named `Conversation`.

> [!IMPORTANT]
> **Can you converse with a published agent without enabling the companion API? Yes** —
> use `DataAgentContext` (`agent_stateless/main.py`). It works with just
> `geminidataanalytics.googleapis.com`. Because the chat is stateless, multi-turn
> is *your* job: keep the running history client-side and pass it back in the
> `messages` list on each call. You only need `ConversationReference` (and the
> companion API) if you want the **server** to manage history for you.

> [!CAUTION]
> While `DataAgentContext` works with just the `geminidataanalytics.googleapis.com` API, the automated conversation history (`ConversationReference`) requires the `cloudaicompanion.googleapis.com` (Gemini for Google Cloud) API to be enabled in your project! 

> [!TIP]
> Enabling `cloudaicompanion.googleapis.com` also unlocks the **Gemini for Google Cloud Console UI**, allowing you to chat with your BigQuery Data Agents directly through the Google Cloud Web Console! If you want a GUI for internal users, this is the API to enable.

#### 🧩 Parsing the Stream (`--parse`)

The verbatim proto dump is great for auditing but noisy to consume. Adding
`--parse` decodes **every** streamed message into one typed line, so you can see
the shape of the whole turn at a glance. All three native scripts
(`inline_chat/main.py`, `agent_stateless/main.py`, `agent_stateful/main.py`)
share the same decoder — it
handles all `SystemMessage` kinds — `text` (THOUGHT / FINAL_RESPONSE /
FOLLOWUP_QUESTIONS), `data` (query / matched_query / big_query_job / result),
`chart`, `schema`, `analysis`, `error` — plus the separate `citation` field.
(The A2A script also takes `--parse`, but decodes its JSON-envelope format
instead — see the A2A section below.) A sample run is captured in
[`agent_stateless/output.parsed.txt`](agent_stateless/output.parsed.txt):

```
[grp 0] TEXT/THOUGHT (2 part(s)): Running a query | Executing: SELECT species, COUNT(*) ...
[grp 0] TEXT/FINAL_RESPONSE (1 part(s)): To find the most prevalent tree species ...
    ↳ citation: id='ex_0' source_type='example_query' title='Verified Query: Which species of tree is most prevalent?'
    ↳ anchor: bytes[68:96] -> ['ex_0']
[grp 0] DATA/query: datasources=['bigquery-public-data.san_francisco.street_trees']
[grp 0] DATA/matched_query: NLQ='Which species of tree is most prevalent?' sql='SELECT species, COUNT(*) ...'
[grp 0] DATA/big_query_job: job_id=job_8MpNRTeiAqubwd6grTTxDSROHZr5 location=US
[grp 0] DATA/result: name='most_prevalent_tree_species' rows=10 cols=['species:STRING', 'tree_count:INTEGER']
[grp 0] CHART/result: mark='bar' title='Top 10 Most Prevalent Tree Species in San Francisco' image_bytes=0
[grp 0] TEXT/FOLLOWUP_QUESTIONS (3 part(s)):
    - Which species of tree has the largest average DBH (diameter at breast height)?
    ...
```

##### How the decoding works

Each `SystemMessage` is a protobuf with a `kind` **oneof** — you branch on which
field is set. `citation` is a *separate* top-level field (not part of `kind`), so
it can ride along with a `FINAL_RESPONSE`:

```python
sm = response.system_message
kind = sm._pb.WhichOneof("kind")          # 'text' | 'data' | 'chart' | 'schema' | 'analysis' | 'error'
if kind == "data":
    sub = sm.data._pb.WhichOneof("kind")  # 'query' | 'generated_sql' | 'result' | 'big_query_job' | 'matched_query'
```

The **citation `source_type`** is itself a oneof (`uri` / `example_query` /
`glossary_term`), so you read it the same way — this is what tells you an answer
was backed by a *verified query*, and the `anchors` pin it to a byte range of the
response text:

```python
if "citation" in sm:                              # proto-plus presence check
    for src in sm.citation.sources:
        source_type = src._pb.WhichOneof("source_type")   # e.g. 'example_query'
    for a in sm.citation.anchors:
        span = (a.text_message_anchor.start_offset_bytes,
                a.text_message_anchor.end_offset_bytes)
```

> [!NOTE]
> On the native `chat()` path this typed `citation` (with `source_type` + byte
> anchors) is actually *richer* than the A2A `matched_query_*` signal: it tells
> you not just **that** a verified query was used, but **which span** of the
> answer it backs. The A2A path is still the one that surfaces the match as
> dedicated top-level message parts — see below.

---

### 🧵 Stateful Conversation (`agent_stateful/main.py`)

Same persistent agent as above, but you chat *through* a server-side
`Conversation`, so the server retains history across turns. (Requires
`cloudaicompanion.googleapis.com` — see the caution in the previous section.)

#### 1. Ensure a `Conversation` exists (this is what holds the history):
```python
convo = geminidataanalytics.Conversation()
convo.agents = [agent_name]
data_chat_client.create_conversation(
    request=geminidataanalytics.CreateConversationRequest(
        parent=f"projects/{billing_project}/locations/{location}",
        conversation_id=convo_id,
        conversation=convo,
    )
)
```

#### 2. Chat via `ConversationReference` (instead of `data_agent_context`):
```python
convo_ref = geminidataanalytics.ConversationReference()
convo_ref.conversation = convo_name
convo_ref.data_agent_context = agent_context   # same agent context as the stateless script

chat_request = geminidataanalytics.ChatRequest(
    parent=...,
    conversation_reference=convo_ref,           # server persists history across turns
    messages=[...],
)
```

The streamed message shapes are identical to the stateless agent path (same
`--parse` decoder applies); the only difference is that follow-up turns sent to
the same `conversation` remember what came before.

---

### 🔗 A2A Streaming (`agent_a2a/main.py`)

This script attaches a **verified query** to the agent, then talks to it over the **A2A `message:stream`** endpoint (raw HTTP, ADC bearer token) so it can read the match signal that the native `chat()` SDK never exposes.

#### 1. Give the agent something to match (an authored `example_query`):
```python
context.example_queries = [
    geminidataanalytics.ExampleQuery(
        natural_language_question="Which species of tree is most prevalent?",
        sql_query="SELECT species, COUNT(*) AS tree_count FROM ... GROUP BY species ORDER BY tree_count DESC",
    )
]
```
Without at least one `example_query`, there is nothing to match and the signal never fires.

#### 2. Discover the stream URL from the agent's public AgentCard, then POST the question:
```python
card_url = (f"https://geminidataanalytics.googleapis.com/v1beta/a2a/"
            f"projects/{project}/locations/{location}/dataAgents/{agent_id}/v1/card")
stream_url = requests.get(card_url, headers=headers).json()["url"].rstrip("/") + "/v1/message:stream"

payload = {"request": {"messageId": str(uuid.uuid4()), "role": "ROLE_USER",
                       "content": {"text": question}}}
resp = requests.post(stream_url, json=payload, headers=headers)
```

#### 3. The signal — `metadata.gda_message.subType`:

The script prints the raw A2A stream verbatim (see [`agent_a2a/output.verbatim.txt`](agent_a2a/output.verbatim.txt)). Within that stream, the verified-query match arrives as two parts you can key off:

```json
{ "text": "Which species of tree is most prevalent?",
  "metadata": { "gda_message": { "subType": "matched_query_question" } } },
{ "text": "```sql\nSELECT species, COUNT(*) ...\n```",
  "metadata": { "gda_message": { "subType": "matched_query_sql" } } }
```

The presence of a `matched_query_question` part is the programmatic equivalent of the UI's "verified" checkmark.

#### 4. Parsing the A2A stream (`--parse`):

The A2A script also takes `--parse`, but its decoder is different from the native
one: it walks the JSON envelopes (`task` / `statusUpdate` / `artifactUpdate`) and
prints one line per part, keyed by `metadata.gda_message.subType`. A sample run is
captured in [`agent_a2a/output.parsed.txt`](agent_a2a/output.parsed.txt):

```
TASK: state=TASK_STATE_WORKING id=eb4671f4-...
STATUS thought: Analyzing context
STATUS thought: Executing: SELECT species, COUNT(*) ...
ARTIFACT[Final response] final_response: To find the most prevalent tree species ...
STATUS query_datasource: bigquery-public-data.san_francisco.street_trees
STATUS matched_query_question: Which species of tree is most prevalent?
STATUS matched_query_sql: ```sql SELECT species, COUNT(*) ...
ARTIFACT[BigQuery job] big_query_job: job_id=job_QeU5S7wHMIVt4V9q00e0PFBuiHWx location=US
ARTIFACT[Data result] result_data: | species | tree_count | ...
STATUS followup_questions: Which caretakers manage the most Sycamore: London Plane trees?
STATUS: state=TASK_STATE_COMPLETED (final)
```

#### 💡 A2A vs. native `chat()` for the match signal

**Both** paths surface the verified-query match as structured data — they just
shape it differently (compare the captured `output.parsed.txt` files):

*   **A2A** emits dedicated top-level parts tagged `matched_query_question` /
    `matched_query_sql`, and renders the result as a markdown table.
*   **Native `chat()`** emits a `data.matched_query.example_query` message **and**
    attaches a `citation` (with `source_type="example_query"` + byte-range
    `anchors`) to the `FINAL_RESPONSE`.

So the older "A2A is the *only* structured path" framing no longer holds. Pick
based on whether you're already on the SDK.

> [!TIP]
> To detect a verified-query hit on the native path, key off the
> **`data.matched_query`** message — it is emitted only when the agent actually
> reused an authored `example_query` (its A2A equivalent is the
> `matched_query_question` / `matched_query_sql` parts).

> [!NOTE]
> `create_data_agent` reuse does **not** update an existing agent's published context. If you change the verified SQL, either call `update_data_agent` or bump `agent_id`. `agent_stateless/main.py` handles this by calling `update_data_agent` when the agent already exists; `agent_a2a/main.py` does not, so bump its `agent_id` if you edit its verified query.
