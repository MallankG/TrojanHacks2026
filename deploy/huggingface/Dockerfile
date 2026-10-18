# Hugging Face Space (Docker, free CPU basic: 16 GB RAM) running the backend with every model.
# Space secrets: GITHUB_TOKEN (read-only token for the private repo, used only at build time), GROQ_API_KEY,
# SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SERVICE_API_KEY, CORS_ORIGINS. New commits: Settings -> Factory rebuild.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ARG REPO=MallankG/TrojanHacks2026
ARG BRANCH=main
WORKDIR /srv
RUN --mount=type=secret,id=GITHUB_TOKEN,mode=0444,required=true \
    git clone --depth 1 --branch "$BRANCH" \
      "https://x-access-token:$(cat /run/secrets/GITHUB_TOKEN)@github.com/$REPO.git" app \
    && git -C app remote set-url origin "https://github.com/$REPO.git"

WORKDIR /srv/app/backend
# CPU-only torch wheels (the default Linux wheel pulls ~3 GB of CUDA libraries)
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

ENV PORT=7860 ENVIRONMENT=production LLM_PROVIDER=groq GROQ_MODEL=openai/gpt-oss-20b \
    APPROVED_DATA_DESTINATIONS='["local","supabase","groq"]'
EXPOSE 7860
CMD uvicorn main:app --host 0.0.0.0 --port $PORT
