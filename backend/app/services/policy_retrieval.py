"""Local embeddings and model-compatible hybrid search; keyword retrieval remains explicit fallback."""
import math
import os
from urllib.parse import urlparse

import httpx

from app.core.db import get_db

DIMENSIONS = 768


def embed(texts: list[str]) -> list[list[float]]:
    host = os.getenv('OLLAMA_HOST', 'http://127.0.0.1:11434').rstrip('/')
    if urlparse(host).hostname not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Embeddings require a local Ollama host')
    model = os.getenv('POLICY_EMBEDDING_MODEL', 'nomic-embed-text')
    with httpx.Client(timeout=30, trust_env=False) as client:
        response = client.post(host + '/api/embed', json={'model': model, 'input': texts, 'truncate': False})
        response.raise_for_status()
        vectors = response.json()['embeddings']
    if len(vectors) != len(texts) or any(len(v) != DIMENSIONS or not all(math.isfinite(x) for x in v)
                                        or not any(v) for v in vectors):
        raise ValueError('Invalid local embedding dimensions or values')
    return vectors


def search(tenant_id: str, query: str, top_k: int = 8) -> dict:
    model = os.getenv('POLICY_EMBEDDING_MODEL', 'nomic-embed-text')
    try:
        vector = embed([query])[0]
    except (httpx.HTTPError, ValueError, KeyError):
        vector = None
    # Existing SQL does not filter embedding model identity. Use it for lexical candidates only;
    # semantic ranking explicitly scopes both tenant and model before comparing vectors.
    lexical = get_db().rpc('match_policy_chunks', {'query_embedding': None, 'query_text': query,
        'match_count': min(top_k * 2, 30), 'filter_tenant_id': tenant_id}).execute().data or []
    if vector is None:
        return {'results': lexical[:top_k], 'mode': 'keyword', 'embedding_model': None}
    response = get_db().table('policy_chunks').select(
        'id,document_id,section,content,embedding,policy_documents(title,publisher,source_url)', count='exact').or_(
        f'tenant_id.is.null,tenant_id.eq.{tenant_id}').eq('embedding_model', model).not_.is_(
        'embedding', 'null').order('id').limit(1000).execute()
    # Never silently search a prefix and advertise it as complete hybrid retrieval.
    if response.count is None or response.count > 1000 or len(response.data) != response.count:
        return {'results': lexical[:top_k], 'mode': 'keyword', 'embedding_model': None,
                'limitation': 'Local semantic corpus exceeds the interactive cap; keyword retrieval used.'}
    import json
    def similarity(row):
        values = json.loads(row['embedding']) if isinstance(row['embedding'], str) else row['embedding']
        norm = math.sqrt(sum(x*x for x in values) * sum(x*x for x in vector))
        return sum(x*y for x,y in zip(values, vector, strict=True)) / norm if norm else -1
    ranked = sorted(((similarity(r), r) for r in response.data), key=lambda x: (-x[0], x[1]['id']))[:top_k*2]
    merged = {r['chunk_id']: {**r, 'rrf_score': 1/(51+i)} for i,r in enumerate(lexical)}
    for i,(score,r) in enumerate(ranked):
        if score < 0.3: continue
        item = merged.setdefault(r['id'], {'chunk_id':r['id'], 'document_id':r['document_id'],
            **r['policy_documents'], 'section':r['section'], 'content':r['content'], 'rrf_score':0})
        item['rrf_score'] += 1/(51+i)
        item['similarity'] = score
    return {'results': sorted(merged.values(), key=lambda r: (-r['rrf_score'],r['chunk_id']))[:top_k],
            'mode': 'hybrid' if response.data else 'keyword', 'embedding_model': model if response.data else None}
