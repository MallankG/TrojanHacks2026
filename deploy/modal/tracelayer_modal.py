"""Modal: the FastAPI backend with every model (XGBoost, temporal GNN, ensemble).

    modal deploy deploy/modal/tracelayer_modal.py     # from the repo root; prints the https://...modal.run URL

Deploy from a clean, up-to-date `main` checkout: `backend/` and `agents/` are uploaded from the local working
tree, not cloned from git.

Cost: request-driven, like a sleeping free web service. No container runs until a request arrives
(min_containers=0, no buffer); the first request wakes one (~15 s cold start, models included) and it shuts down
SCALEDOWN_S after the last request. Usage counts against Modal's free Starter allowance ($30/month) only while
a container is up (1 vCPU + 2 GB, about $0.06/hour); with no payment method on the workspace nothing can be
billed beyond it.

Secrets come from the Modal secret "tracelayer-api": SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GROQ_API_KEY,
optional SUPABASE_JWT_SECRET / SERVICE_API_KEY / feature flags, and CORS_ORIGINS as a JSON list, e.g.
'["https://tracelayer-five.vercel.app"]' (a bare URL fails settings validation and the app won't start).
"""
from pathlib import Path

import modal

# repo root when deploying; inside the container this module is /root/tracelayer_modal.py and local paths are unused
ROOT = Path(__file__).resolve().parents[2] if modal.is_local() else Path("/")
# ponytail: idle seconds before shutdown. Lower saves a little more allowance but makes viewers sit through
# more ~15 s cold starts between clicks; raise it for a live demo session.
SCALEDOWN_S = 60
IBM = "ml/artifacts/ibm"
IGNORE = [
    ".venv", "**/.venv", ".env", "**/.env", ".env.*", "**/.env.*",  # secrets come only from the Modal secret
    "**/__pycache__", "**/.pytest_cache", "**/.ruff_cache", "**/.DS_Store", "tests", "**/*.egg-info", "ml/data",
    # offline evaluation only; serving reads xgb_temporal, gnn_temporal, node_features_train and the ensemble
    f"{IBM}/logs", f"{IBM}/*_scores.npz", f"{IBM}/*.joblib", f"{IBM}/*.md", f"{IBM}/ibm_xgb_tuned.json",
    f"{IBM}/ibm_xgb_paper*", f"{IBM}/ibm_gnn.pt", f"{IBM}/ibm_node_features.parquet",
]

image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("libgomp1")  # xgboost's OpenMP runtime
    # dependencies first (cached layer); "-e ../agents" comes from the copied package below, pytest is test-only
    .add_local_file(ROOT / "backend" / "requirements.txt", "/tmp/requirements.txt", copy=True)
    .run_commands(
        "grep -vE '^(-e |pytest)' /tmp/requirements.txt > /tmp/req.txt",
        # CPU-only torch wheels (the default Linux wheel pulls ~3 GB of CUDA libraries)
        "pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/req.txt",
    )
    .add_local_dir(ROOT / "agents", "/app/agents", copy=True, ignore=IGNORE)
    .run_commands("pip install --no-cache-dir /app/agents")
    .env({
        "ENVIRONMENT": "production", "LLM_PROVIDER": "groq",
        "APPROVED_DATA_DESTINATIONS": '["local","supabase","groq"]',  # Groq receives case evidence: declared
        # one thread per library: torch/xgboost would otherwise size pools to the host's cores inside 1 vCPU
        "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
    })
    .add_local_dir(ROOT / "backend", "/app/backend", ignore=IGNORE)  # code + serving artifacts, mounted at start
)

app = modal.App("tracelayer-api", image=image)


# Request-driven: zero containers while idle, at most one when busy (chat sessions, paused case workflows and
# model caches live in process memory, so a second container would split them). timeout covers agent runs,
# which execute as background tasks of the request.
@app.function(secrets=[modal.Secret.from_name("tracelayer-api")], cpu=1.0, memory=2048, timeout=3600,
              min_containers=0, buffer_containers=0, max_containers=1, scaledown_window=SCALEDOWN_S)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def api():
    import os
    import sys

    os.chdir("/app/backend")  # relative paths (artifacts, sample data) resolve as in local runs
    sys.path.insert(0, "/app/backend")
    from main import app as fastapi_app

    # load the models once per container, before serving: no first-request stall, no duplicate concurrent loads
    from app.services.detection import benchmarks
    from ml.ibm.ensemble import EnsembleScorer

    for name in ("xgb_temporal", "gnn_temporal"):
        benchmarks._scorer(name)
    EnsembleScorer("gnn_temporal", "xgb_temporal", benchmarks.ART)
    return fastapi_app
