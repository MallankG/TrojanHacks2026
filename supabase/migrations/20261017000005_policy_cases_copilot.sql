-- Policy RAG corpus, cases, evidence, agent runs, narratives (with verifier output) and reviewer decisions.

-- Policy corpus. tenant_id null = shared public guidance (FFIEC, FinCEN) visible to every tenant.
create table public.policy_documents (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references public.tenants (id) on delete cascade,
  title text not null,
  publisher text not null,
  source_url text not null check (source_url ~ '^https://'),
  document_type text,
  published_date date,
  effective_date date,
  retrieved_at timestamptz not null,
  content_checksum text,
  created_at timestamptz not null default now(),
  unique nulls not distinct (tenant_id, source_url)
);
alter table public.policy_documents enable row level security;

-- Embedding dimension 768 matches nomic-embed-text.
create table public.policy_chunks (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid references public.tenants (id) on delete cascade,
  document_id uuid not null references public.policy_documents (id) on delete cascade,
  chunk_index integer not null check (chunk_index >= 0),
  section text,
  content text not null,
  token_count integer check (token_count >= 0),
  embedding extensions.vector(768),
  embedding_model text,
  fts tsvector generated always as (to_tsvector('english'::regconfig, coalesce(section, '') || ' ' || content)) stored,
  indexed_at timestamptz not null default now(),
  unique (document_id, chunk_index),
  check ((embedding is null) = (embedding_model is null))
);
alter table public.policy_chunks enable row level security;
create index policy_chunks_tenant_idx on public.policy_chunks (tenant_id);
create index policy_chunks_embedding_idx on public.policy_chunks
  using hnsw (embedding extensions.vector_cosine_ops);
create index policy_chunks_fts_idx on public.policy_chunks using gin (fts);

-- ---------------------------------------------------------------------------
-- Cases
-- ---------------------------------------------------------------------------
create table public.cases (
  id uuid primary key default gen_random_uuid(),
  case_number bigint generated always as identity unique,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  title text not null,
  summary text,
  status public.case_status not null default 'open',
  priority_band public.priority_band not null default 'medium',
  assigned_to uuid references public.profiles (id) on delete set null,
  disposition public.case_disposition,
  disposition_note text,
  disposed_by uuid references public.profiles (id),
  disposed_at timestamptz,
  created_by uuid references public.profiles (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, id),
  check (
    (disposition is null and disposed_by is null and disposed_at is null)
    or (disposition is not null and disposed_by is not null and disposed_at is not null)
  )
);
alter table public.cases enable row level security;
create index cases_status_idx on public.cases (tenant_id, status, priority_band);
create index cases_assigned_to_idx on public.cases (assigned_to);
create index cases_disposed_by_idx on public.cases (disposed_by);
create index cases_created_by_idx on public.cases (created_by);
create trigger cases_set_updated_at before update on public.cases
  for each row execute function private.set_updated_at();

create table public.case_alerts (
  case_id uuid not null,
  alert_id uuid not null,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  added_by uuid references public.profiles (id) on delete set null,
  added_at timestamptz not null default now(),
  primary key (case_id, alert_id),
  foreign key (tenant_id, case_id) references public.cases (tenant_id, id) on delete cascade,
  foreign key (tenant_id, alert_id) references public.alerts (tenant_id, id)
);
alter table public.case_alerts enable row level security;
create index case_alerts_case_idx on public.case_alerts (tenant_id, case_id);
create index case_alerts_alert_idx on public.case_alerts (tenant_id, alert_id);
create index case_alerts_added_by_idx on public.case_alerts (added_by);

create table public.case_evidence (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  case_id uuid not null,
  evidence_type public.evidence_type not null,
  source_ref text,
  transaction_ids uuid[] not null default '{}',
  alert_id uuid,
  account_id uuid,
  risk_signal_id uuid,
  dq_issue_id uuid,
  policy_chunk_id uuid references public.policy_chunks (id),
  explanation text not null,
  payload jsonb not null default '{}'::jsonb,
  added_by uuid references public.profiles (id) on delete set null,
  added_by_type public.actor_type not null default 'user',
  created_at timestamptz not null default now(),
  unique (tenant_id, id),
  foreign key (tenant_id, case_id) references public.cases (tenant_id, id) on delete cascade,
  foreign key (tenant_id, alert_id) references public.alerts (tenant_id, id),
  foreign key (tenant_id, account_id) references public.accounts (tenant_id, id),
  foreign key (tenant_id, risk_signal_id) references public.risk_signals (tenant_id, id),
  foreign key (tenant_id, dq_issue_id) references public.data_quality_issues (tenant_id, id),
  check (evidence_type not in ('transaction', 'transaction_path') or cardinality(transaction_ids) > 0),
  check (evidence_type <> 'alert' or alert_id is not null),
  check (evidence_type <> 'account' or account_id is not null),
  check (evidence_type <> 'risk_signal' or risk_signal_id is not null),
  check (evidence_type <> 'data_quality_issue' or dq_issue_id is not null),
  check (evidence_type <> 'policy_citation' or policy_chunk_id is not null)
);
alter table public.case_evidence enable row level security;
create index case_evidence_case_idx on public.case_evidence (tenant_id, case_id);
create index case_evidence_alert_idx on public.case_evidence (tenant_id, alert_id);
create index case_evidence_account_idx on public.case_evidence (tenant_id, account_id);
create index case_evidence_signal_idx on public.case_evidence (tenant_id, risk_signal_id);
create index case_evidence_dq_issue_idx on public.case_evidence (tenant_id, dq_issue_id);
create index case_evidence_policy_chunk_idx on public.case_evidence (policy_chunk_id);
create index case_evidence_added_by_idx on public.case_evidence (added_by);
create index case_evidence_txn_ids_idx on public.case_evidence using gin (transaction_ids);

-- LangGraph orchestration runs; provider/model/prompt versions are kept for reproducibility.
create table public.agent_runs (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  case_id uuid not null,
  alert_id uuid,
  graph_name text not null,
  graph_version text,
  status public.agent_run_status not null default 'queued',
  current_step text,
  steps jsonb not null default '[]'::jsonb,
  llm_provider text,
  llm_model text,
  embedding_model text,
  prompt_version text,
  config jsonb not null default '{}'::jsonb,
  error_message text,
  triggered_by uuid references public.profiles (id) on delete set null,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  unique (tenant_id, id),
  foreign key (tenant_id, case_id) references public.cases (tenant_id, id) on delete cascade,
  foreign key (tenant_id, alert_id) references public.alerts (tenant_id, id) on delete set null (alert_id),
  check (completed_at is null or started_at is null or completed_at >= started_at)
);
alter table public.agent_runs enable row level security;
create index agent_runs_case_idx on public.agent_runs (tenant_id, case_id, created_at desc);
create index agent_runs_alert_idx on public.agent_runs (tenant_id, alert_id);
create index agent_runs_triggered_by_idx on public.agent_runs (triggered_by);

-- Narrative versions are immutable drafts; edits create a new version pointing at its parent.
create table public.case_narratives (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  case_id uuid not null,
  version integer not null check (version > 0),
  parent_narrative_id uuid,
  agent_run_id uuid,
  author_type public.actor_type not null,
  authored_by uuid references public.profiles (id),
  content text not null,
  llm_provider text,
  llm_model text,
  prompt_version text,
  verification_status public.verification_status not null default 'pending',
  verification_report jsonb not null default '{}'::jsonb,
  verified_at timestamptz,
  created_at timestamptz not null default now(),
  unique (tenant_id, id),
  unique (case_id, version),
  foreign key (tenant_id, case_id) references public.cases (tenant_id, id) on delete cascade,
  foreign key (tenant_id, parent_narrative_id) references public.case_narratives (tenant_id, id)
    on delete set null (parent_narrative_id),
  foreign key (tenant_id, agent_run_id) references public.agent_runs (tenant_id, id)
    on delete set null (agent_run_id),
  check (author_type <> 'user' or authored_by is not null),
  check (author_type <> 'agent' or llm_model is not null)
);
alter table public.case_narratives enable row level security;
create index case_narratives_case_idx on public.case_narratives (tenant_id, case_id);
create index case_narratives_parent_idx on public.case_narratives (tenant_id, parent_narrative_id);
create index case_narratives_agent_run_idx on public.case_narratives (tenant_id, agent_run_id);
create index case_narratives_authored_by_idx on public.case_narratives (authored_by);

-- Verifier output per claim: facts must reference stored evidence; hypotheses/unknowns are labelled.
create table public.narrative_claims (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  narrative_id uuid not null,
  claim_index integer not null check (claim_index >= 0),
  claim_text text not null,
  claim_kind public.claim_kind not null,
  referenced_transaction_ids uuid[] not null default '{}',
  referenced_account_ids uuid[] not null default '{}',
  referenced_values jsonb not null default '{}'::jsonb,
  cited_chunk_ids uuid[] not null default '{}',
  verification public.claim_verification not null default 'pending',
  verifier_notes text,
  created_at timestamptz not null default now(),
  unique (narrative_id, claim_index),
  foreign key (tenant_id, narrative_id) references public.case_narratives (tenant_id, id) on delete cascade
);
alter table public.narrative_claims enable row level security;
create index narrative_claims_narrative_idx on public.narrative_claims (tenant_id, narrative_id);

create table public.narrative_citations (
  narrative_id uuid not null,
  policy_chunk_id uuid not null references public.policy_chunks (id),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  quoted_text text,
  created_at timestamptz not null default now(),
  primary key (narrative_id, policy_chunk_id),
  foreign key (tenant_id, narrative_id) references public.case_narratives (tenant_id, id) on delete cascade
);
alter table public.narrative_citations enable row level security;
create index narrative_citations_narrative_idx on public.narrative_citations (tenant_id, narrative_id);
create index narrative_citations_chunk_idx on public.narrative_citations (policy_chunk_id);

-- Human review decisions. The case's disposition is derived from these (see trigger below).
create table public.case_decisions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  case_id uuid not null,
  narrative_id uuid,
  decision public.review_decision not null,
  disposition public.case_disposition,
  notes text,
  decided_by uuid not null references public.profiles (id),
  decided_at timestamptz not null default now(),
  unique (tenant_id, id),
  foreign key (tenant_id, case_id) references public.cases (tenant_id, id) on delete cascade,
  foreign key (tenant_id, narrative_id) references public.case_narratives (tenant_id, id)
);
alter table public.case_decisions enable row level security;
create index case_decisions_case_idx on public.case_decisions (tenant_id, case_id, decided_at desc);
create index case_decisions_narrative_idx on public.case_decisions (tenant_id, narrative_id);
create index case_decisions_decided_by_idx on public.case_decisions (decided_by);

-- ---------------------------------------------------------------------------
-- Workflow integrity triggers
-- ---------------------------------------------------------------------------

-- Only reviewers/admins of the same tenant can record a disposition on a case.
create function private.enforce_case_disposition()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role public.app_role;
  v_tenant uuid;
begin
  if new.disposition is not null and new.disposition is distinct from old.disposition then
    select p.role, p.tenant_id into v_role, v_tenant
    from public.profiles p where p.id = new.disposed_by;

    if v_tenant is distinct from new.tenant_id or v_role is null or v_role not in ('reviewer', 'admin') then
      raise exception 'Only a reviewer or admin of this tenant can record a case disposition'
        using errcode = '42501';
    end if;
    new.disposed_at = coalesce(new.disposed_at, now());
  end if;
  return new;
end;
$$;

create trigger cases_enforce_disposition before update on public.cases
  for each row execute function private.enforce_case_disposition();

-- Accept/reject/escalate and any disposition require a reviewer/admin of the same tenant.
create function private.enforce_case_decision()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_role public.app_role;
  v_tenant uuid;
begin
  select p.role, p.tenant_id into v_role, v_tenant
  from public.profiles p where p.id = new.decided_by;

  if v_tenant is distinct from new.tenant_id then
    raise exception 'Decision maker does not belong to this tenant' using errcode = '42501';
  end if;

  if (new.decision <> 'edit' or new.disposition is not null)
     and (v_role is null or v_role not in ('reviewer', 'admin')) then
    raise exception 'Only a reviewer or admin can record a % decision', new.decision
      using errcode = '42501';
  end if;
  return new;
end;
$$;

create trigger case_decisions_enforce before insert on public.case_decisions
  for each row execute function private.enforce_case_decision();

-- Apply a decision to its case so cases.disposition always mirrors the latest reviewer decision.
create function private.apply_case_decision()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if new.disposition is not null then
    update public.cases
    set disposition = new.disposition,
        disposition_note = new.notes,
        disposed_by = new.decided_by,
        disposed_at = new.decided_at,
        status = case
          when new.decision = 'escalate' or new.disposition = 'escalate_for_sar_consideration'
            then 'escalated'::public.case_status
          else 'closed'::public.case_status
        end
    where id = new.case_id and tenant_id = new.tenant_id;
  elsif new.decision = 'escalate' then
    update public.cases
    set status = 'escalated'
    where id = new.case_id and tenant_id = new.tenant_id;
  end if;
  return new;
end;
$$;

create trigger case_decisions_apply after insert on public.case_decisions
  for each row execute function private.apply_case_decision();

-- Linking an alert to a case moves the alert into the case workflow.
create function private.mark_alert_in_case()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.alerts
  set status = 'in_case'
  where id = new.alert_id and tenant_id = new.tenant_id and status in ('new', 'under_review');
  return new;
end;
$$;

create trigger case_alerts_mark_alert after insert on public.case_alerts
  for each row execute function private.mark_alert_in_case();
