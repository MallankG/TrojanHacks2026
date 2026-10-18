"""Supabase access with the service-role key (bypasses RLS).

Every query issued through this module must filter by tenant_id explicitly; tenant scoping is the
backend's responsibility because RLS does not apply to the service role.
"""

from contextvars import ContextVar
from collections.abc import Callable, Iterable
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any
from uuid import UUID

import httpx
from supabase import Client, create_client

from app.core.config import get_settings

workflow_lease_var = ContextVar("workflow_lease", default=None)

PAGE_SIZE = 1000
INSERT_CHUNK = 500


@lru_cache
def get_db() -> Client:
    settings = get_settings()
    # Concurrent dashboard reads use independent HTTP/1.1 pooled connections. A shared
    # HTTP/2 connection can be closed by the upstream while other streams are active.
    client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    rest = client.postgrest
    original = rest.session
    # Keep this transport private to PostgREST: Storage changes its own base URL.
    rest.session = httpx.Client(base_url=original.base_url, headers=original.headers,
                                http2=False, timeout=30.0)
    original.close()
    return client


def to_json(value: Any) -> Any:
    """Make a value JSON-safe without losing money precision (Decimal -> str, never float)."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [to_json(v) for v in value]
    return str(value)


def fetch_all(build_query: Callable[[], Any], page_size: int = PAGE_SIZE) -> list[dict]:
    """Page through a PostgREST query. build_query must return a fresh, ordered query each call."""
    rows: list[dict] = []
    start = 0
    while True:
        page = build_query().range(start, start + page_size - 1).execute().data or []
        rows.extend(page)
        if len(page) < page_size:
            return rows
        start += page_size


def with_actor(query: Any, actor: Any | None) -> Any:
    """Forward the acting user to Postgres so audit triggers attribute the change correctly.

    actor is a CurrentUser (duck-typed to avoid a circular import). The headers are per-query.
    """
    if lease := workflow_lease_var.get():
        query.headers["x-workflow-run"] = lease[0]
        query.headers["x-workflow-token"] = lease[1]
    if actor is not None:
        if actor.id:
            query.headers["x-actor-id"] = actor.id
        query.headers["x-actor-type"] = actor.actor_type
    return query


def insert_many(table: str, rows: list[dict], chunk: int = INSERT_CHUNK, actor: Any | None = None) -> list[dict]:
    """Insert rows in chunks and return the inserted rows (with generated ids)."""
    db = get_db()
    inserted: list[dict] = []
    for i in range(0, len(rows), chunk):
        batch = [to_json(r) for r in rows[i : i + chunk]]
        if batch:
            # default_to_null=False: keys missing from a row take the column default instead of NULL.
            inserted.extend(with_actor(db.table(table).insert(batch, default_to_null=False), actor).execute().data or [])
    return inserted


def chunked(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def fetch_by_ids(table: str, tenant_id: str, ids: Iterable[str], columns: str = "*", id_column: str = "id") -> list[dict]:
    db = get_db()
    unique_ids = sorted({str(i) for i in ids if i})
    rows: list[dict] = []
    # Keep URLs short: PostgREST "in" filters travel in the query string.
    for batch in chunked(unique_ids, 150):
        rows.extend(
            db.table(table).select(columns).eq("tenant_id", tenant_id).in_(id_column, batch).execute().data or []
        )
    return rows


def get_one(table: str, tenant_id: str, row_id: str, columns: str = "*") -> dict | None:
    rows = get_db().table(table).select(columns).eq("tenant_id", tenant_id).eq("id", row_id).limit(1).execute().data
    return rows[0] if rows else None
