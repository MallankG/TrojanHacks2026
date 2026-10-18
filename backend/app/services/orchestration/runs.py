"""Job orchestration and leases; case checkpoints and traces survive process restarts."""
import asyncio
from contextlib import suppress
import logging
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import HTTPException
from tracelayer_agents import api

from app.core.config import get_settings
from app.core.db import get_db, get_one, insert_many, with_actor, workflow_lease_var
from app.core.errors import NotFound
from app.services import cases
from app.services.enterprise import require_case
from .adapters import Context, services
from .checkpoints import SupabaseCheckpointer

log = logging.getLogger(__name__)
PUBLIC_FIELDS = 'id,tenant_id,case_id,ingestion_run_id,alert_id,graph_name,graph_version,status,current_step,steps,result,error_message,created_at,started_at,completed_at,managed'


def authorize_case(user, cid, action='read', *, allow_closed=False):
    if get_settings().enterprise_enabled or get_settings().case_authorization_enabled:
        case, _, _ = require_case(user,cid,action)
    else:
        case = get_one('cases',user.tenant_id,cid)
        if not case: raise NotFound('Case')
    if action != 'read' and case['status']=='closed' and not allow_closed: raise HTTPException(409,'Case is closed')
    return case


def authorize_recovery(user, run):
    case=authorize_case(user,run['case_id'],'write',allow_closed=True)
    if case['status']=='closed':
        rows=get_db().table('case_decisions').select('id').eq('tenant_id',user.tenant_id).eq(
            'agent_run_id',run['id']).eq('decided_by',user.id).limit(1).execute().data
        if not rows:raise HTTPException(409,'Only the original reviewer can recover a decision already saved on a closed case')
    return case


def read(user, rid):
    row=get_one('agent_runs',user.tenant_id,rid,PUBLIC_FIELDS)
    if not row or not row['managed']:raise NotFound('Managed agent run')
    if row['case_id']:authorize_case(user,row['case_id'])
    if row.get('result'):
        row['result']={k:v for k,v in row['result'].items() if not k.startswith('_')}
    return row


def create(user, graph, key, *, cid=None, batch=None, question=None, mode='strict'):
    # Validate access before idempotency lookup to prevent revoked memberships leaking old results.
    aid=None
    if cid:
        authorize_case(user,cid,'write')
        links=get_db().table('case_alerts').select('alert_id').eq('tenant_id',user.tenant_id).eq('case_id',cid).order('added_at').limit(1).execute().data
        if not links:raise HTTPException(422,'Attach an alert with transaction evidence before running agents')
        aid=links[0]['alert_id']
        evidence=cases.evidence_bundle(user.tenant_id,cid)
        if not evidence['transactions'] or len(evidence['transactions'])>500:
            raise HTTPException(422,'Case agents require between 1 and 500 evidence transactions')
    else:
        ingestion=get_one('ingestion_runs',user.tenant_id,batch,'id,status,accepted_count')
        if not ingestion:raise NotFound('Ingestion run')
        if ingestion['status'] not in ('completed','completed_with_errors'):
            raise HTTPException(422,'Choose a completed ingestion run')
        if ingestion['accepted_count']>500:
            raise HTTPException(422,'Interactive analysis is limited to batches with at most 500 accepted rows')
    cfg={'mode':mode,'question':question}
    old=get_db().table('agent_runs').select('*').eq('tenant_id',user.tenant_id).eq('idempotency_key',key).eq('managed',True).limit(1).execute().data
    if old:
        r=old[0]
        if (r['graph_name'],r['case_id'],r['ingestion_run_id'],r['config'])!=(graph,cid,batch,cfg):
            raise HTTPException(409,'This request key was already used for a different operation')
        return read(user,r['id']),False
    row={'id':str(uuid4()),'tenant_id':user.tenant_id,'case_id':cid,'ingestion_run_id':batch,'alert_id':aid,
        'graph_name':graph,'graph_version':'live-v1','managed':True,'idempotency_key':key,'status':'queued',
        'triggered_by':user.id,'config':cfg}
    saved=insert_many('agent_runs',[row],actor=user)[0]
    return read(user,saved['id']),True


def preflight_decision(user, rid, action, reason):
    r=read(user,rid)
    if r['graph_name']!='case_workflow' or not r['case_id']:raise HTTPException(422,'Choose a case draft workflow')
    case=authorize_case(user,r['case_id'],'write')
    if r['status']!='awaiting_review':raise HTTPException(409,'This draft is not awaiting review')
    if action in ('escalate','dismiss') and user.role not in ('admin','reviewer'):
        raise HTTPException(403,'A reviewer must make the final decision')
    cases.check_four_eyes(case,user,'escalate' if action=='escalate' else 'reject', 'false_positive' if action=='dismiss' else None)
    if action in ('escalate','dismiss'):
        ns=get_db().table('case_narratives').select('verification_status').eq('tenant_id',user.tenant_id).eq('agent_run_id',rid).order('version',desc=True).limit(1).execute().data
        if not ns or ns[0]['verification_status']!='passed':raise HTTPException(422,'A verified draft is required for a final decision')
    return api.Decision(action=action,reason=reason,actor_id=user.id,
        actor_role='supervisor' if user.role in ('admin','reviewer') else 'analyst')


def claim(user, rid, resume=False):
    token=str(uuid4())
    row=with_actor(get_db().rpc('claim_workflow_run',{'p_tenant':user.tenant_id,'p_run':rid,
        'p_token':token,'p_resume':resume}),user).execute().data
    return row,token


async def execute(user, rid, decision=None, claimed=None, recover=False):
    if claimed is None:
        try: claimed=await asyncio.to_thread(claim,user,rid)
        except Exception:
            log.exception('Agent job could not claim run %s',rid)
            return
    r,token=claimed
    scope=workflow_lease_var.set((rid,token))
    owner=asyncio.current_task()
    async def renew():
        while True:
            await asyncio.sleep(25)
            try:
                await asyncio.to_thread(lambda: get_db().rpc('renew_workflow_lease',{
                    'p_tenant':user.tenant_id,'p_run':rid,'p_token':token}).execute())
            except Exception:
                log.exception('Workflow lease renewal failed: %s',rid)
                owner.cancel()
                return
    heartbeat=asyncio.create_task(renew())
    try:
        if recover:authorize_recovery(user,r)
        elif r['case_id']:authorize_case(user,r['case_id'],'write')
        ctx=Context(user,r,token)
        runtime=api.AgentRuntime(services(ctx),api.AgentConfig.from_env(),
            checkpointer=SupabaseCheckpointer(user.tenant_id,rid,token,rid),thread_id=rid)
        if recover:
            result=await runtime.recover_case(r['alert_id'],r['case_id'])
            status='completed' if isinstance(result,api.CaseDecisionResult) else 'awaiting_review'
        elif decision:
            result=await runtime.resume_case(r['case_id'],decision)
            status='completed'
        elif r['graph_name']=='case_workflow':
            result=await runtime.start_case(r['alert_id'],r['case_id'])
            status='awaiting_review'
        elif r['graph_name']=='analysis_workflow':
            result=await runtime.run_analysis(r['ingestion_run_id'],r['config']['mode'])
            status='completed'
        elif r['graph_name']=='copilot_workflow':
            result=await runtime.copilot_query(r['config']['question'],r['alert_id'])
            status='completed'
        else:raise ValueError('Unknown managed workflow')
        changes={'status':status,'result':result.model_dump(mode='json'),'error_message':None,
                 'completed_at':datetime.now(UTC).isoformat(),'lease_expires_at':None,'lease_token':None}
    except Exception:
        log.exception('Agent workflow failed: run %s',rid)
        changes={'status':'failed','error_message':'The workflow stopped. Review its saved steps before starting another run.',
            'completed_at':datetime.now(UTC).isoformat(),'lease_expires_at':None,'lease_token':None}
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat
        workflow_lease_var.reset(scope)
    with_actor(get_db().rpc('finish_workflow_run',{'p_tenant':user.tenant_id,'p_run':rid,'p_token':token,'p_changes':changes}),user).execute()
