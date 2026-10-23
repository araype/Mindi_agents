# mindi_agent

AI companion agent for [Mindi](https://github.com/araype/Mindi), built with [Google ADK](https://adk.dev). It helps women in perimenopause and menopause understand their symptoms and how much they affect their daily life, and guides them on which specialist to see.

> **Mindi does not replace medical care.** It explains and orients; it does not diagnose, prescribe, or recommend medications or doses.

## How it works

```mermaid
flowchart TD
    U[User message] --> F{Red-flag filter<br/>rule-based, no LLM}
    F -- emergency or<br/>medical check needed --> A[Fixed, gentle alert message]
    F -- no flags --> M[Mindi coordinator]
    M --> P[Psychology]
    M --> H[Hormonal]

    M --> G[Gynecology]
```

1. **Red-flag filter.** A `before_model_callback` checks every message against fixed phrase lists *before* calling the model. If it matches, it returns a predefined message (self-harm support, urgent care, or medical evaluation) and the model is never called. It is attached to every agent, so it keeps working after a transfer. Colloquial uses ("estos bochornos van a matarme") are removed before matching, and physical or evaluation phrases are ignored when one of the three previous words is a negation ("no tengo dolor de pecho"). Negations never apply to self-harm phrases. The alert stays in the agents' instructions for the next 3 user messages and then expires.
2. **Coordinator (`mindi`).** Answers general questions (what perimenopause is, what the test measures, how to use the report) and routes symptom questions to a specialist. It runs on `openai/gpt-4.1-mini`: with `gpt-4o-mini` it often answered symptom questions itself instead of transferring.
3. **Specialist sub-agents.** Each one maps to a subscale of the Menopause Rating Scale (MRS) and runs on `openai/gpt-4o-mini`:

| Agent | Covers |
|---|---|
| `psicologia` | Mood, irritability, anxiety, mental fatigue, insomnia that comes with worry, sadness or anxiety |
| `hormonal` | Hot flashes, night sweats (including waking up hot or sweating), palpitations, joint and muscle pain, period changes, general questions about hormone therapy |
| `ginecologia` | Vaginal dryness, sexual health, urinary symptoms |

Sleep is routed by cause: waking up from hot flashes or night sweats goes to `hormonal`; insomnia with worry, sadness or anxiety goes to `psicologia`; if the cause is unclear, the coordinator asks first.

All agents share the same rules: Peruvian Spanish, warm and brief, no emojis, no diagnoses, no absolute promises.

4. **Knowledge sheets.** Each agent's instruction includes short sheets in `mindi_agent/conocimiento/` (`comun.md` plus one per specialist). They summarize the HNHU 2024 climacteric guideline (primary source) and the AETSA 2015 guideline (complement) in patient-friendly language, citing the section of each claim. They deliberately leave out drug names, doses and the "therapeutic window". The sheets are the only clinical knowledge the agents get, so a clinician should review any change to them. Curly braces are not allowed in the sheets because ADK treats `{name}` as a state variable.
5. **MRS reading.** If the session state contains `resultado_mrs`, a `before_agent_callback` turns it into `lectura_mrs` using the HNHU cut-offs (somatic ≥8, psychological ≥6, urogenital ≥3, total ≥8 / ≥15). Subscales are compared in proportion to their maximum to pick the area that affects her most. The model reads that sentence instead of interpreting raw numbers.

## Project structure

```
mindi_agent/
├── __init__.py        # from . import agent
├── agent.py           # filter, MRS reading, coordinator and sub-agents (root_agent)
├── conocimiento/      # knowledge sheets injected into each agent's instruction
├── requirements.txt
└── .env.example       # copy to .env for local testing
tests/
└── test_filtro.py     # filter, MRS reading and transfer guards, no model calls
evals/
├── banco_preguntas_mindi.md   # the team's question bank (source of truth)
├── generar_evalset.py         # bank -> mindi.evalset.json
├── mindi.evalset.json         # generated, do not edit by hand
├── metricas.py                # custom metrics for adk eval
├── test_config.json           # metrics and thresholds
└── resumen.py                 # readable summary of the last run
revision_clinica/
├── generar.py                 # builds the document for the medical team
├── generar_docx.py            # Markdown -> Word, with Track Changes on
├── revision_clinica.md        # generated
└── revision_clinica.docx      # generated, for comments and tracked changes
```

Run the tests from the repo root:

```bash
python -m unittest discover -s tests -v
```

The cases use the IDs of the team's question bank (A1, G3, J1...).

Sub-agents hand the conversation back when the topic changes: to another specialist, or to `mindi` for general questions. Each one only sees the *other* areas in its instructions. A `before_tool_callback` blocks any transfer to itself (which would otherwise crash the request in ADK) and allows only one transfer per user message, so the area that receives a message answers it instead of passing it on again.

After a self-harm alert, an `after_model_callback` appends a fixed reminder of Línea 113, opción 5, to any answer that does not mention it, for as long as the alert is active.

## Evals

The question bank in `evals/banco_preguntas_mindi.md` is turned into an `adk eval` set. Every case is checked with three metrics:

| Metric | What it checks | How |
|---|---|---|
| `agente_correcto` | The agent that answered is one the bank accepts | Code, no model |
| `mensaje_fijo_correcto` | The red-flag filter returned the expected fixed message, or none | Code, exact text |
| `respuesta_cumple_banco` | The answer meets the general criteria and the "Debe" / "No debe" columns | `gpt-4o` as judge, with rubrics |

Multi-turn cases (I) run as one conversation, and J cases start with `resultado_mrs` in the session state. B2 was adjusted after the bank was written (sleep rule: the coordinator may ask first); that change is in `AJUSTES` in `generar_evalset.py`.

Setup (once, in the same virtualenv as the agent):

```bash
pip install "google-adk[eval]==2.12.0"
```

Run from the repo root:

```bash
# 1. After editing the bank, regenerate the eval set
python evals/generar_evalset.py

# 2. Run every case against the local agent (uses OPENAI_API_KEY from mindi_agent/.env)
adk eval mindi_agent evals/mindi.evalset.json --config_file_path evals/test_config.json

# 3. Read the failures (add --todos to see every case with its answer)
python evals/resumen.py
```

To run only some cases, append their IDs: `evals/mindi.evalset.json:B2,J3`. A full run takes about 10 minutes and calls the agent's models and the judge, so it costs a little OpenAI credit. Answers vary between runs, so rerun a failing case before changing the agent; the judge also makes occasional mistakes, so read its reason in the summary. Results are saved in `mindi_agent/.adk/eval_history/` (ignored by git).

After a run, rebuild the document for the medical team with the real answers:

```bash
pip install "python-docx==1.1.2"   # once
python revision_clinica/generar.py        # Markdown (add --resultado FILE to use an older run)
python revision_clinica/generar_docx.py   # Word copy, opens with Track Changes on
```

If a run fails with `RateLimitError: You have no credits remaining`, the OpenAI account is out of credit: the cases show empty metrics and the run is not valid. The deployed service uses the same account, so it also returns HTTP 500 for anything that reaches the model (the fixed red-flag messages still work).

## Run locally

Requirements: Python 3.10+ and an OpenAI API key from the [OpenAI platform](https://platform.openai.com/api-keys). The agents use OpenAI models through LiteLLM (`MODELO` and `MODELO_COORDINADOR` in `agent.py`); to switch back to Gemini, change them and use `GOOGLE_API_KEY` instead.

```bash
pip install -r mindi_agent/requirements.txt
cp mindi_agent/.env.example mindi_agent/.env   # then paste your API key
adk web
```

Run `adk web` from the folder that **contains** `mindi_agent/`. Open the dev UI, pick `mindi_agent`, and check the events tab to see which agent handled each message.

Suggested smoke tests:

| Message | Expected |
|---|---|
| "¿Qué es la perimenopausia?" | Coordinator answers |
| "Estoy muy irritable con mi familia" | Transfers to `psicologia` |
| "Me dan bochornos en la noche" | Transfers to `hormonal` |
| "Me duele cuando tengo relaciones" | Transfers to `ginecologia` |
| "Tengo dolor de pecho" | Fixed emergency message, no model call |

## Deploy to Cloud Run

One-time setup:

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com

# Allow Cloud Build to build the image
gcloud projects add-iam-policy-binding YOUR_PROJECT \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/cloudbuild.builds.builder"

# Store the API key as a secret (check first: gcloud secrets list)
echo "YOUR_API_KEY" | gcloud secrets create OPENAI_API_KEY --data-file=-
gcloud secrets add-iam-policy-binding OPENAI_API_KEY \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

If the secret already exists, do not create it again. To change the key, add a new version in the Cloud console: **Security → Secret Manager → OPENAI_API_KEY → New version**, paste the key and save. The service reads `latest`, so the next deploy (or a new revision) picks it up; disable the old version afterwards.

Deploy (public, one instance at most, no dev UI):

```bash
adk deploy cloud_run \
  --project=YOUR_PROJECT \
  --region=us-central1 \
  --service_name=mindi-agent \
  --session_service_uri=memory:// \
  --artifact_service_uri=memory:// \
  ./mindi_agent \
  -- --set-secrets=OPENAI_API_KEY=OPENAI_API_KEY:latest --max-instances=1 --allow-unauthenticated
```

Sessions are kept in memory and are lost when the instance restarts; pass `--session_service_uri` with a Postgres URL to persist them. The URIs are explicit because, when they are unset, ADK may connect to Agent Platform services on its own. Add `--allow_origins="https://YOUR-APP.vercel.app"` only if a browser will call the service directly, and `--with_ui` to also serve the ADK dev UI.

## Calling the agent

```bash
# Create a session (optionally with the user's MRS results)
curl -X POST $APP_URL/apps/mindi_agent/users/USER_ID/sessions \
  -H "Content-Type: application/json" \
  -d '{"session_id": "SESSION_ID", "state": {"resultado_mrs": {"total": 18, "somatico": 7, "psicologico": 8, "urogenital": 3}}}'

# Send a message
curl -X POST $APP_URL/run_sse \
  -H "Content-Type: application/json" \
  -d '{
    "app_name": "mindi_agent",
    "user_id": "USER_ID",
    "session_id": "SESSION_ID",
    "new_message": {"role": "user", "parts": [{"text": "Duermo muy mal últimamente"}]},
    "streaming": false
  }'
```

We recommend calling the agent from a server-side route (for example, a Vercel API route) rather than directly from the browser.

## Roadmap

- [ ] RAG over clinical guidelines (Supabase pgvector) as a tool for each specialist
- [ ] Persistent sessions in Supabase Postgres
- [x] Evaluation set built from the team's question bank (`adk eval`)
- [ ] Add real user questions to the evaluation set
- [ ] WhatsApp channel
- [ ] Review red-flag lists with gynecologists (see `revision_clinica/`)

## Safety and privacy notes

- The red-flag lists are a first layer, not a complete safety system. Phrase matching misses many ways of describing a symptom, so logs should be reviewed regularly.
- Conversations about health are sensitive personal data under Peru's Personal Data Protection Law (Ley 29733). Get explicit consent before storing them.
- Check the data-use terms of your OpenAI plan before handling real users' conversations.
- Never commit `.env` or API keys.

## Team

Aracely Ayala UTEC
