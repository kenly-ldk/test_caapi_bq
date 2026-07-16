# Cloud Run demo — CA API identity models

A minimal, deployable web app that chats with a **published Data Agent** by
reference (`DataAgentContext`), mirroring
[`agent_stateless/main.py`](../../connection_approaches/agent_stateless/main.py)
(SF street trees, one verified example query). The agent is created once by
[`ensure_agent.py`](ensure_agent.py); the app only references it, so the
per-request identity needs just `dataAgentUser` (get + chat). The only thing that
changes between deployments is **which credentials the CA API client is built
with** — chosen by the `IDENTITY_MODE` env var. Whatever identity the client uses,
the CA API propagates it to BigQuery.

See the repo README's [☁️ Deployment & Identity Models](../README.md#-deployment--identity-models)
for the full identity table this demo exercises.

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask app; `GET /ask?q=...` chats the published agent via `DataAgentContext` |
| `ensure_agent.py` | create/update the published Data Agent once (run as the runtime SA/deployer) |
| `Dockerfile` / `requirements.txt` | container (gunicorn on `$PORT`) |
| `config.env.example` | copy to `config.env` (gitignored) and fill in |
| `deploy.sh` | `gcloud run deploy` (explicit `--project`) |
| `setup-iam.sh` | grant CA + BQ roles / `run.invoker` / `tokenCreator` |
| `validate_identity.sh` + `validate_identity.py` | empirical proof (below) |

## Quick start

```bash
cp config.env.example config.env   # then edit real values

# Create the published Data Agent once (needs dataAgentCreator):
pip install -r requirements.txt
python3 ensure_agent.py --project=<your-project> --agent-id=sf-trees-demo-agent

# Local smoke test (uses your own ADC = model L):
BILLING_PROJECT=<your-project> AGENT_ID=sf-trees-demo-agent IDENTITY_MODE=adc python3 app.py &
curl "localhost:8080/ask?q=Which+species+of+tree+is+most+prevalent%3F"

# Deploy to Cloud Run (runtime SA = model 1/2):
./setup-iam.sh
./deploy.sh
```

`deploy.sh` prints the invoke command; the caller needs `roles/run.invoker` on
the service (the deploy is `--no-allow-unauthenticated`).

### Reproducing the live deployment / tests

The exact multi-service deployment (models 2/3/4) and its manual tests are
captured as runnable scripts:

- **`deploy_all_models.sh`** — every command used to deploy + wire IAM for the
  three services.
- **`test_all_models.sh`** — the `curl` tests for each model. Invocation is
  `curl`; the tokens are minted with `gcloud`. On this workstation run
  `USE_GC=1 ./test_all_models.sh`.

> [!IMPORTANT]
> All three services are **authenticated**. Cloud Run's invocation check needs an
> OIDC **ID token** (audience = service URL) — a plain access token gives `401`,
> no auth gives `403`. For the **end-user** service you send **two headers**:
> `X-Serverless-Authorization: Bearer <ID token>` (Cloud Run invocation) and
> `Authorization: Bearer <end-user token>` (relayed by the app to CA API/BigQuery).

### Which header carries what — and why it differs by model

Cloud Run's invocation check reads its ID token from the **`Authorization`** header
**by default**. So the only question per model is: *does the app itself also need
`Authorization`?*

| | App needs `Authorization`? | Invocation ID token goes in | Why |
|---|---|---|---|
| **Model 2 / 3** | **No** — data identity is the runtime SA / an in-code impersonated SA | `Authorization` (default) | nothing else wants the header, so Cloud Run uses it |
| **Model 4** | **Yes** — it relays the end user's access token to CA API/BigQuery | `X-Serverless-Authorization` | `Authorization` is taken by the end-user token |

It is a **header-collision** choice, not a per-model requirement. For models 2/3
Cloud Run validates the ID token in `Authorization` and passes the header through
to the container, but the app ignores it — no conflict. For model 4 the app needs
`Authorization` for the end-user token; if you put that access token there, Cloud
Run tries to validate it as an *invocation* credential and fails with `401` (an
access token is not an OIDC ID token for the service audience).

That is exactly what `X-Serverless-Authorization` is for. **Cloud Run's rule: if
`X-Serverless-Authorization` is present, it is used for the invocation check and
`Authorization` is passed untouched to your service.** So model 4 sends:

```
X-Serverless-Authorization: Bearer <ID token>          # -> Cloud Run run.invoker check
Authorization:              Bearer <end-user token>    # -> your app -> CA API -> BigQuery
```

(Models 2/3 *could* also use `X-Serverless-Authorization`; there is just no
reason to, since their app never reads `Authorization`.)

## Identity models (`IDENTITY_MODE`)

The **model L / 1 / 2 / 3 / 4** labels used here are defined in the canonical
identity-knob table in the parent
[☁️ Deployment & Identity Models](../README.md#-deployment--identity-models)
(L = local ADC; 1 = default runtime SA; 2 = custom runtime SA; 3 = impersonated
SA; 4 = end-user OAuth). `IDENTITY_MODE` selects which the app uses at runtime:

| Mode | Effective identity at CA API + BigQuery | Notes |
|---|---|---|
| `adc` (default) | the ambient ADC principal — your user locally, the **runtime SA** on Cloud Run | models L / 1 / 2 |
| `impersonate` | `TARGET_SA` | runtime SA needs `roles/iam.serviceAccountTokenCreator` on it (model 3) |
| `end_user` | the **end user** | the request must carry the user's own cloud-platform-scoped OAuth token in `Authorization`; the *whole* `chat()` runs as the user (model 4) |

> [!IMPORTANT]
> There is **no** mode where the CA API is called by the SA but BigQuery is read
> as the end user. For BigQuery the two are one identity — see the proof below.

## 🧪 Empirical proof — no split identity for BigQuery

`validate_identity.sh` proves that, for BigQuery, control-plane and data-plane
identity are **coupled to the caller**, and that the `ChatRequest.credentials`
field (documented for Looker) **cannot** inject a separate BigQuery data identity.

It provisions throwaway resources against a **private** table (a public table
can't demonstrate *denial*), runs four controls, and **reverts everything** via a
`trap … EXIT`:

| Control | caller of `chat()` | `credentials` field | Expected | Proves |
|---|---|---|---|---|
| A | P-read (has `dataViewer`) | — | ✅ succeed | positive control |
| B | P-none (no `dataViewer`) | — | ❌ denied | BigQuery enforces the **caller's** IAM |
| C | P-none | P-read's token | ❌ denied | `credentials` is **not** a BQ data identity → **no split** |
| D | P-read | P-none's token | ✅ succeed | BigQuery ignores `credentials` for BQ |

`A✓ B✗ C✗ D✓` is the decisive result.

> [!CAUTION]
> This **mutates your project** (creates two service accounts, a dataset/table,
> and IAM bindings) and needs the operator to hold
> `roles/iam.serviceAccountTokenCreator` on the two test SAs to mint their tokens.
> Get sign-off before running; it reverts on exit.

```bash
./validate_identity.sh   # reads config.env; prints A/B/C/D outcomes + verdict
```

A cheaper half-proof needs no private table: call `chat()` as a service account
with **zero** BigQuery roles → the job fails at `bigquery.jobs.create`, showing
the CA API uses the caller's identity, not a privileged service identity.
