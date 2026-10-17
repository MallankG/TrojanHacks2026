"""Canonical hashing and the append-only audit hash chain.

`hash = sha256(prev_hash + canonical_json(event))`, where the event is serialized without its own
`prev_hash`/`hash` fields and a missing `prev_hash` (genesis) counts as "".
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic_core import to_jsonable_python

from tracelayer_agents.interfaces import AuditRepository
from tracelayer_agents.schemas import AuditEvent

SYSTEM_ACTOR_ID = "tracelayer_agents"
SYSTEM_ACTOR_ROLE = "system"


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace; models, Decimals and datetimes as JSON."""
    return json.dumps(
        to_jsonable_python(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def sha256_json(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()


def event_hash(event: AuditEvent, prev_hash: str | None) -> str:
    body = event.model_dump(mode="json", exclude={"prev_hash", "hash"})
    return hashlib.sha256(((prev_hash or "") + canonical_json(body)).encode()).hexdigest()


def record(
    repo: AuditRepository,
    *,
    action: str,
    object_type: str,
    object_id: str,
    metadata: dict[str, Any] | None = None,
    model_versions: dict[str, str] | None = None,
    reason: str | None = None,
) -> AuditEvent:
    """Chain a system event onto `repo`'s head and append it. Not safe for concurrent writers on
    one repo; the repo rejects a stale `prev_hash`, so a race fails loudly instead of forking."""
    event = AuditEvent(
        event_id=f"evt_{uuid.uuid4().hex}",
        actor_id=SYSTEM_ACTOR_ID,
        actor_role=SYSTEM_ACTOR_ROLE,
        action=action,
        object_type=object_type,
        object_id=object_id,
        model_versions=model_versions or {},
        timestamp=datetime.now(UTC),
        reason=reason,
        metadata=metadata or {},
    )
    prev = repo.last_hash()
    chained = event.model_copy(update={"prev_hash": prev, "hash": event_hash(event, prev)})
    return repo.append(chained)
