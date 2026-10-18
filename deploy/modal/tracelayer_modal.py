"""Modal: the FastAPI backend with every model (XGBoost, temporal GNN, ensemble).

    modal deploy deploy/modal/tracelayer_modal.py     # from the repo root; prints the https://...modal.run URL

Free Starter credits ($30/month) cover a demo: containers scale to zero after SCALEDOWN_S idle, so the
first request after a pause waits for the cold start (torch + models load, ~20-40 s).
Secrets come from the Modal secret "tracelayer-api" (SUPABASE_*, SERVICE_API_KEY, GROQ_API_KEY, CORS_ORIGINS).
"""
from pathlib import Path

import modal

# repo root when deploying; inside the container this module is /root/tracelayer_modal.py and local paths are unused
ROOT = Path(__file__).resolve().parents[2] if modal.is_local() else Path("/")
SCALEDOWN_S = 300  # ponytail: idle seconds before scale-to-zero; raise to cut cold starts, costs credits
IGNORE = [".venv", "**/.venv", ".env", "**/.env", "**/__pycache__", "**/.pytest_cache", "tests", "**/*.egg-info"]

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgomp1")  # xgboost's OpenMP runtime
    # dependencies first (cached layer); "-e ../agents" is installed from the copied package below
    .add_local_file(ROOT / "backend" / "requirements.txt", "/tmp/requirements.txt", copy=True)
    .run_commands(
        "grep -v '^-e ' /tmp/requirements.txt > /tmp/req.txt",
        # CPU-only torch wheels (the default Linux wheel pulls ~3 GB of CUDA libraries)
        "pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/req.txt",
    )
    .add_local_dir(ROOT / "agents", "/app/agents", copy=True, ignore=IGNORE)
    .run_commands("pip install --no-cache-dir /app/agents")
    .env({"ENVIRONMENT": "production", "LLM_PROVIDER": "groq", "GROQ_MODEL": "openai/gpt-oss-20b",
          "APPROVED_DATA_DESTINATIONS": '["local","supabase","groq"]'})
    .add_local_dir(ROOT / "backend", "/app/backend", ignore=IGNORE)  # code + model artifacts, mounted at start
)

app = modal.App("tracelayer-api", image=image)


@app.function(secrets=[modal.Secret.from_name("tracelayer-api")], cpu=1.0, memory=2048, timeout=600,
              scaledown_window=SCALEDOWN_S, max_containers=1)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def api():
    import os
    import sys

    os.chdir("/app/backend")  # relative paths (artifacts, sample data) resolve as in local runs
    sys.path.insert(0, "/app/backend")
    from main import app as fastapi_app

    return fastapi_app
