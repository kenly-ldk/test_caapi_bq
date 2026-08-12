# Conversational Analytics API (GDA) — BigQuery integration guide

A hands-on tour of the BigQuery **Conversational Analytics API** (Gemini Data
Analytics, `geminidataanalytics`): four ways to connect to and invoke a CA agent,
the IAM / identity model behind them, and a runnable Cloud Run demo that proves
how identity propagates to BigQuery.

The repo is organized into **two areas**, each with its own README:

| Area | What's inside |
|---|---|
| [🔌 **connection_approaches/**](connection_approaches/README.md) | The four ways to invoke a CA agent — **Stateless Chat** (inline context), **Persistent Agent** (stateless chat via `DataAgentContext`), **Stateful Conversation** (`ConversationReference`), and **A2A streaming** (verified-query match signal). Overview + code walkthrough + `--parse` decoding. |
| [🔐 **permissions-identity/**](permissions-identity/README.md) | Who can use the agent, whose identity reaches BigQuery, and how that changes on Cloud Run (two-plane model, role tables, diagrams) — plus [`cloud_run_demo/`](permissions-identity/cloud_run_demo/README.md), a deployable app demonstrating the SA / impersonation / end-user-OAuth identity models and an empirical "no split identity" proof. |

---

## 🛠 Environment & setup

Shared by every script in the repo.

#### 1. Create & activate a virtual environment
```bash
python3 -m venv venv_caapi
source venv_caapi/bin/activate
```

#### 2. Install the SDK
```bash
pip install google-cloud-geminidataanalytics
# (the Cloud Run demo also needs: flask gunicorn google-auth requests)
```

#### 3. Authenticate (Application Default Credentials)
```bash
gcloud auth application-default login
```

## ▶️ Running the connection-approach scripts

All four use the **same question and context** (SF street trees + one verified
`example_query`), so their outputs are directly comparable. Each prints the raw,
verbatim stream by default; add `--parse` for a compact typed summary.

```bash
source venv_caapi/bin/activate

python3 connection_approaches/inline_chat/main.py        # Stateless Chat (inline context)
python3 connection_approaches/agent_stateless/main.py    # Persistent agent, stateless chat (DataAgentContext)
python3 connection_approaches/agent_stateful/main.py     # Stateful conversation (needs cloudaicompanion API)
python3 connection_approaches/agent_a2a/main.py          # A2A streaming (verified-query match signal)

python3 connection_approaches/agent_stateless/main.py --parse   # typed summary

# --followup runs a 3-turn conversation (works on all four approaches)
python3 connection_approaches/agent_stateless/main.py --parse --followup
```

See [connection_approaches/README.md](connection_approaches/README.md) for the
per-approach walkthrough, and
[permissions-identity/README.md](permissions-identity/README.md) for the IAM model
+ the Cloud Run demo.

## 📁 Repo layout

```
connection_approaches/     # the 4 ways to invoke a CA agent (see its README)
  README.md
  inline_chat/     ├─ main.py, output.verbatim.txt, output.parsed.txt
  agent_stateless/ ├─ main.py, output.verbatim.txt, output.parsed.txt
  agent_stateful/  ├─ main.py, output.verbatim.txt, output.parsed.txt
  agent_a2a/       └─ main.py, output.verbatim.txt, output.parsed.txt
permissions-identity/      # IAM / identity model (see its README)
  README.md
  cloud_run_demo/          # deployable demo of the Cloud Run identity models
    README.md
    app.py, ensure_agent.py, Dockerfile, requirements.txt, config.env.example,
    deploy.sh, setup-iam.sh, deploy_all_models.sh, test_all_models.sh,
    validate_identity.sh, validate_identity.py
```
