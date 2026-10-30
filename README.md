# HandoffIQ — AI Knowledge & Task Handoff Platform
Captures meetings/work transfers, turns raw notes into structured recaps with AI, tracks every action item until it is closed
with a recorded outcome, carries open items forward with history, and writes catch-up briefs for people who missed things.

## Run (Windows PowerShell)

Open PowerShell in the `handoffiq` project folder (the folder containing `requirements.txt`).

```powershell
python -m venv venv
.\\venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
# Configure Gemini: copy .env.example to .env and paste your key into GEMINI_API_KEY.
Copy-Item .env.example .env
notepad .env
python -m uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000` for the app and `http://localhost:8000/docs` for the API.

### Where to see logs

Logging is enabled automatically when the app starts. It is **not a dashboard page**:
- **Live console:** JSON log lines appear in the same PowerShell window running Uvicorn.
- **Log file:** `logs/app.log` inside the project folder. The path is printed in the `application_started` event.
- **Live file tail (open a second PowerShell window in the project folder):**

```powershell
Get-Content .\\logs\\app.log -Wait
```

- **Read the current log file:**

```powershell
Get-Content .\\logs\\app.log
```

To test it, create a meeting or refresh the dashboard, then inspect the file. Each API request records method, path, HTTP status, latency, and request ID. Domain events and AI outcomes are also recorded.

Optional settings:
```powershell
$env:LOG_LEVEL = "DEBUG"                  # default INFO
$env:LOG_FILE = "logs\\handoffiq.jsonl"  # relative paths resolve from project root
```

Linux/macOS equivalent:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # edit .env and set GEMINI_API_KEY
python -m uvicorn app.main:app --reload --port 8000
tail -f logs/app.log
```

Deploy with Docker:
```bash
docker build -t handoffiq .
docker run -p 8000:8000 -e GEMINI_API_KEY="$GEMINI_API_KEY" -e GEMINI_MODEL="gemini-2.5-flash" -v "$PWD/data:/srv/data" -v "$PWD/logs:/srv/logs" -e LOG_FILE=/srv/logs/app.log handoffiq
```
Mount both `data/` and `logs/` so the SQLite database and log file persist.
## Deployment\nSee [`DEPLOY_RENDER.md`](DEPLOY_RENDER.md) for the Render Blueprint and step-by-step deployment instructions. The included `render.yaml` configures a free demo deployment. Its SQLite database is ephemeral on Render Free, so demo data may reset after restarts or redeploys.\n\n## Structure
```
app/main.py   API, validation (pydantic), request-ID middleware, lifecycle + risk logic
app/ai.py     Google Gemini API call (retry, latency logging), recap + brief prompts, rule-based fallbacks
app/db.py     SQLite schema, helpers, seed data (3 KB docs, 1 past meeting with items)
app/log.py    JSON logging -> project-root logs/app.log (rotating) + console, request_id via contextvar
static/index.html   single-page UI (no build step)
```
Tables: meetings, decisions, items (owner, due, status, outcome, slips, origin_id, kb links), item_events (full audit trail), kb.

## AI configuration

Provider: **Google Gemini Developer API** using the REST `generateContent` endpoint.
Default model: `gemini-2.5-flash` (configure with `GEMINI_MODEL`).
API key: `GEMINI_API_KEY`, loaded from the local `.env` file or environment variable. Never logged.

Get a Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey).
1. Copy `.env.example` to `.env` in the project root (same folder as `requirements.txt`).
2. Open `.env` and replace `put-your-gemini-api-key-here` with your key.
3. Save the file and restart the backend. `.env` is ignored by Git and must never be uploaded or shared.

The selected `gemini-2.5-flash` model is listed with free-tier input/output pricing in Google's Gemini API pricing documentation. Free-tier access is subject to Google's model availability, rate limits, quotas and account eligibility; Google may change these limits. Free-tier data may be used by Google to improve its products. Do not send confidential or sensitive meeting content using a free-tier key.

- **Recap**: meeting notes + participants + KB catalogue + today's date -> JSON (summary, decisions, items with owner/due/kb_ids).
- **KB linking**: done in the same call (model picks relevant document IDs per item, shown as 📎).
- **Brief**: AI writes a <180-word brief from structured records (missed meetings, decisions, the person's open items by risk, KB to read).
- **Failure handling**: 2 attempts, 45s timeout; on failure a rule-based parser / template is used and the UI reports `ai_used=false`. Successful Gemini output reports `ai_used=true`.

## Data source
Seeded demo data plus anything pasted in the UI (chats, notes, transcripts, emails as raw text). KB is added via the UI.

## Logging
JSON lines with `ts, level, logger, request_id, msg` + context. Logs are written to the console and `logs/app.log` (2 MB rotation, 3 backups). The path is anchored to the project root by default and is emitted at startup. `x-request-id` header is accepted/generated and returned. Each request logs method, path, status and latency. Logging uses a dedicated `handoffiq` logger so Uvicorn's pre-existing handlers cannot silently prevent file logging.
Key events: `meeting_created`, `item_created`, `item_reassigned`, `item_closed`, `item_outcome_recorded`, `item_carried_forward`, `kb_created`.
Gemini calls log `provider, op, model, attempt, outcome, latency_ms`. Unhandled errors log stack trace with request ID. Headers/keys never logged.

## Logging dashboard

The frontend has a **System Logs** tab with:
- Request, error, warning and average-latency summary cards.
- Gemini call, success, failure and total-event counters.
- Search by message, endpoint, request ID or other logged context.
- Severity filtering and 15-second auto-refresh.
- A structured log table showing timestamp, severity, event, request ID and event details.

Backend endpoints:
- `GET /api/logs?level=ALL&search=&limit=200` — recent structured log records (maximum 500).
- `GET /api/logs/stats` — aggregate request and Gemini metrics.
- `GET /health` — deployment health check.

The dashboard reads the application's rotating JSON log file. On ephemeral hosting such as Render Free, log files may disappear on restart/redeploy; use the hosting provider's own log viewer for durable runtime logs.

Optional protection: set `LOG_DASHBOARD_TOKEN` to a long random secret in the server environment. The UI's Access protection panel sends it as `X-Logs-Token`. If unset, the log API is accessible to anyone who can access the public app, so set a token before exposing real operational logs publicly. Do not log secrets or personal/sensitive content.

## Special feature: Slip Radar
Most tools list action items; the real failure is items that *silently die*. Every open item gets a 0-100 risk score
(overdue, no owner, no due date, due soon, **slip count** = times it was carried forward). The radar view ranks everything,
the catch-up brief puts risky items first for the new owner, and the one-click **work transfer** reassigns a leaver's open items with audit events.
Why it adds value: it turns passive tracking into prioritised follow-up, and the slip count preserves the history that carry-forward normally hides.

## Assumptions
- "Absent" field on a meeting = people who missed it / are taking over; they are who the brief is for. Names are matched case-insensitively as plain text (no auth/users).
- Closing an item (done/dropped) requires an outcome.

## Known limitations / expected improvements
- No auth, roles or multi-tenancy; SQLite single-node. Move to Postgres + SSO.
- KB linking passes doc snippets in the prompt (fine for small KBs); use embeddings/pgvector for scale.
- Follow-up is pull-based (radar); add email/Slack reminders and a scheduler for due-date nudges.
- No file/audio ingestion yet (add Whisper transcription + PDF/email importers).
- Risk weights are heuristic; calibrate from real closure data.
- Automated tests are minimal (smoke script); add pytest with a mocked AI client.
