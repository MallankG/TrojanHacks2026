"""Local embeddings and model-compatible hybrid search; keyword retrieval remains explicit fallback."""
import math
import os
from urllib.parse import urlparse

import httpx

from app.core.db import get_db

DIMENSIONS = 768


def embed(texts: list[str], timeout: float = 30) -> list[list[float]]:
    host = os.getenv('OLLAMA_HOST', 'http://127.0.0.1:11434').rstrip('/')
    if urlparse(host).hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Embeddings require a local Ollama host')
    model = os.getenv('POLICY_EMBEDDING_MODEL', 'nomic-embed-text')
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.post(host + '/api/embed', json={'model': model, 'input': texts, 'truncate': False})
        response.raise_for_status()
        vectors = response.json().get('embeddings')
    if not isinstance(vectors, list):
        raise ValueError('Embedding response has no embeddings list')
    if len(vectors) != len(texts) or any(len(v) != DIMENSIONS or not all(math.isfinite(x) for x in v)
                                        or not any(v) for v in vectors):
        raise ValueError('Invalid local embedding dimensions or values')
    return vectors


def _vector(values: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in values) + "]"


def search(tenant_id: str, query: str, top_k: int = 8) -> dict:
    """Hybrid keyword + vector retrieval in pgvector, comparing only same-model embeddings;
    keyword-only when the local embedding model is unavailable."""
    model = os.getenv('POLICY_EMBEDDING_MODEL', 'nomic-embed-text')
    try:
        vector = embed([query], timeout=3)[0]  # interactive: a slow model falls back to keyword quickly
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        vector = None
    rows = get_db().rpc('match_policy_chunks', {
        'query_embedding': _vector(vector) if vector else None, 'query_text': query,
        'match_count': top_k, 'filter_tenant_id': tenant_id,
        'filter_embedding_model': model if vector else None}).execute().data or []
    semantic = vector is not None and any(r.get('similarity') is not None for r in rows)
    return {'results': rows, 'mode': 'hybrid' if semantic else 'keyword',
            'embedding_model': model if semantic else None}
