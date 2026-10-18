"""Model-run manifest and evaluation helpers (PRD §5 items "persist run ID, inference time, artifact checksum,
reproducibility metadata" and "Evaluation: precision@K, recall@K, latency").

    from ml.engine.manifest import run_manifest, timed, at_k
    with timed() as t: scores = scorer.predict(X)
    manifest = run_manifest("xgb_temporal", ["ml/artifacts/ibm/ibm_xgb_temporal.json"], threshold=thr,
                            inference_ms=t.ms, n_scored=len(scores))
"""
import hashlib
import json
import platform
import subprocess
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import numpy as np


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:  # not a git checkout (e.g. Colab): recorded as missing, not guessed
        return None


@contextmanager
def timed():
    class T:
        ms = None
    t0 = time.perf_counter()
    yield T
    T.ms = round((time.perf_counter() - t0) * 1000, 2)


def run_manifest(model_name, artifact_paths, *, model_version=None, feature_version=None, threshold=None,
                 inference_ms=None, n_scored=None, split_config=None, metrics=None) -> dict:
    """Everything needed to reproduce and audit one scoring run. Missing provenance is None, never inferred."""
    import xgboost, sklearn, pandas  # noqa: E401  (versions are part of reproducibility)
    return {
        "run_id": str(uuid.uuid4()),
        "model_name": model_name,
        "model_version": model_version,
        "feature_version": feature_version,
        "artifacts": [{"path": str(p), "sha256": sha256(p), "bytes": Path(p).stat().st_size} for p in artifact_paths],
        "threshold": threshold,
        "inference_ms": inference_ms,
        "ms_per_1k": round(inference_ms / n_scored * 1000, 3) if inference_ms and n_scored else None,
        "n_scored": n_scored,
        "split_config": split_config,
        "metrics": metrics,
        "code_commit": _git_commit(),
        "runtime": {"python": platform.python_version(), "xgboost": xgboost.__version__, "sklearn": sklearn.__version__,
                    "pandas": pandas.__version__},
        "created_at": datetime.now(UTC).isoformat(),
    }


def at_k(y, scores, ks=(100, 500, 1000)) -> dict:
    """precision@K and recall@K on a ranked alert queue (stable tie order)."""
    order = np.argsort(-np.asarray(scores), kind="stable")
    y = np.asarray(y)[order]
    total = max(int(y.sum()), 1)
    return {f"{m}@{k}": float(y[:k].sum() / (k if m == "precision" else total)) for k in ks for m in ("precision", "recall")}


def write_manifest(manifest: dict, out_dir) -> Path:
    p = Path(out_dir) / f"run_{manifest['model_name']}_{manifest['run_id'][:8]}.json"
    p.write_text(json.dumps(manifest, indent=2, default=str))
    return p
