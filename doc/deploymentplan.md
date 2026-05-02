# Deployment Plan — WanderAI Trip Planner

This document covers how to deploy the project for **$0** in hosting cost. The
project is currently a **Streamlit monolith**: `src/ui/streamlit_app.py`
imports the agents/orchestrator directly and runs them in-process. There is
no separate API server today.

Two paths are described:

- **Path A** — Deploy the current monolith as-is (recommended, ~30 minutes).
- **Path B** — Split into a FastAPI backend + separate frontend
  (only if a real API surface is needed; ~1–2 days of refactor).

---

## Path A — Deploy as-is (recommended)

One deployment serves both UI and agent logic.

### Host: Streamlit Community Cloud

- Free, unlimited public apps, GitHub-integrated.
- App sleeps on idle and wakes on request (acceptable cold-start for a demo).
- Built-in secret management.

### Steps

1. **Initialize git and push to GitHub**

   This repo is not a git repo yet.

   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<user>/<repo>.git
   git push -u origin main
   ```

2. **Add `.gitignore`** at repo root. Must exclude:

   ```gitignore
   __pycache__/
   *.pyc
   .env
   .venv/
   venv/
   traces/
   .streamlit/secrets.toml
   .pytest_cache/
   .DS_Store
   ```

3. **Pin the Python version**

   Add a `runtime.txt` (or `.python-version`) at repo root:

   ```
   python-3.11
   ```

4. **Confirm `requirements.txt` is complete**

   Current file already lists `google-genai`, `streamlit`, `pydantic`,
   `httpx`, `python-dotenv`, `rich`. No changes needed for deploy.

5. **Wire `st.secrets` as a fallback for env vars**

   Streamlit Cloud injects secrets via `st.secrets`, not `os.environ`.
   Add a small bootstrap at the top of `src/ui/streamlit_app.py`
   (after `load_dotenv()`):

   ```python
   import streamlit as st
   for key in ("GEMINI_API_KEY", "GEMINI_MODEL_FAST", "GEMINI_MODEL_SMART"):
       if not os.getenv(key) and key in st.secrets:
           os.environ[key] = st.secrets[key]
   ```

   This keeps `LLMClient` (which reads `os.getenv`) working in both local
   and cloud environments.

6. **Configure secrets in the Streamlit Cloud dashboard**

   App → Settings → Secrets:

   ```toml
   GEMINI_API_KEY = "your-key-here"
   ```

7. **Deploy**

   - Go to https://share.streamlit.io
   - **New app** → pick the GitHub repo and branch
   - **Main file path**: `src/ui/streamlit_app.py`
   - **Deploy**

   First boot takes ~2–3 minutes while dependencies install.

### Backup hosts (also free)

| Host                     | Notes                                                                |
| ------------------------ | -------------------------------------------------------------------- |
| Hugging Face Spaces      | Streamlit SDK, no idle sleep, public by default. Use a `README.md` header with `sdk: streamlit`. |
| Render free web service  | Sleeps after 15 min idle (~30 s cold start). Needs `Procfile` with `web: streamlit run src/ui/streamlit_app.py --server.port=$PORT --server.address=0.0.0.0`. |
| Railway                  | Trial credit only; not truly free long-term.                         |

### Gotchas

- **`traces/` is ephemeral on Streamlit Cloud.** The filesystem is wiped on
  restart. Trace JSON is fine for a live demo but won't persist. If trace
  history matters, write to S3 / a Postgres free tier (Supabase/Neon) instead.
- **`tools/data/` static lookup data** must be committed to git — verify it
  is not in `.gitignore`.
- **Outbound HTTP** (web search, Gemini API) is allowed on all the hosts
  listed; nothing special required.
- **Free Gemini quota** is per API key (15 RPM, 1M tokens/day on Flash).
  Heavy demos can blow this — see `LLMClient.QuotaExhaustedError` handling.

---

## Path B — Split: FastAPI backend + separate frontend

Worth it if you want:

- A real API surface for mobile / third-party clients.
- A React/Next.js UI in place of Streamlit.
- Independent scaling/observability for the agent layer.

Skip this path if Path A meets your needs — the split adds CORS, auth,
streaming, and cold-start complexity for no UX gain in a single-user demo.

### Architecture

```
[ Frontend (Streamlit or Next.js) ]
            │  HTTPS (httpx / fetch)
            ▼
[ FastAPI backend ]  ── runs orchestrator.graph, agents, tools
            │
            ▼
[ Gemini API ]   [ web_search ]   [ static tools/data ]
```

### Backend deployment (free options)

| Host                     | Free tier characteristics                                              |
| ------------------------ | ---------------------------------------------------------------------- |
| Hugging Face Spaces (Docker) | No idle sleep, 16 GB RAM, persistent until inactivity for ~weeks. |
| Render free web service  | 750 h/month, sleeps after 15 min idle, ~30 s cold start.               |
| Fly.io free allowance    | 3 shared-CPU machines, can be configured to scale-to-zero.             |

**Backend refactor checklist:**

1. Add `fastapi` and `uvicorn` to `requirements.txt`.
2. Create `src/api/main.py` exposing endpoints, e.g.:
   - `POST /plan` — accepts free-text brief, returns final `TripBrief` JSON.
   - `POST /plan/stream` — server-sent events streaming per-agent progress.
3. Move the orchestration loop currently in `streamlit_app.py` into a
   plain function `run_pipeline(brief_text: str) -> AsyncIterator[Event]`,
   then call it from both the CLI (`src/ui/cli.py`) and the FastAPI route.
4. CORS: allow the frontend origin via `fastapi.middleware.cors`.
5. Health check: `GET /healthz` → `{"ok": true}` for Render's probe.
6. Dockerfile (for Spaces / Fly):

   ```dockerfile
   FROM python:3.11-slim
   WORKDIR /app
   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   COPY . .
   ENV PORT=8000
   CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
   ```

### Frontend deployment (free options)

- **Keep Streamlit** on Streamlit Cloud — fastest path. Replace direct
  agent imports with `httpx.post("https://<backend-url>/plan", ...)`.
- **Next.js / React** on **Vercel free** — full UI rebuild required, but
  gets you a real production-grade frontend, SSR, and a nice domain.

### Operational concerns introduced by the split

- **Cold starts** on Render free hurt UX (~30 s on first request).
  Mitigate with a cron-style ping (UptimeRobot free tier).
- **Auth**: previously implicit (single Streamlit session). Now needs at
  minimum an API key header on the backend, and CORS allow-listing.
- **Streaming**: per-agent progress over HTTP requires SSE or websockets.
- **Cost ceiling**: still $0, but you now have two services to monitor.

---

## Recommendation

**Start with Path A.** It is a 30-minute deploy, costs nothing, and matches
how the project is currently architected. Move to Path B only when there's a
concrete reason — a non-Streamlit client, multi-tenant usage, or a need to
decouple the agent runtime from the UI lifecycle.

---

## Quick checklist (Path A)

- [ ] `git init` + push to GitHub
- [ ] `.gitignore` covers `traces/`, `.env`, `.streamlit/secrets.toml`
- [ ] `runtime.txt` pins Python 3.11
- [ ] `streamlit_app.py` reads `st.secrets` as fallback for `GEMINI_API_KEY`
- [ ] Secret set in Streamlit Cloud dashboard
- [ ] App deployed with main file `src/ui/streamlit_app.py`
- [ ] Smoke test: load app, submit a brief, verify a trip plan comes back
