"""Policy corpus storage and retrieval for RAG. Chunking/embedding happen in the agent service."""

import hashlib
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from postgrest.exceptions import APIError

from app.api.deps import Limit, Offset, page
from app.core import audit
from app.core.db import get_db, to_json, with_actor
from app.core.errors import NotFound
from app.core.security import ANY_CALLER, CAN_MANAGE_POLICY, CurrentUser
from app.schemas import PolicyDocumentCreate, PolicySearch

router = APIRouter(prefix="/policy", tags=["policy (RAG)"])

EMBEDDING_DIMENSIONS = 768  # must match policy_chunks.embedding vector(768)


def _vector(values: list[float]) -> str:
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def _visible(query, user: CurrentUser):
    return query.or_(f"tenant_id.is.null,tenant_id.eq.{user.tenant_id}")


@router.post("/documents", status_code=201)
def ingest_document(body: PolicyDocumentCreate, user: CurrentUser = Depends(CAN_MANAGE_POLICY)) -> dict:
    """Store a curated guidance document and its chunks (with optional pre-computed embeddings).

    Re-ingesting the same source_url revises the document in place: chunks are upserted by chunk_index so
    their ids (and any citations of them) stay valid; stale chunks are removed unless something cites them.
    """
    for chunk in body.chunks:
        if chunk.embedding is not None and len(chunk.embedding) != EMBEDDING_DIMENSIONS:
            raise HTTPException(422, f"Embeddings must have {EMBEDDING_DIMENSIONS} dimensions")
    if any(c.embedding for c in body.chunks) and not body.embedding_model:
        raise HTTPException(422, "embedding_model is required when embeddings are supplied")
    if len({c.chunk_index for c in body.chunks}) != len(body.chunks):
        raise HTTPException(422, "chunk_index values must be unique")
    # Shared guidance is visible to every tenant, so only the platform's own workers may write it.
    if body.shared and not user.is_service:
        raise HTTPException(403, "Only platform workers can write shared guidance; set shared=false for tenant documents")

    db = get_db()
    tenant_id = None if body.shared else user.tenant_id
    checksum = hashlib.sha256("\n".join(c.content for c in sorted(body.chunks, key=lambda c: c.chunk_index)).encode()).hexdigest()
    fields = to_json({
        "tenant_id": tenant_id, "title": body.title, "publisher": body.publisher, "source_url": body.source_url,
        "document_type": body.document_type, "published_date": body.published_date, "effective_date": body.effective_date,
        "retrieved_at": body.retrieved_at, "content_checksum": checksum,
    })
    lookup = db.table("policy_documents").select("id").eq("source_url", body.source_url)
    existing = (lookup.is_("tenant_id", "null") if tenant_id is None else lookup.eq("tenant_id", tenant_id)).execute().data
    if existing:
        query = db.table("policy_documents").update(fields).eq("id", existing[0]["id"])
    else:
        query = db.table("policy_documents").insert(fields)
    document = with_actor(query, user).execute().data[0]

    rows = [
        to_json({
            "tenant_id": tenant_id, "document_id": document["id"], "chunk_index": c.chunk_index, "section": c.section,
            "content": c.content, "token_count": c.token_count,
            "embedding": _vector(c.embedding) if c.embedding else None,
            "embedding_model": body.embedding_model if c.embedding else None,
        })
        for c in body.chunks
    ]
    for i in range(0, len(rows), 100):
        with_actor(db.table("policy_chunks").upsert(rows[i : i + 100], on_conflict="document_id,chunk_index"), user).execute()

    kept_stale = 0
    if existing:
        indexes = {c.chunk_index for c in body.chunks}
        stale = [c for c in db.table("policy_chunks").select("id, chunk_index").eq("document_id", document["id"]).execute().data
                 if c["chunk_index"] not in indexes]
        for chunk in stale:
            try:
                db.table("policy_chunks").delete().eq("id", chunk["id"]).execute()
            except APIError:
                kept_stale += 1  # still cited by case evidence or a narrative; keep it so the citation resolves
    audit.record(user, "policy.document_indexed", "policy_document", document["id"],
                 {"source_url": body.source_url, "chunks": len(body.chunks), "revised": bool(existing),
                  "stale_chunks_kept": kept_stale})
    return {**document, "chunk_count": len(body.chunks), "revised": bool(existing), "stale_chunks_kept": kept_stale}


@router.get("/documents")
def list_documents(limit: Limit = 50, offset: Offset = 0, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    query = _visible(get_db().table("policy_documents").select("*, policy_chunks(count)", count="exact"), user).order("title")
    return page(query, limit, offset)


@router.get("/documents/{document_id}")
def get_document(document_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    rows = _visible(get_db().table("policy_documents").select("*").eq("id", str(document_id)), user).execute().data
    if not rows:
        raise NotFound("Policy document")
    chunks = (get_db().table("policy_chunks").select("id, chunk_index, section, content, token_count, embedding_model")
              .eq("document_id", str(document_id)).order("chunk_index").execute().data)
    return {**rows[0], "chunks": chunks}


@router.get("/chunks/{chunk_id}")
def get_chunk(chunk_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    rows = _visible(get_db().table("policy_chunks").select(
        "id, chunk_index, section, content, policy_documents(id, title, publisher, source_url, published_date)"
    ).eq("id", str(chunk_id)), user).execute().data
    if not rows:
        raise NotFound("Policy chunk")
    return rows[0]


@router.post("/search")
def search(body: PolicySearch, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    """Hybrid keyword + vector retrieval (reciprocal rank fusion) over shared and tenant guidance."""
    if body.query_embedding is not None and len(body.query_embedding) != EMBEDDING_DIMENSIONS:
        raise HTTPException(422, f"query_embedding must have {EMBEDDING_DIMENSIONS} dimensions")
    result = get_db().rpc("match_policy_chunks", {
        "query_embedding": _vector(body.query_embedding) if body.query_embedding else None,
        "query_text": body.query_text,
        "match_count": body.match_count,
        "filter_tenant_id": user.tenant_id,
    }).execute()
    return {"results": result.data or [], "mode": "hybrid" if body.query_embedding and body.query_text
            else "vector" if body.query_embedding else "keyword"}
