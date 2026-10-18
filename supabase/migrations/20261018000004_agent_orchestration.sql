begin;
-- Durable, tenant-scoped LangGraph runs. No checkpoint is readable from a browser.
alter table public.agent_runs alter column case_id drop not null;
alter table public.agent_runs
  add column ingestion_run_id uuid,
  add column managed boolean not null default false,
  add column idempotency_key uuid,
  add column result jsonb,
  add column lease_token uuid,
  add column lease_expires_at timestamptz,
  add column revision integer not null default 0,
  add foreign key (tenant_id,ingestion_run_id) references public.ingestion_runs(tenant_id,id),
  add check (case_id is not null or ingestion_run_id is not null);
create unique index managed_run_idempotency on public.agent_runs(tenant_id,idempotency_key) where managed;
create unique index managed_case_active on public.agent_runs(tenant_id,case_id,graph_name)
 where managed and case_id is not null and status in ('queued','running','awaiting_review');
create table public.agent_checkpoints (
 tenant_id uuid not null, run_id uuid not null, namespace text not null default '',
 checkpoint_id text not null, parent_id text, checkpoint jsonb not null, metadata jsonb not null,
 lease_token uuid not null, created_at timestamptz not null default now(),
 primary key(run_id,namespace,checkpoint_id),
 foreign key(tenant_id,run_id) references public.agent_runs(tenant_id,id),
 check(pg_column_size(checkpoint)<4194304)
);
create table public.agent_checkpoint_writes (
 tenant_id uuid not null, run_id uuid not null, namespace text not null default '',
 checkpoint_id text not null, task_id text not null, write_index integer not null,
 channel text not null, value jsonb not null, lease_token uuid not null,
 primary key(run_id,namespace,checkpoint_id,task_id,write_index),
 foreign key(tenant_id,run_id) references public.agent_runs(tenant_id,id)
);
create table public.agent_chain_receipts (
 sequence bigint generated always as identity primary key,
 tenant_id uuid not null, run_id uuid not null, event_id text not null,
 prev_hash text, hash text not null check(hash ~ '^[0-9a-f]{64}$'), event jsonb not null,
 created_at timestamptz not null default now(), unique(run_id,event_id),
 foreign key(tenant_id,run_id) references public.agent_runs(tenant_id,id)
);
create table public.agent_evidence_bundles (
 tenant_id uuid not null,run_id uuid not null,alert_id uuid not null,bundle jsonb not null,
 primary key(run_id,alert_id),
 foreign key(tenant_id,run_id) references public.agent_runs(tenant_id,id),
 foreign key(tenant_id,alert_id) references public.alerts(tenant_id,id)
);
do $$ declare t text; begin
 foreach t in array array['agent_checkpoints','agent_checkpoint_writes','agent_chain_receipts','agent_evidence_bundles'] loop
  execute format('alter table public.%I enable row level security',t);
  execute format('revoke all on public.%I from public,anon,authenticated',t);
  execute format('grant all on public.%I to service_role',t);
 end loop;
end $$;
grant usage,select on sequence public.agent_chain_receipts_sequence_seq to service_role;
create trigger agent_chain_immutable before update or delete on public.agent_chain_receipts
 for each row execute function private.prevent_audit_mutation();
create function private.validate_checkpoint_lease() returns trigger language plpgsql set search_path='' as $$
begin
 perform 1 from public.agent_runs where id=new.run_id and tenant_id=new.tenant_id
   and managed and lease_token=new.lease_token and lease_expires_at>clock_timestamp() and status='running' for share;
 if not found then
  raise exception 'Workflow lease is no longer active' using errcode='23505';
 end if;
 return new;
end $$;
create trigger checkpoint_active_lease before insert or update on public.agent_checkpoints
 for each row execute function private.validate_checkpoint_lease();
create trigger checkpoint_writes_active_lease before insert or update on public.agent_checkpoint_writes
 for each row execute function private.validate_checkpoint_lease();
create function public.claim_workflow_run(p_tenant uuid,p_run uuid,p_token uuid,p_resume boolean)
 returns jsonb language plpgsql security invoker set search_path='' as $$
declare r public.agent_runs; begin
 select * into r from public.agent_runs where tenant_id=p_tenant and id=p_run and managed for update;
 if not found then raise exception 'Workflow not found' using errcode='23503'; end if;
 if r.status='running' and r.lease_expires_at>clock_timestamp() then
  raise exception 'Workflow is already running' using errcode='23505';
 end if;
 if (p_resume and r.status not in ('awaiting_review','running','failed'))
   or (not p_resume and r.status not in ('queued','running')) then
  raise exception 'Workflow cannot run in its current state' using errcode='23505';
 end if;
 update public.agent_runs set status='running',lease_token=p_token,
  lease_expires_at=clock_timestamp()+interval '2 minutes',revision=revision+1,
  started_at=coalesce(started_at,now()),completed_at=null where id=p_run returning * into r;
 return to_jsonb(r);
end $$;
create function public.append_workflow_step(p_tenant uuid,p_run uuid,p_token uuid,p_step jsonb)
 returns void language plpgsql security invoker set search_path='' as $$ begin
 update public.agent_runs set steps=steps||jsonb_build_array(p_step),current_step=p_step->>'node'
 where tenant_id=p_tenant and id=p_run and managed and lease_token=p_token and lease_expires_at>clock_timestamp() and status='running';
 if not found then raise exception 'Workflow lease lost' using errcode='23505'; end if;
end $$;
create function public.append_agent_receipt(p_tenant uuid,p_run uuid,p_token uuid,p_event jsonb)
 returns jsonb language plpgsql security definer set search_path='' as $$
declare last_hash text; saved public.agent_chain_receipts; r public.agent_runs; begin
 select * into r from public.agent_runs where tenant_id=p_tenant and id=p_run and managed for update;
 if not found or r.lease_token is distinct from p_token or r.lease_expires_at<=clock_timestamp() or r.status<>'running' then
  raise exception 'Workflow lease lost' using errcode='23505';
 end if;
 select * into saved from public.agent_chain_receipts where run_id=p_run and event_id=p_event->>'event_id';
 if found then
  if saved.event - 'prev_hash' - 'hash' is distinct from p_event - 'prev_hash' - 'hash' then
   raise exception 'Audit event id reused' using errcode='23505'; end if;
  return saved.event;
 end if;
 select hash into last_hash from public.agent_chain_receipts where run_id=p_run order by sequence desc limit 1;
 if last_hash is distinct from p_event->>'prev_hash' then raise exception 'Audit chain changed' using errcode='23505'; end if;
 insert into public.agent_chain_receipts(tenant_id,run_id,event_id,prev_hash,hash,event)
 values(p_tenant,p_run,p_event->>'event_id',last_hash,p_event->>'hash',p_event);
 perform private.write_audit_event(p_tenant,r.triggered_by,'user',p_event->>'action','agent_run',p_run::text,
  jsonb_build_object('agent_event_id',p_event->>'event_id','agent_hash',p_event->>'hash','delegated_actor',r.triggered_by));
 return p_event;
end $$;
alter table public.case_narratives add column workflow_attempt integer;
create unique index workflow_draft_attempt on public.case_narratives(agent_run_id,workflow_attempt)
 where workflow_attempt is not null;
alter table public.case_decisions add column agent_run_id uuid,
 add foreign key (tenant_id,agent_run_id) references public.agent_runs(tenant_id,id);
create unique index workflow_single_decision on public.case_decisions(agent_run_id) where agent_run_id is not null;
create function public.save_workflow_draft(p_tenant uuid,p_run uuid,p_token uuid,p_receipt jsonb)
 returns jsonb language plpgsql security definer set search_path='' as $$
declare r public.agent_runs; n public.case_narratives; item jsonb; idx integer:=0; ver integer;
begin
 select * into r from public.agent_runs where tenant_id=p_tenant and id=p_run and managed for update;
 if not found or r.lease_token is distinct from p_token or r.lease_expires_at<=clock_timestamp() or r.status<>'running' then
  raise exception 'Workflow lease lost' using errcode='23505'; end if;
 perform 1 from public.cases where id=r.case_id and tenant_id=p_tenant and status<>'closed' for update;
 if not found then raise exception 'Open case required' using errcode='23514'; end if;
 select * into n from public.case_narratives where agent_run_id=p_run and workflow_attempt=(p_receipt->>'attempt')::integer;
 if found then
  if n.verification_report is distinct from p_receipt then raise exception 'Draft attempt already saved with a different receipt' using errcode='23505'; end if;
  return to_jsonb(n);
 end if;
 select coalesce(max(version),0)+1 into ver from public.case_narratives where tenant_id=p_tenant and case_id=r.case_id;
 insert into public.case_narratives(tenant_id,case_id,agent_run_id,workflow_attempt,version,author_type,authored_by,
  content,llm_provider,llm_model,prompt_version,verification_status,verification_report,verified_at)
 values(p_tenant,r.case_id,p_run,(p_receipt->>'attempt')::integer,ver,'agent',r.triggered_by,
  p_receipt->>'content',p_receipt->>'provider',p_receipt->>'model',p_receipt->>'prompt_version',
  case when jsonb_array_length(p_receipt->'errors')=0 then 'passed'::public.verification_status else 'failed'::public.verification_status end,
  p_receipt,now()) returning * into n;
 for item in select value from jsonb_array_elements(p_receipt->'claims') loop
  if exists(select 1 from jsonb_array_elements_text(item->'transactions') ref
    where not exists(select 1 from public.transactions t where t.id=ref::uuid and t.tenant_id=p_tenant))
   or exists(select 1 from jsonb_array_elements_text(item->'accounts') ref
    where not exists(select 1 from public.accounts a where a.id=ref::uuid and a.tenant_id=p_tenant))
   or exists(select 1 from jsonb_array_elements_text(item->'citations') ref
    where not exists(select 1 from public.policy_chunks pc where pc.id=ref::uuid and (pc.tenant_id is null or pc.tenant_id=p_tenant))) then
   raise exception 'Claim reference outside tenant scope' using errcode='42501'; end if;
  insert into public.narrative_claims(tenant_id,narrative_id,claim_index,claim_text,claim_kind,
   referenced_transaction_ids,referenced_account_ids,cited_chunk_ids,referenced_values,verification,verifier_notes)
  values(p_tenant,n.id,idx,item->>'text',(item->>'kind')::public.claim_kind,
   array(select value::uuid from jsonb_array_elements_text(item->'transactions')),
   array(select value::uuid from jsonb_array_elements_text(item->'accounts')),
   array(select value::uuid from jsonb_array_elements_text(item->'citations')),item->'values',
   (item->>'verification')::public.claim_verification,item->>'notes');
  idx:=idx+1;
 end loop;
 for item in select value from jsonb_array_elements(p_receipt->'sources') loop
  if not exists(select 1 from public.policy_chunks where id=(item->>'chunk_id')::uuid and (tenant_id is null or tenant_id=p_tenant)) then
   raise exception 'Policy citation outside tenant scope' using errcode='42501'; end if;
  insert into public.narrative_citations(tenant_id,narrative_id,policy_chunk_id,quoted_text)
   values(p_tenant,n.id,(item->>'chunk_id')::uuid,item->>'excerpt') on conflict do nothing;
 end loop;
 update public.cases set status='in_review' where id=r.case_id and tenant_id=p_tenant;
 perform private.write_audit_event(p_tenant,r.triggered_by,'user','case.agent_draft_saved','case',r.case_id::text,
  jsonb_build_object('run_id',p_run,'narrative_id',n.id,'verification',n.verification_status));
 return to_jsonb(n);
end $$;
create function public.record_workflow_decision(p_tenant uuid,p_run uuid,p_token uuid,p_actor uuid,p_action text,p_reason text)
 returns jsonb language plpgsql security definer set search_path='' as $$
declare r public.agent_runs; c public.cases; n public.case_narratives; d public.case_decisions; actor_role text; receipt jsonb;
begin
 select * into r from public.agent_runs where id=p_run and tenant_id=p_tenant and managed for update;
 if not found or r.lease_token is distinct from p_token or r.lease_expires_at<=clock_timestamp() or r.status<>'running' then
  raise exception 'Workflow lease lost' using errcode='23505'; end if;
 select * into d from public.case_decisions where agent_run_id=p_run;
 if found then
  if d.decided_by<>p_actor or d.notes<>p_reason or d.decision::text<>case p_action when 'escalate' then 'escalate' when 'dismiss' then 'reject' else 'edit' end then raise exception 'Decision already recorded' using errcode='23505'; end if;
  return r.result->'_decision_receipt';
 end if;
 select * into c from public.cases where id=r.case_id and tenant_id=p_tenant for update;
 select role::text into actor_role from public.profiles where id=p_actor and tenant_id=p_tenant;
 if c.status='closed' or actor_role not in ('admin','reviewer','analyst','investigator') or length(btrim(p_reason))<10
  or p_action not in ('escalate','dismiss','request_more_data') then
  raise exception 'Decision is not permitted' using errcode='42501'; end if;
 if p_action in ('escalate','dismiss') and actor_role not in ('admin','reviewer') then
  raise exception 'Reviewer required' using errcode='42501'; end if;
 if p_action='escalate' and c.escalation_requested_by=p_actor then
  raise exception 'A different reviewer must decide an escalation' using errcode='42501'; end if;
 select * into n from public.case_narratives where agent_run_id=p_run order by version desc limit 1;
 if not found then raise exception 'Saved draft required' using errcode='23514'; end if;
 if p_action in ('escalate','dismiss') and n.verification_status<>'passed' then
  raise exception 'Verified draft required for final disposition' using errcode='23514'; end if;
 insert into public.case_decisions(tenant_id,case_id,narrative_id,agent_run_id,decision,disposition,decided_by,notes)
 values(p_tenant,c.id,n.id,p_run,
  case p_action when 'escalate' then 'escalate'::public.review_decision when 'dismiss' then 'reject'::public.review_decision else 'edit'::public.review_decision end,
  case p_action when 'escalate' then 'escalate_for_sar_consideration'::public.case_disposition when 'dismiss' then 'false_positive'::public.case_disposition else null end,
  p_actor,p_reason) returning * into d;
 receipt:=jsonb_build_object('decision',to_jsonb(d),'before',to_jsonb(c),
  'after',(select to_jsonb(current_case) from public.cases current_case where id=c.id and tenant_id=p_tenant));
 update public.agent_runs set result=coalesce(result,'{}'::jsonb)||jsonb_build_object('_decision_receipt',receipt) where id=p_run and tenant_id=p_tenant;
 return receipt;
end $$;
-- Side effects from a managed detector must hold the same lease as its graph checkpoint.
create function private.validate_agent_effect() returns trigger language plpgsql set search_path='' as $$
declare rid uuid; token uuid; hdr jsonb; mid uuid;
begin
 if tg_table_name='alerts' and tg_op='UPDATE' then
  if (to_jsonb(new)-'status'-'updated_at')=(to_jsonb(old)-'status'-'updated_at') then return new; end if;
 end if;
 if tg_table_name='model_runs' then rid:=(new.config->>'agent_run_id')::uuid;
 elsif tg_table_name='agent_evidence_bundles' then rid:=new.run_id;
 else
  if tg_table_name in ('alerts','risk_signals') then mid:=new.model_run_id;
  else select a.model_run_id into mid from public.alerts a where a.id=new.alert_id and a.tenant_id=new.tenant_id;
  end if;
  select (m.config->>'agent_run_id')::uuid into rid from public.model_runs m where m.id=mid and m.tenant_id=new.tenant_id;
 end if;
 if rid is null then return new; end if;
 hdr:=coalesce(nullif(current_setting('request.headers',true),''),'{}')::jsonb;
 token:=(hdr->>'x-workflow-token')::uuid;
 if (hdr->>'x-workflow-run') is distinct from rid::text then
  raise exception 'Managed model writes require their workflow lease' using errcode='42501'; end if;
 perform 1 from public.agent_runs where tenant_id=new.tenant_id and id=rid and managed
  and status='running' and lease_token=token and lease_expires_at>clock_timestamp() for share;
 if not found then raise exception 'Workflow lease lost' using errcode='23505'; end if;
 return new;
end $$;
do $$ declare t text; begin
 foreach t in array array['model_runs','risk_signals','alerts','alert_transactions','alert_accounts','alert_signals','agent_evidence_bundles'] loop
  execute format('create trigger agent_effect_lease before insert or update on public.%I for each row execute function private.validate_agent_effect()',t);
 end loop;
end $$;
create function public.renew_workflow_lease(p_tenant uuid,p_run uuid,p_token uuid)
 returns void language plpgsql security invoker set search_path='' as $$ begin
 update public.agent_runs set lease_expires_at=clock_timestamp()+interval '2 minutes'
 where tenant_id=p_tenant and id=p_run and managed and status='running' and lease_token=p_token and lease_expires_at>clock_timestamp();
 if not found then raise exception 'Workflow lease lost' using errcode='23505'; end if;
end $$;
create function public.finish_workflow_run(p_tenant uuid,p_run uuid,p_token uuid,p_changes jsonb)
 returns void language plpgsql security invoker set search_path='' as $$ begin
 update public.agent_runs set status=(p_changes->>'status')::public.agent_run_status,
 result=coalesce(result,'{}'::jsonb)||coalesce(p_changes->'result','{}'::jsonb),error_message=p_changes->>'error_message',completed_at=clock_timestamp(),
 lease_token=null,lease_expires_at=null
 where tenant_id=p_tenant and id=p_run and managed and status='running' and lease_token=p_token and lease_expires_at>clock_timestamp();
 if not found then raise exception 'Workflow lease lost' using errcode='23505'; end if;
end $$;
do $$ declare f record; begin
 for f in select p.oid::regprocedure as signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname='public' and p.proname in ('claim_workflow_run','append_workflow_step','append_agent_receipt','save_workflow_draft','record_workflow_decision','renew_workflow_lease','finish_workflow_run') loop
  execute format('revoke all on function %s from public,anon,authenticated',f.signature);
  execute format('grant execute on function %s to service_role',f.signature);
 end loop;
end $$;

commit;
