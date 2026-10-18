# Deploying TraceLayer on free tiers

Every service below is on its free tier; none of them needs a card.

| piece | host | free tier |
|---|---|---|
| Frontend (Next.js) | **Vercel** Hobby | unlimited hobby projects |
| Backend (FastAPI + all models: XGBoost, temporal GNN, ensemble) | **Hugging Face Space** (Docker, CPU basic) | 2 vCPU, 16 GB RAM; sleeps after 48 h without traffic |
| LLM (case narratives, chat, copilot) | **Groq** API | `openai/gpt-oss-20b`: 30 requests/min, 8K tokens/min, 1,000 requests/day |
| Database, auth, storage | **Supabase** (already set up) | 500 MB DB; pauses after 7 days idle |

Why Hugging Face for the backend: the model stack (torch + xgboost + artifacts) needs about 1 GB of RAM.
Render's and Koyeb's free tiers stop at 512 MB, so they can only run the backend without the trained models
(see [Alternative: Render](#alternative-render-free-no-trained-models)).

Do the steps in order: the frontend needs the backend URL, and the backend needs the frontend URL for CORS.

---

## Before you start

- [ ] Your changes are merged into `main` on GitHub (`MallankG/TrojanHacks2026`). The Space builds from `main`.
- [ ] Supabase migrations in `supabase/migrations/` are applied to your project (see README "Run locally").
- [ ] You have these from Supabase → Project Settings → API keys:
  - Project URL (`https://<ref>.supabase.co`)
  - Publishable key (`sb_publishable_...`), for the frontend
  - Service role / secret key, **backend only, never in Vercel**

---

## Step 1: Get a Groq API key (2 min)

1. Go to https://console.groq.com and sign in (Google or GitHub; no card).
2. **API Keys** → **Create API Key** → name it `tracelayer` → copy the key (`gsk_...`). It is shown once.
3. Optional check from your terminal:
   ```bash
   curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer gsk_..." | head -c 300
   ```
   You should see a JSON list of models that includes `openai/gpt-oss-20b`.

If Groq is down or the daily limit is reached, the app still answers with a deterministic template draft,
labelled `fallback_used=true` in the receipt.

## Step 2: Create a GitHub token for the Space (3 min)

The repo is private, so the Space needs read access to clone it while it builds.

1. GitHub → avatar → **Settings** → **Developer settings** → **Personal access tokens** → **Fine-grained tokens**
   → **Generate new token**.
2. Name: `hf-space-tracelayer`. Expiration: whatever you like (e.g. 90 days).
3. **Repository access** → *Only select repositories* → `MallankG/TrojanHacks2026`.
4. **Permissions** → Repository permissions → **Contents: Read-only**. Nothing else.
5. **Generate token** and copy it (`github_pat_...`).

## Step 3: Create the Hugging Face Space (backend with all models, ~15 min including build)

1. Sign up / sign in at https://huggingface.co.
2. Top-right **+ New** → **Space**.
   - Space name: `tracelayer-api`
   - License: any (e.g. `other`)
   - SDK: **Docker** → template **Blank**
   - Hardware: **CPU basic · 2 vCPU · 16 GB · FREE**
   - Visibility: **Public**. The browser has to reach the API. Your code stays private: the Space only
     contains the Dockerfile, and the image clones the repo with your token.
   - **Create Space**.
3. Add the secrets *before* uploading files, so the first build has them.
   Space → **Settings** → **Variables and secrets** → **New secret** for each:

   | name | value |
   |---|---|
   | `GITHUB_TOKEN` | the token from step 2 |
   | `GROQ_API_KEY` | the key from step 1 |
   | `SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `SUPABASE_SERVICE_ROLE_KEY` | the service role key |
   | `SERVICE_API_KEY` | any long random string, e.g. output of `openssl rand -hex 32` |
   | `CORS_ORIGINS` | `["http://localhost:3000"]` for now (updated in step 5) |

4. Upload the two files from this repo's `deploy/huggingface/` folder to the Space root:
   Space → **Files** → **+ Contribute** → **Upload files** → drag in `deploy/huggingface/Dockerfile` and
   `deploy/huggingface/README.md` → **Commit changes to main**.
   (The README's header tells Hugging Face this is a Docker Space listening on port 7860.)
5. The build starts automatically. Watch **Logs** → *Build*. It installs CPU-only torch, xgboost and the rest
   (about 8–12 minutes). When the status badge turns **Running**, the API is up.
6. Your backend URL is `https://<your-hf-username>-tracelayer-api.hf.space`.
   Check it in a browser:
   - `https://<...>.hf.space/health` → `{"status":"ok"}`
   - `https://<...>.hf.space/docs` → the interactive API docs

**If the build fails:**
- `secret GITHUB_TOKEN not found` → the secret name is wrong or was added after the build started:
  fix it, then **Settings** → **Factory rebuild**.
- `Authentication failed` on `git clone` → the token has no access to the repo, or it expired.
- Out of disk/memory while installing → make sure the Dockerfile still has the
  `--extra-index-url https://download.pytorch.org/whl/cpu` line (CUDA wheels are ~3 GB).

## Step 4: Deploy the frontend on Vercel (5 min)

1. https://vercel.com → **Add New…** → **Project** → import `MallankG/TrojanHacks2026`
   (grant Vercel access to the repo if asked).
2. **Root Directory** → **Edit** → `frontend`. Framework preset: Next.js (auto-detected).
3. **Environment Variables** (all three are public browser values):

   | name | value |
   |---|---|
   | `NEXT_PUBLIC_API_BASE_URL` | `https://<your-hf-username>-tracelayer-api.hf.space` (no trailing slash) |
   | `NEXT_PUBLIC_SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY` | `sb_publishable_...` |

   Never add the service role key here.
4. **Deploy**. When it finishes, copy the production URL, e.g. `https://tracelayer.vercel.app`
   (Project → **Domains** shows it).

## Step 5: Connect the pieces (3 min)

1. **CORS on the backend.** Space → Settings → Variables and secrets → edit `CORS_ORIGINS`:
   ```
   ["https://tracelayer.vercel.app","http://localhost:3000"]
   ```
   Use your exact Vercel production URL. Then **Settings** → **Restart this Space** (a restart is enough for
   a secret change; no rebuild needed).
2. **Supabase auth redirects.** Supabase → **Authentication** → **URL Configuration**:
   - Site URL: `https://tracelayer.vercel.app`
   - Redirect URLs: add `https://tracelayer.vercel.app/**`
   - **Save**.

## Step 6: Smoke test (5 min)

1. Open the Vercel URL and sign in with a demo account (see `ACCOUNTS_AND_ROLES.md`).
2. Upload a CSV from `backend/sample_data/` (or use existing data) and run detection.
3. Check the models ran: Space → **Logs** → *Container*. A run that scored with the trained models logs no
   `xgb_temporal scoring failed` / `gnn_temporal scoring failed` lines, and the run summary's
   `supervised.skipped_reason` is `null`. The first run after the Space wakes is slower while the models load.
4. Open a case and draft a narrative. The receipt should say provider `groq`, model `openai/gpt-oss-20b`,
   `fallback_used=false`. If it says `template` with an error like `groq_no_api_key` or `groq_rate_limited`,
   check the `GROQ_API_KEY` secret or wait for the rate limit to reset.
5. Browser devtools → Network: requests to `*.hf.space` should succeed with no CORS errors. A CORS error means
   `CORS_ORIGINS` doesn't exactly match the URL in the address bar.

---

## Updating after new commits

- **Frontend:** Vercel redeploys automatically on every push to `main`.
- **Backend:** the Space doesn't watch GitHub. After pushing to `main`, go to Space → **Settings** →
  **Factory rebuild**. To deploy another branch, change `ARG BRANCH=main` in the Space's Dockerfile.

## Free-tier behaviour to expect

- The Space sleeps after 48 hours without traffic. The next request wakes it (about 1–2 minutes).
  Open the `/health` URL a few minutes before a demo.
- Supabase pauses a free project after 7 days without activity: Dashboard → **Restore project**.
- Groq: 30 requests/min, 8,000 tokens/min and 1,000 requests/day on `openai/gpt-oss-20b`. A case narrative
  prompt is several thousand tokens, so expect about one narrative draft per minute; chat answers are smaller.
  Over a limit, the receipt shows `groq_rate_limited` or `groq_request_too_large` and the template answers.
- Groq is only called when `APPROVED_DATA_DESTINATIONS` contains `"groq"` (set in `render.yaml` and the
  Space Dockerfile); otherwise answers come from the template with `groq_not_approved_destination`.
- The model must not state counts in prose (a verifier guardrail); when it does twice, the narrative
  falls back to the template, which the verifier still checks.
  Above that, answers fall back to the labelled template until the limit resets.
- Policy search uses keyword retrieval when deployed (vector embeddings need a local Ollama).
- Data leaves your infrastructure only to Supabase and Groq. Both are listed in `APPROVED_DATA_DESTINATIONS`.
  Use only synthetic or demo data.

---

## Alternative: Render free (no trained models)

Simpler setup, but Render's free 512 MB can't hold torch + xgboost, so `render.yaml` installs
`backend/requirements-slim.txt`: rules, anomaly detection, the graph engine, agents and Groq work, while the
trained IBM models report "unavailable". It sleeps after 15 minutes idle (about 1 minute to wake).

1. https://render.com → **New** → **Blueprint** → connect GitHub → pick `MallankG/TrojanHacks2026`.
2. Render reads `render.yaml`. Fill the prompted values: `GROQ_API_KEY`, `SUPABASE_URL`,
   `SUPABASE_SERVICE_ROLE_KEY`, `CORS_ORIGINS` (`["http://localhost:3000"]` for now). `SERVICE_API_KEY` is
   generated for you.
3. **Apply**. When the deploy is live, check `https://tracelayer-api.onrender.com/health`.
4. Continue with steps 4–6 above, using the Render URL as `NEXT_PUBLIC_API_BASE_URL`. In step 5, update
   `CORS_ORIGINS` in Render → service → **Environment** (it redeploys on save).
