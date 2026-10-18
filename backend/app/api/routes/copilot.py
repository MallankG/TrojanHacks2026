"""Agent integration: agent runs, narrative versions, claims, citations and verifier results.

The agent service (LangGraph) drafts and verifies; this API stores every artifact so the case is reproducible.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import ensure_tenant_ids
from app.core.db import fetch_by_ids, get_db, get_one, insert_many, to_json, with_actor
from app.core.errors import NotFound
from app.core.security import ANY_CALLER, CAN_INVESTIGATE_OR_SERVICE, CurrentUser
from app.schemas import AgentRunCreate, AgentRunUpdate, NarrativeCreate, NarrativeVerification
from app.services import cases as case_service

router = APIRouter(tags=["copilot (agent integration)"])


@router.post("/cases/{case_id}/agent-runs", status_code=201)
def create_agent_run(case_id: UUID, body: AgentRunCreate, user: CurrentUser = Depends(CAN_INVESTIGATE_OR_SERVICE)) -> dict:
    if not get_one("cases", user.tenant_id, str(case_id), "id"):
        raise NotFound("Case")
    row = {**body.model_dump(), "tenant_id": user.tenant_id, "case_id": str(case_id), "status": "queued", "triggered_by": user.id}
    return insert_many("agent_runs", [row], actor=user)[0]


@router.patch("/agent-runs/{run_id}")
def update_agent_run(run_id: UUID, body: AgentRunUpdate, user: CurrentUser = Depends(CAN_INVESTIGATE_OR_SERVICE)) -> dict:
    existing = get_one("agent_runs", user.tenant_id, str(run_id), "id,managed")
    if not existing:
        raise NotFound("Agent run")
    if existing["managed"]:
        raise HTTPException(409, "Managed workflows report their own state")
    now = datetime.now(UTC).isoformat()
    changes: dict = body.model_dump(exclude_unset=True, exclude={"append_steps"})
    if body.status == "running":
        changes["started_at"] = now  # only applied if not already set
    if body.status in ("completed", "failed", "awaiting_review"):
        changes["completed_at"] = now
    steps = [{**s.model_dump(mode="json"), "at": s.at.isoformat() if s.at else now} for s in body.append_steps]
    # Atomic append in the database so concurrent step reports cannot overwrite each other.
    query = get_db().rpc("append_agent_steps", {
        "p_run_id": str(run_id), "p_tenant_id": user.tenant_id, "p_steps": steps, "p_changes": to_json(changes),
    })
    return with_actor(query, user).execute().data[0]


@router.get("/agent-runs/{run_id}")
def get_agent_run(run_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    run = get_one("agent_runs", user.tenant_id, str(run_id))
    if not run:
        raise NotFound("Agent run")
    if run.get("managed"):
        from app.services.orchestration.runs import read
        return read(user, str(run_id))
    return run


@router.post("/cases/{case_id}/narratives", status_code=201)
def create_narrative(case_id: UUID, body: NarrativeCreate, user: CurrentUser = Depends(CAN_INVESTIGATE_OR_SERVICE)) -> dict:
    """Store a narrative version (agent draft or human edit) with its structured claims and citations."""
    tid, cid = user.tenant_id, str(case_id)
    if not get_one("cases", tid, cid, "id"):
        raise NotFound("Case")
    if user.is_service and not body.llm_model:
        raise HTTPException(422, "Agent-authored narratives must record llm_model")
    ensure_tenant_ids("transactions", tid, {t for c in body.claims for t in c.referenced_transaction_ids}, "transaction ids")
    ensure_tenant_ids("accounts", tid, {a for c in body.claims for a in c.referenced_account_ids}, "account ids")
    chunk_ids = {str(c.policy_chunk_id) for c in body.citations} | {str(x) for c in body.claims for x in c.cited_chunk_ids}
    if chunk_ids:
        chunks = get_db().table("policy_chunks").select("id, tenant_id").in_("id", sorted(chunk_ids)).execute().data
        valid = {c["id"] for c in chunks if c["tenant_id"] in (None, tid)}
        if chunk_ids - valid:
            raise HTTPException(422, f"Unknown policy chunks: {sorted(chunk_ids - valid)[:10]}")
    for ref, table in ((body.parent_narrative_id, "case_narratives"), (body.agent_run_id, "agent_runs")):
        if ref and not fetch_by_ids(table, tid, [ref], "id"):
            raise HTTPException(422, f"Unknown {table[:-1].replace('_', ' ')}")

    if body.agent_run_id:
        managed = get_one("agent_runs", tid, str(body.agent_run_id), "case_id,managed")
        if managed and (managed["managed"] or managed["case_id"] != cid):
            raise HTTPException(409, "Managed drafts are saved by the workflow")
    row = {
        "tenant_id": tid, "case_id": cid, "version": case_service.next_narrative_version(tid, cid),
        "parent_narrative_id": body.parent_narrative_id, "agent_run_id": body.agent_run_id,
        "author_type": "agent" if user.is_service else "user", "authored_by": user.id, "content": body.content,
        "llm_provider": body.llm_provider, "llm_model": body.llm_model, "prompt_version": body.prompt_version,
    }
    narrative = insert_many("case_narratives", [row], actor=user)[0]
    insert_many("narrative_claims", [{**c.model_dump(), "tenant_id": tid, "narrative_id": narrative["id"], "claim_index": i}
                                     for i, c in enumerate(body.claims)], actor=user)
    insert_many("narrative_citations", [{**c.model_dump(), "tenant_id": tid, "narrative_id": narrative["id"]}
                                        for c in body.citations], actor=user)
    return narrative


@router.get("/cases/{case_id}/narratives")
def list_narratives(case_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> list[dict]:
    return (get_db().table("case_narratives").select("*").eq("tenant_id", user.tenant_id).eq("case_id", str(case_id))
            .order("version", desc=True).execute().data)


@router.get("/narratives/{narrative_id}")
def get_narrative(narrative_id: UUID, user: CurrentUser = Depends(ANY_CALLER)) -> dict:
    db = get_db()
    narrative = get_one("case_narratives", user.tenant_id, str(narrative_id))
    if not narrative:
        raise NotFound("Narrative")
    claims = (db.table("narrative_claims").select("*").eq("tenant_id", user.tenant_id).eq("narrative_id", str(narrative_id))
              .order("claim_index").execute().data)
    citations = (db.table("narrative_citations")
                 .select("quoted_text, policy_chunks(id, section, content, policy_documents(title, publisher, source_url))")
                 .eq("tenant_id", user.tenant_id).eq("narrative_id", str(narrative_id)).execute().data)
    unsupported = [c for c in claims if c["verification"] in ("unsupported", "unverifiable")]
    return {**narrative, "claims": claims, "citations": citations, "unsupported_claims": unsupported}


@router.put("/narratives/{narrative_id}/verification")
def record_verification(narrative_id: UUID, body: NarrativeVerification, request: Request,
                        user: CurrentUser = Depends(CAN_INVESTIGATE_OR_SERVICE)) -> dict:
    """Store the evidence verifier's result for a narrative and each of its claims."""
    db = get_db()
    tid, nid = user.tenant_id, str(narrative_id)
    request.state.operation_success = body.verification_status == "passed"
    narrative = get_one("case_narratives", tid, nid, "id,workflow_attempt")
    if not narrative:
        raise NotFound("Narrative")
    if narrative["workflow_attempt"] is not None:
        raise HTTPException(409, "Workflow verifier receipts are immutable")
    for claim in body.claims:
        query = (db.table("narrative_claims").update({"verification": claim.verification, "verifier_notes": claim.verifier_notes})
                 .eq("tenant_id", tid).eq("narrative_id", nid).eq("claim_index", claim.claim_index))
        if not with_actor(query, user).execute().data:
            raise HTTPException(422, f"Narrative has no claim_index {claim.claim_index}")
    query = db.table("case_narratives").update(to_json({
        "verification_status": body.verification_status,
        "verification_report": body.verification_report,
        "verified_at": datetime.now(UTC).isoformat(),
    })).eq("tenant_id", tid).eq("id", nid)
    return with_actor(query, user).execute().data[0]
