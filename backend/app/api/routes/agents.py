"""Authenticated, persisted multi-agent jobs. Browser callers never choose actors or services."""
from typing import Literal
from uuid import UUID

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
