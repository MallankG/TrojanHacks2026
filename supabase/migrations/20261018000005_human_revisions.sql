begin;
-- Human edits preserve the original draft and use the existing immutable workflow receipt format.
-- Only the backend may call this function, after deterministic evidence verification and case ABAC.
create unique index if not exists human_revision_request_key
 on public.case_narratives(tenant_id,(verification_report->>'revision_key'))
 where verification_report ? 'revision_key';

create or replace function public.save_workflow_revision(
 p_tenant uuid,p_run uuid,p_token uuid,p_actor uuid,p_key uuid,p_receipt jsonb
) returns jsonb language plpgsql security invoker set search_path='' as $human_revision$
declare
 run_row public.agent_runs;
 case_row public.cases;
 narrative_row public.case_narratives;
 item jsonb;
 version_number integer;
 attempt_number integer;
 claim_number integer:=0;
begin
 select * into run_row from public.agent_runs
  where tenant_id=p_tenant and id=p_run and managed for update;
 if not found or run_row.graph_name<>'case_workflow' then
  raise exception 'Case workflow required' using errcode='23514';
 end if;
 if not exists(select 1 from public.profiles where id=p_actor and tenant_id=p_tenant
  and role in ('admin','reviewer','investigator','analyst')) then
  raise exception 'Investigator role required' using errcode='42501';
 end if;
 select * into narrative_row from public.case_narratives
  where tenant_id=p_tenant and verification_report->>'revision_key'=p_key::text;
 if found then
  if narrative_row.agent_run_id<>p_run or narrative_row.authored_by<>p_actor then
   raise exception 'Revision key belongs to another operation' using errcode='23505';
  end if;
  return jsonb_build_object('id',narrative_row.id,'version',narrative_row.version,'verification_status',narrative_row.verification_status);
 end if;
 if run_row.status<>'running' or run_row.lease_token is distinct from p_token
  or run_row.lease_expires_at<=clock_timestamp() then
  raise exception 'Workflow lease lost' using errcode='23505';
 end if;
 select * into case_row from public.cases where tenant_id=p_tenant and id=run_row.case_id for update;
 if not found or case_row.status='closed' then
  raise exception 'Open case required' using errcode='23514';
 end if;
 if jsonb_typeof(p_receipt->'errors') is distinct from 'array' or jsonb_array_length(p_receipt->'errors')<>0
  or p_receipt->>'bundle_hash' is null then
  raise exception 'Passed server verifier receipt required' using errcode='23514';
 end if;
 perform 1 from public.case_narratives where id=(p_receipt->>'parent_id')::uuid
  and tenant_id=p_tenant and case_id=run_row.case_id and agent_run_id=p_run;
 if not found then raise exception 'Parent narrative does not belong to workflow' using errcode='23514'; end if;
 select coalesce(max(version),0)+1 into version_number from public.case_narratives
  where tenant_id=p_tenant and case_id=run_row.case_id;
 select coalesce(max(workflow_attempt),0)+1 into attempt_number from public.case_narratives where agent_run_id=p_run;
 insert into public.case_narratives(tenant_id,case_id,agent_run_id,workflow_attempt,version,parent_narrative_id,
  author_type,authored_by,content,llm_model,prompt_version,verification_status,verification_report,verified_at)
 values(p_tenant,run_row.case_id,p_run,attempt_number,version_number,(p_receipt->>'parent_id')::uuid,
  'user',p_actor,p_receipt->>'content','human-reviewed','human-edit-v1','passed',
  p_receipt||jsonb_build_object('revision_key',p_key,'source_run_id',p_run,'edited_by',p_actor),now()) returning * into narrative_row;
 for item in select value from jsonb_array_elements(p_receipt->'claims') loop
  if exists(select 1 from jsonb_array_elements_text(item->'transactions') ref where not exists(
   select 1 from public.transactions where tenant_id=p_tenant and id=ref::uuid))
   or exists(select 1 from jsonb_array_elements_text(item->'accounts') ref where not exists(
   select 1 from public.accounts where tenant_id=p_tenant and id=ref::uuid))
   or exists(select 1 from jsonb_array_elements_text(item->'citations') ref where not exists(
   select 1 from public.policy_chunks where (tenant_id is null or tenant_id=p_tenant) and id=ref::uuid)) then
   raise exception 'Reference outside tenant scope' using errcode='42501';
  end if;
  insert into public.narrative_claims(tenant_id,narrative_id,claim_index,claim_text,claim_kind,
   referenced_transaction_ids,referenced_account_ids,cited_chunk_ids,referenced_values,verification)
  values(p_tenant,narrative_row.id,claim_number,item->>'text',(item->>'kind')::public.claim_kind,
   array(select value::uuid from jsonb_array_elements_text(item->'transactions')),
   array(select value::uuid from jsonb_array_elements_text(item->'accounts')),
   array(select value::uuid from jsonb_array_elements_text(item->'citations')),item->'values','supported');
  claim_number:=claim_number+1;
 end loop;
 for item in select value from jsonb_array_elements(p_receipt->'sources') loop
  if not exists(select 1 from public.policy_chunks where id=(item->>'chunk_id')::uuid
   and (tenant_id is null or tenant_id=p_tenant)) then
   raise exception 'Policy reference outside tenant' using errcode='42501';
  end if;
  insert into public.narrative_citations(tenant_id,narrative_id,policy_chunk_id,quoted_text)
   values(p_tenant,narrative_row.id,(item->>'chunk_id')::uuid,item->>'excerpt') on conflict do nothing;
 end loop;
 update public.agent_runs set result=coalesce(result,'{}'::jsonb)||jsonb_build_object(
  'draft',p_receipt->'draft','verifier_errors','[]'::jsonb,'flagged_claims','[]'::jsonb,
  'human_revision_id',narrative_row.id,'human_revision_version',version_number),
  status='completed',current_step='human_revision',completed_at=clock_timestamp(),
  lease_token=null,lease_expires_at=null where id=p_run;
 perform private.write_audit_event(p_tenant,p_actor,'user','case.human_revision_saved','case',run_row.case_id::text,
  jsonb_build_object('run_id',p_run,'narrative_id',narrative_row.id,'revision_key',p_key));
 return jsonb_build_object('id',narrative_row.id,'version',version_number,'verification_status','passed');
end;
$human_revision$;
revoke all on function public.save_workflow_revision(uuid,uuid,uuid,uuid,uuid,jsonb) from public,anon,authenticated;
grant execute on function public.save_workflow_revision(uuid,uuid,uuid,uuid,uuid,jsonb) to service_role;
commit;
