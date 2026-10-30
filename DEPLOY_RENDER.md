# Deploy HandoffIQ on Render (free demo deployment)

## Before deploying
1. Push this project folder to a GitHub repository. Do not commit `.env` or any API key.
2. Create a Gemini API key at https://aistudio.google.com/app/apikey.
3. Confirm `.gitignore` excludes `.env`, `data/`, and `logs/`.

## Deploy
1. Sign in at https://render.com and connect your GitHub account.
2. In Render Dashboard choose **New + -> Blueprint**.
3. Select the GitHub repository containing this project and the `render.yaml` file.
4. Render reads `render.yaml` and creates the `handoffiq` web service.
5. Open the service's **Environment** page and add `GEMINI_API_KEY` with your real key. Do not put the key in source code or `render.yaml`.
6. Save and deploy. Wait for the deploy status to become **Live**.
7. Open the generated `https://<service-name>.onrender.com` URL. Check `/health` returns `{"status":"ok"}` and `/docs` opens the API docs.

## Render settings if creating a Web Service manually
- Runtime: Python
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Health check path: `/health`
- Environment variables:
  - `GEMINI_API_KEY` = your Gemini API key (secret)
  - `GEMINI_MODEL` = `gemini-2.5-flash`
  - `DB_PATH` = `data/app.db`
  - `LOG_FILE` = `logs/app.log`\n  - `LOG_DASHBOARD_TOKEN` = optional long random secret to protect the System Logs tab

## System Logs dashboard\nThe app includes a System Logs tab backed by `/api/logs` and `/api/logs/stats`. For a public deployment, set `LOG_DASHBOARD_TOKEN` in Render Environment and paste the same value into the dashboard Access protection panel. Render Free has an ephemeral filesystem, so use Render Dashboard → service → Logs for durable provider-side runtime log viewing.\n\n## Important free-plan limitations
- Render free web services spin down after 15 minutes without traffic; the first request after sleeping can take about a minute.
- Their filesystem is ephemeral. The SQLite database and local log file can be lost on restart, spin-down, or redeploy. The app seeds demo records when the database is empty, so the demo can still be shown, but data you add may not persist.
- This free configuration is suitable for an assessment/demo, not production or reliable long-term data storage. For durable user data, migrate to a managed PostgreSQL database or use a paid persistent disk.
- Gemini free-tier access has usage limits and account eligibility requirements. Do not send confidential meeting data through a free-tier API key.

## Submission checklist
- [ ] Public app URL opens
- [ ] `/health` returns status `ok`
- [ ] Dashboard loads and seeded demo data is visible
- [ ] Create a meeting and generate a recap; confirm `ai_used: true`
- [ ] Test carry-forward and Slip Radar
- [ ] Check Render Logs for successful startup and AI call events
- [ ] Submit the public app URL and GitHub repository URL (if requested)
- [ ] Never submit or publish the Gemini API key
