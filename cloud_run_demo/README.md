# Cloud Run demo — CA API identity models

A minimal, deployable web app that wraps the same inline-context `chat()` as
[`../inline_chat/main.py`](../inline_chat/main.py) (SF street trees, one verified
example query). The only thing that changes between deployments is **which
credentials the CA API client is built with** — chosen by the `IDENTITY_MODE`
env var. Whatever identity the client uses, the CA API propagates it to BigQuery.

See the repo README's [☁️ Deployment & Identity Models](../README.md#-deployment--identity-models)
for the full identity table this demo exercises.

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask app; `GET /ask?q=...` runs one `chat()` turn |
| `Dockerfile` / `requirements.txt` | container (gunicorn on `$PORT`) |
| `config.env.example` | copy to `config.env` (gitignored) and fill in |
| `deploy.sh` | `gcloud run deploy` (explicit `--project`) |
| `setup-iam.sh` | grant CA + BQ roles / `run.invoker` / `tokenCreator` |
| `validate_identity.sh` + `validate_identity.py` | empirical proof (below) |

## Quick start

```bash
cp config.env.example config.env   # then edit real values

# Local smoke test (uses your own ADC = model L):
pip install -r requirements.txt
BILLING_PROJECT=<your-project> IDENTITY_MODE=adc python3 app.py &
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

## Identity models (`IDENTITY_MODE`)

The **model L / 1 / 2 / 3 / 4** labels used here and below mirror the identity-knob
table in the repo README's
[☁️ Deployment & Identity Models](../README.md#-deployment--identity-models):

| Model | Effective principal | Where |
|---|---|---|
| **L** | your user account (local ADC) | local dev only |
| **1** | Compute **default** runtime SA | Cloud Run |
| **2** | your **custom** runtime SA | Cloud Run |
| **3** | an **impersonated** target SA | Cloud Run |
| **4** | the **end user** (forwarded OAuth token) | Cloud Run |

`IDENTITY_MODE` selects which of these the app uses at runtime:

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
