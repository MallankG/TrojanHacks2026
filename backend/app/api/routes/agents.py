"""Authenticated, persisted multi-agent jobs. Browser callers never choose actors or services."""
from typing import Literal
from uuid import UUID
from tracelayer_agents import api as agent_api

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import ANY_USER, CAN_INVESTIGATE, CurrentUser, require_roles
from app.services.orchestration import runs

router=APIRouter(prefix='/agents',tags=['agent workflows'])

class RequestKey(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key:UUID

class Analysis(RequestKey):
    ingestion_run_id:UUID
    mode:Literal['strict','exploratory']='strict'

class Question(RequestKey):
    question:str=Field(min_length=3,max_length=2000)

    @field_validator('question',mode='before')
    @classmethod
    def trim(cls,value):return value.strip() if isinstance(value,str) else value

class Decision(BaseModel):
    model_config=ConfigDict(extra='forbid')
    action:Literal['escalate','dismiss','request_more_data']
    reason:str=Field(min_length=10,max_length=4000)

    @field_validator('reason',mode='before')
    @classmethod
    def trim(cls,value):return value.strip() if isinstance(value,str) else value

@router.post('/analysis',status_code=202)
def analyze(body:Analysis,jobs:BackgroundTasks,user:CurrentUser=Depends(require_roles('admin','analyst'))):
    row,new=runs.create(user,'analysis_workflow',str(body.request_key),batch=str(body.ingestion_run_id),mode=body.mode)
    if new:jobs.add_task(runs.execute,user,row['id'])
    return row

@router.post('/cases/{case_id}/draft',status_code=202)
def draft(case_id:UUID,body:RequestKey,jobs:BackgroundTasks,user:CurrentUser=Depends(CAN_INVESTIGATE)):
    row,new=runs.create(user,'case_workflow',str(body.request_key),cid=str(case_id))
    if new:jobs.add_task(runs.execute,user,row['id'])
    return row

@router.post('/cases/{case_id}/copilot',status_code=202)
def copilot(case_id:UUID,body:Question,jobs:BackgroundTasks,user:CurrentUser=Depends(CAN_INVESTIGATE)):
    row,new=runs.create(user,'copilot_workflow',str(body.request_key),cid=str(case_id),question=body.question.strip())
    if new:jobs.add_task(runs.execute,user,row['id'])
    return row

@router.get('/runs/{run_id}')
def status(run_id:UUID,user:CurrentUser=Depends(ANY_USER)):
    return runs.read(user,str(run_id))

@router.post('/runs/{run_id}/decision',status_code=202)
def decide(run_id:UUID,body:Decision,jobs:BackgroundTasks,user:CurrentUser=Depends(CAN_INVESTIGATE)):
    decision=runs.preflight_decision(user,str(run_id),body.action,body.reason.strip())
    claimed=runs.claim(user,str(run_id),resume=True)
    jobs.add_task(runs.execute,user,str(run_id),decision,claimed)
    return runs.read(user,str(run_id))

@router.post('/runs/{run_id}/recover',status_code=202)
def recover(run_id:UUID,jobs:BackgroundTasks,user:CurrentUser=Depends(CAN_INVESTIGATE)):
    from fastapi import HTTPException
    row=runs.read(user,str(run_id))
    if row['graph_name']!='case_workflow':
        raise HTTPException(409,'Only case drafts support checkpoint recovery. Review partial analysis outputs before starting a new batch run.')
    if row['status'] not in ('queued','running','failed'):
        raise HTTPException(409,'This run does not need recovery')
    runs.authorize_recovery(user,row)
    claimed=runs.claim(user,str(run_id),resume=row['status']!='queued')
    jobs.add_task(runs.execute,user,str(run_id),None,claimed,True)
    return runs.read(user,str(run_id))

@router.get('/runs/{run_id}/audit-verification')
def verify_audit(run_id:UUID,user:CurrentUser=Depends(ANY_USER)):
    from fastapi import HTTPException
    from app.core.db import get_db
    from tracelayer_agents import api
    runs.read(user,str(run_id))
    response=get_db().table('agent_chain_receipts').select('event',count='exact').eq('tenant_id',user.tenant_id).eq(
        'run_id',str(run_id)).order('sequence').limit(1000).execute()
    if response.count is None:
        raise HTTPException(502,'The database did not return a complete audit count')
    if response.count>1000 or len(response.data)!=response.count:
        raise HTTPException(422,'Audit chain exceeds the interactive verification cap')
    return api.verify_events([api.contracts.AuditEvent.model_validate(r['event']) for r in response.data]).model_dump(mode='json')


class DraftReview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    draft: agent_api.contracts.NarrativeDraft
    request_key:UUID | None=None


def _review_material(user,run_id,body):
    from app.core.db import get_db
    from tracelayer_agents import api
    from app.services.orchestration.adapters import Context,Cases
    row=runs.read(user,str(run_id))
    if row['graph_name']!='case_workflow' or not row['case_id']:
        from fastapi import HTTPException
        raise HTTPException(422,'Choose a case narrative workflow')
    saved=get_db().table('case_narratives').select('id,verification_report').eq('tenant_id',user.tenant_id).eq(
        'agent_run_id',str(run_id)).order('version',desc=True).limit(1).execute().data
    if not saved:
        from app.core.errors import NotFound
        raise NotFound('Saved draft')
    sources=[api.contracts.RetrievedChunk.model_validate(s) for s in saved[0]['verification_report'].get('sources',[])]
    bundle=Cases(Context(user,row,None)).get_evidence(row['alert_id'])
    draft=body.draft.model_copy(update={'provider':'template','model':'human-reviewed',
        'prompt_version':'human-edit-v1','fallback_used':False})
    errors=api.verify_narrative(draft,bundle,sources)
    return row,saved[0],draft,bundle,sources,errors


@router.post('/runs/{run_id}/verify-draft')
def verify_draft(run_id:UUID,body:DraftReview,user:CurrentUser=Depends(CAN_INVESTIGATE)):
    from app.core import audit
    row,_,_,bundle,_,errors=_review_material(user,run_id,body)
    audit.record(user,'case.draft_preview_verified','case',row['case_id'],{'errors':len(errors),'saved':False})
    return {'passed':not errors,'verifier_errors':[e.model_dump(mode='json') for e in errors],
            'bundle_hash':bundle.bundle_hash,'saved':False}


@router.post('/runs/{run_id}/revisions',status_code=201)
def save_revision(run_id:UUID,body:DraftReview,user:CurrentUser=Depends(CAN_INVESTIGATE)):
    """Save an append-only human revision. Reverify server-side, preserving the original workflow receipt."""
    from fastapi import HTTPException
    from app.core.db import get_db,with_actor
    row,parent,draft,bundle,sources,errors=_review_material(user,run_id,body)
    if not body.request_key: raise HTTPException(422,'request_key is required to save an idempotent revision')
    # Replay first: a retry after a lost response returns the committed revision even if the case closed since.
    old=get_db().table('case_narratives').select('id,version,verification_status,authored_by,agent_run_id').eq(
        'tenant_id',user.tenant_id).eq('verification_report->>revision_key',str(body.request_key)).limit(1).execute().data
    if old:
        if old[0]['authored_by']!=user.id or old[0]['agent_run_id']!=str(run_id):raise HTTPException(409,'Request key already used')
        return {k:v for k,v in old[0].items() if k in ('id','version','verification_status')}
    runs.authorize_case(user,row['case_id'],'write')
    if errors: raise HTTPException(422,{'message':'Resolve verifier errors before saving','verifier_errors':[e.model_dump(mode='json') for e in errors]})
    if row['status']!='awaiting_review':raise HTTPException(409,'Wait for a draft awaiting human review')
    claimed=runs.claim(user,str(run_id),resume=True)
    receipt={'parent_id':parent['id'],'content':draft.summary,'draft':draft.model_dump(mode='json'),
        'errors':[],'bundle_hash':bundle.bundle_hash,'sources':[s.model_dump(mode='json') for s in sources],
        'claims':[{'text':c.text,'kind':'hypothesis' if c.claim_type=='hypothesis' else 'fact',
            'transactions':c.transaction_ids,'accounts':c.account_ids,'citations':c.citation_chunk_ids,
            'values':{'amounts':[str(x) for x in c.amounts],'dates':[str(x) for x in c.dates]}}
            for c in draft.observed_facts]}
    try:
        narrative=with_actor(get_db().rpc('save_workflow_revision',{'p_tenant':user.tenant_id,'p_run':str(run_id),
            'p_token':claimed[1],'p_actor':user.id,'p_key':str(body.request_key),'p_receipt':receipt}),user).execute().data
    except Exception:
        # A network failure after a committed RPC is safe to retry with the request key.  Do not let a
        # best-effort lease release hide the original error.
        try:
            with_actor(get_db().rpc('finish_workflow_run',{'p_tenant':user.tenant_id,'p_run':str(run_id),'p_token':claimed[1],
                'p_changes':{'status':'awaiting_review','lease_token':None,'lease_expires_at':None}}),user).execute()
        except Exception:
            pass
        raise
    return narrative
