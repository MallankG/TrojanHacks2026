-- Append-only audit trail plus triggers that record key workflow events automatically.
-- The backend writes additional events (e.g. evidence access) directly with the service role.

create table public.audit_events (
  id bigint generated always as identity primary key,
  event_id uuid not null default gen_random_uuid() unique,
  tenant_id uuid not null references public.tenants (id),
  actor_id uuid, -- deliberately no FK: audit rows must survive user deletion unchanged
  actor_type public.actor_type not null,
  action text not null check (action ~ '^[a-z_]+\.[a-z_]+$'),
  object_type text not null,
  object_id text not null,
  occurred_at timestamptz not null default now(),
  request_id text,
  metadata jsonb not null default '{}'::jsonb
);
alter table public.audit_events enable row level security;
create index audit_events_tenant_time_idx on public.audit_events (tenant_id, occurred_at desc);
create index audit_events_object_idx on public.audit_events (tenant_id, object_type, object_id);
create index audit_events_actor_idx on public.audit_events (actor_id);

create function private.prevent_audit_mutation()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'audit_events is append-only' using errcode = '42501';
end;
$$;

create trigger audit_events_immutable before update or delete on public.audit_events
  for each row execute function private.prevent_audit_mutation();

-- ---------------------------------------------------------------------------
-- Audit helpers
-- ---------------------------------------------------------------------------
create function private.write_audit_event(
  p_tenant_id uuid,
  p_actor_id uuid,
  p_actor_type public.actor_type,
  p_action text,
  p_object_type text,
  p_object_id text,
  p_metadata jsonb
)
returns void
language sql
security definer
set search_path = ''
as $$
  insert into public.audit_events (tenant_id, actor_id, actor_type, action, object_type, object_id, metadata)
  values (p_tenant_id, p_actor_id, p_actor_type, p_action, p_object_type, p_object_id, coalesce(p_metadata, '{}'::jsonb));
$$;

create function private.actor_kind(p_actor_id uuid)
returns public.actor_type
language sql
immutable
set search_path = ''
as $$
  select case when p_actor_id is null then 'system'::public.actor_type else 'user'::public.actor_type end;
$$;

-- ---------------------------------------------------------------------------
-- Table-specific audit triggers. auth.uid() is set for user JWT requests; backend
-- (service role) requests fall back to the actor column on the row.
-- ---------------------------------------------------------------------------
create function private.audit_ingestion_runs()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor uuid := coalesce((select auth.uid()), new.triggered_by);
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

create trigger ingestion_runs_audit after insert or update on public.ingestion_runs
  for each row execute function private.audit_ingestion_runs();

create function private.audit_data_quality_issues()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor uuid := coalesce((select auth.uid()), new.resolved_by);
begin
  if new.status is distinct from old.status then
    perform private.write_audit_event(new.tenant_id, v_actor, private.actor_kind(v_actor),
      'dq_issue.status_changed', 'data_quality_issue', new.id::text,
      jsonb_build_object('from', old.status, 'to', new.status, 'note', new.remediation_note));
  end if;
  return new;
end;
$$;

create trigger dq_issues_audit after update on public.data_quality_issues
  for each row execute function private.audit_data_quality_issues();

create function private.audit_model_runs()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor uuid := coalesce((select auth.uid()), new.triggered_by);
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

create trigger model_runs_audit after insert or update on public.model_runs
  for each row execute function private.audit_model_runs();

create function private.audit_alerts()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor uuid := (select auth.uid());
begin
  if new.status is distinct from old.status then
    perform private.write_audit_event(new.tenant_id, v_actor, private.actor_kind(v_actor),
      'alert.status_changed', 'alert', new.id::text,
      jsonb_build_object('from', old.status, 'to', new.status));
  end if;
  return new;
end;
$$;

create trigger alerts_audit after update on public.alerts
  for each row execute function private.audit_alerts();

create function private.audit_cases()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_uid uuid := (select auth.uid());
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

create trigger cases_audit after insert or update on public.cases
  for each row execute function private.audit_cases();

create function private.audit_case_narratives()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_actor uuid := coalesce((select auth.uid()), new.authored_by);
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
    perform private.write_audit_event(new.tenant_id, (select auth.uid()), 'system'::public.actor_type,
      'narrative.verification_completed', 'case_narrative', new.id::text,
      jsonb_build_object('case_id', new.case_id, 'from', old.verification_status, 'to', new.verification_status));
  end if;
  return new;
end;
$$;

create trigger case_narratives_audit after insert or update on public.case_narratives
  for each row execute function private.audit_case_narratives();

create function private.audit_case_decisions()
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

create trigger case_decisions_audit after insert on public.case_decisions
  for each row execute function private.audit_case_decisions();
