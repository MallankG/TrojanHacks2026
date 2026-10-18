-- Attribute trigger-written audit events to the real caller.
-- Browser requests carry a user JWT (auth.uid()). The FastAPI backend writes with the service role and
-- forwards the acting user in the X-Actor-Id / X-Actor-Type request headers. Only the service role can
-- write these tables, so browser clients cannot forge these headers.

create or replace function private.request_header(p_name text)
returns text
language sql
stable
set search_path = ''
as $$
  select nullif(current_setting('request.headers', true), '')::json ->> p_name;
$$;

create or replace function private.current_actor()
returns uuid
language plpgsql
stable
set search_path = ''
as $$
declare
  v_header text;
begin
  if (select auth.uid()) is not null then
    return (select auth.uid());
  end if;
  v_header := private.request_header('x-actor-id');
  if v_header is null then
    return null;
  end if;
  return v_header::uuid;
exception when invalid_text_representation then
  return null;
end;
$$;

-- Without a user: 'agent' when the backend marks the caller as an agent/worker, otherwise 'system'.
create or replace function private.actor_kind(p_actor_id uuid)
returns public.actor_type
language sql
stable
set search_path = ''
as $$
  select case
    when p_actor_id is not null then 'user'::public.actor_type
    when private.request_header('x-actor-type') = 'agent' then 'agent'::public.actor_type
    else 'system'::public.actor_type
  end;
$$;

create or replace function private.audit_ingestion_runs()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor uuid := coalesce(private.current_actor(), new.triggered_by);
begin
  if tg_op = 'INSERT' then
    perform private.write_audit_event(new.tenant_id, v_actor, private.actor_kind(v_actor),
      'ingestion.run_started', 'ingestion_run', new.id::text,
      jsonb_build_object('file_id', new.file_id, 'schema_version', new.schema_version));
  elsif new.status is distinct from old.status then
    perform private.write_audit_event(new.tenant_id, v_actor, private.actor_kind(v_actor),
      'ingestion.run_status_changed', 'ingestion_run', new.id::text,
      jsonb_build_object('from', old.status, 'to', new.status, 'row_count', new.row_count,
        'accepted_count', new.accepted_count, 'rejected_count', new.rejected_count));
  end if;
  return new;
end;
$$;

create or replace function private.audit_data_quality_issues()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor uuid := coalesce(private.current_actor(), new.resolved_by);
begin
  if new.status is distinct from old.status then
    perform private.write_audit_event(new.tenant_id, v_actor, private.actor_kind(v_actor),
      'dq_issue.status_changed', 'data_quality_issue', new.id::text,
      jsonb_build_object('from', old.status, 'to', new.status, 'note', new.remediation_note));
  end if;
  return new;
end;
$$;

create or replace function private.audit_model_runs()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor uuid := coalesce(private.current_actor(), new.triggered_by);
begin
  if tg_op = 'INSERT' then
    perform private.write_audit_event(new.tenant_id, v_actor, private.actor_kind(v_actor),
      'model_run.started', 'model_run', new.id::text,
      jsonb_build_object('model_name', new.model_name, 'model_version', new.model_version,
        'model_kind', new.model_kind, 'dataset_version', new.dataset_version));
  elsif new.status is distinct from old.status then
    perform private.write_audit_event(new.tenant_id, v_actor, private.actor_kind(v_actor),
      'model_run.status_changed', 'model_run', new.id::text,
      jsonb_build_object('from', old.status, 'to', new.status));
  end if;
  return new;
end;
$$;

create or replace function private.audit_alerts()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor uuid := private.current_actor();
begin
  if new.status is distinct from old.status then
    perform private.write_audit_event(new.tenant_id, v_actor, private.actor_kind(v_actor),
      'alert.status_changed', 'alert', new.id::text,
      jsonb_build_object('from', old.status, 'to', new.status));
  end if;
  return new;
end;
$$;

create or replace function private.audit_cases()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := private.current_actor();
  v_actor uuid;
begin
  if tg_op = 'INSERT' then
    v_actor := coalesce(v_uid, new.created_by);
    perform private.write_audit_event(new.tenant_id, v_actor, private.actor_kind(v_actor),
      'case.created', 'case', new.id::text,
      jsonb_build_object('title', new.title, 'status', new.status));
    return new;
  end if;

  if new.status is distinct from old.status then
    v_actor := coalesce(v_uid, new.disposed_by);
    perform private.write_audit_event(new.tenant_id, v_actor, private.actor_kind(v_actor),
      'case.status_changed', 'case', new.id::text,
      jsonb_build_object('from', old.status, 'to', new.status));
  end if;

  if new.assigned_to is distinct from old.assigned_to then
    perform private.write_audit_event(new.tenant_id, v_uid, private.actor_kind(v_uid),
      'case.assigned', 'case', new.id::text,
      jsonb_build_object('from', old.assigned_to, 'to', new.assigned_to));
  end if;

  if new.disposition is distinct from old.disposition then
    v_actor := coalesce(v_uid, new.disposed_by);
    perform private.write_audit_event(new.tenant_id, v_actor, private.actor_kind(v_actor),
      'case.disposition_recorded', 'case', new.id::text,
      jsonb_build_object('from', old.disposition, 'to', new.disposition, 'note', new.disposition_note));
  end if;
  return new;
end;
$$;

create or replace function private.audit_case_narratives()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor uuid := coalesce(private.current_actor(), new.authored_by);
  v_actor_type public.actor_type := case
    when new.author_type = 'agent' then 'agent'::public.actor_type
    else private.actor_kind(v_actor)
  end;
begin
  if tg_op = 'INSERT' then
    perform private.write_audit_event(new.tenant_id, v_actor, v_actor_type,
      'narrative.version_created', 'case_narrative', new.id::text,
      jsonb_build_object('case_id', new.case_id, 'version', new.version,
        'agent_run_id', new.agent_run_id, 'prompt_version', new.prompt_version, 'llm_model', new.llm_model));
  elsif new.verification_status is distinct from old.verification_status then
    perform private.write_audit_event(new.tenant_id, private.current_actor(), private.actor_kind(private.current_actor()),
      'narrative.verification_completed', 'case_narrative', new.id::text,
      jsonb_build_object('case_id', new.case_id, 'from', old.verification_status, 'to', new.verification_status));
  end if;
  return new;
end;
$$;

create or replace function private.audit_case_decisions()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform private.write_audit_event(new.tenant_id, new.decided_by, 'user'::public.actor_type,
    'case.review_decision', 'case_decision', new.id::text,
    jsonb_build_object('case_id', new.case_id, 'decision', new.decision,
      'disposition', new.disposition, 'narrative_id', new.narrative_id));
  return new;
end;
$$;
