"""Canonical hashing and the append-only audit hash chain.

`hash = sha256(prev_hash + canonical_json(event))`, where the event is serialized without its own
`prev_hash`/`hash` fields and a missing `prev_hash` (genesis) counts as "".
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic_core import to_jsonable_python

from tracelayer_agents.interfaces import AuditRepository
from tracelayer_agents.schemas import AuditEvent, AuditVerification

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
    actor_id: str = SYSTEM_ACTOR_ID,
    actor_role: str = SYSTEM_ACTOR_ROLE,
    before_hash: str | None = None,
    after_hash: str | None = None,
    prompt_version: str | None = None,
) -> AuditEvent:
    """Chain an event onto `repo`'s head and append it. The actor is the system unless given (a
    human decision passes its own). Not safe for concurrent writers on one repo; the repo rejects
    a stale `prev_hash`, so a race fails loudly instead of forking."""
    event = AuditEvent(
        event_id=f"evt_{uuid.uuid4().hex}",
        actor_id=actor_id,
        actor_role=actor_role,
        action=action,
        object_type=object_type,
        object_id=object_id,
        before_hash=before_hash,
        after_hash=after_hash,
        model_versions=model_versions or {},
        prompt_version=prompt_version,
        timestamp=datetime.now(UTC),
        reason=reason,
        metadata=metadata or {},
    )
    prev = repo.last_hash()
    chained = event.model_copy(update={"prev_hash": prev, "hash": event_hash(event, prev)})
    return repo.append(chained)


def verify_chain(events: Sequence[AuditEvent]) -> AuditVerification:
    """Check a chain in insertion order: each event's `prev_hash` must be the previous event's
    `hash` (None for the first), and its `hash` must match the recomputed one. Stops at the first
    bad event."""
    prev: str | None = None
    for i, event in enumerate(events):
        if event.prev_hash != prev:
            return AuditVerification(
                ok=False, checked=i, first_bad_index=i,
                reason=f"event {event.event_id!r}: prev_hash does not link to the previous event",
            )
        if event.hash != event_hash(event, prev):
            return AuditVerification(
                ok=False, checked=i, first_bad_index=i,
                reason=f"event {event.event_id!r}: hash mismatch (event content changed)",
            )
        prev = event.hash
    return AuditVerification(ok=True, checked=len(events))
