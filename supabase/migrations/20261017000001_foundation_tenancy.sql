-- TraceLayer foundation: extensions, private helper schema, enums, tenancy and profiles.

create extension if not exists vector with schema extensions;
create extension if not exists pg_trgm with schema extensions;

-- Helper functions live in a schema that is not exposed through the Data API.
create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated, service_role;

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------
create type public.app_role as enum ('admin', 'investigator', 'analyst', 'reviewer', 'data_steward');
create type public.actor_type as enum ('user', 'system', 'agent');

create type public.ingestion_status as enum ('pending', 'running', 'completed', 'completed_with_errors', 'failed');
create type public.source_row_status as enum ('accepted', 'accepted_with_warnings', 'rejected');
create type public.dq_issue_type as enum (
  'missing_required_field', 'invalid_timestamp', 'invalid_currency', 'invalid_amount',
  'duplicate_record', 'unresolved_account', 'schema_mismatch', 'invalid_format', 'other'
);
create type public.dq_severity as enum ('info', 'warning', 'error', 'critical');
create type public.dq_issue_status as enum ('open', 'acknowledged', 'remediated', 'waived');

create type public.entity_type as enum ('individual', 'business', 'financial_institution', 'unknown');
create type public.resolution_status as enum ('unresolved', 'candidate_match', 'resolved', 'distinct');
create type public.account_status as enum ('active', 'dormant', 'restricted', 'closed');
create type public.txn_channel as enum ('ach', 'wire', 'card', 'cash', 'check', 'p2p', 'internal_transfer', 'crypto', 'other');
create type public.txn_direction as enum ('inbound', 'outbound', 'internal', 'external');
create type public.txn_status as enum ('posted', 'pending', 'reversed', 'failed');

create type public.typology as enum (
  'rapid_in_out', 'layering', 'mule_network', 'funnel', 'fan_out', 'cycle', 'structuring',
  'dormant_reactivation', 'behavioral_deviation', 'scam_proceeds', 'model_anomaly', 'other'
);
create type public.model_kind as enum ('rule_engine', 'supervised', 'anomaly', 'graph_model', 'ranker');
create type public.run_status as enum ('running', 'completed', 'failed');
create type public.feature_entity as enum ('transaction', 'account');
create type public.signal_source as enum ('rule', 'supervised_model', 'anomaly_model', 'graph_model');

create type public.alert_scope as enum ('transaction', 'account', 'network');
create type public.priority_band as enum ('low', 'medium', 'high', 'critical');
create type public.alert_status as enum ('new', 'under_review', 'in_case', 'closed_no_action', 'dismissed');

create type public.case_status as enum ('open', 'in_review', 'pending_review', 'escalated', 'closed');
create type public.case_disposition as enum (
  'no_further_action', 'false_positive', 'monitor', 'needs_more_information', 'escalate_for_sar_consideration'
);
create type public.evidence_type as enum (
  'transaction', 'transaction_path', 'account', 'alert', 'risk_signal',
  'data_quality_issue', 'policy_citation', 'analyst_note'
);
create type public.agent_run_status as enum ('queued', 'running', 'awaiting_review', 'completed', 'failed');
create type public.verification_status as enum ('pending', 'passed', 'flagged', 'failed');
create type public.claim_kind as enum ('fact', 'hypothesis', 'unknown');
create type public.claim_verification as enum ('pending', 'supported', 'unsupported', 'unverifiable', 'not_applicable');
create type public.review_decision as enum ('accept', 'edit', 'reject', 'escalate');

-- ---------------------------------------------------------------------------
-- Shared trigger: updated_at
-- ---------------------------------------------------------------------------
create function private.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- Tenancy
-- ---------------------------------------------------------------------------
create table public.tenants (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique check (slug ~ '^[a-z0-9-]+$'),
  name text not null,
  is_synthetic boolean not null default true,
  created_at timestamptz not null default now()
);
alter table public.tenants enable row level security;

-- Role and tenant are stored here (not in user-editable auth metadata).
-- Users cannot update their own profile; only the backend (service role) can.
create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  tenant_id uuid references public.tenants (id) on delete set null,
  role public.app_role not null default 'investigator',
  display_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.profiles enable row level security;
create index profiles_tenant_id_idx on public.profiles (tenant_id);
create trigger profiles_set_updated_at before update on public.profiles
  for each row execute function private.set_updated_at();

-- ---------------------------------------------------------------------------
-- Auth helpers used by RLS policies
-- ---------------------------------------------------------------------------
create function private.current_tenant_id()
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
  select p.tenant_id from public.profiles p where p.id = (select auth.uid());
$$;

create function private.current_app_role()
returns public.app_role
language sql
stable
security definer
set search_path = ''
as $$
  select p.role from public.profiles p where p.id = (select auth.uid());
$$;

revoke execute on function private.current_tenant_id() from public, anon;
revoke execute on function private.current_app_role() from public, anon;
grant execute on function private.current_tenant_id() to authenticated, service_role;
grant execute on function private.current_app_role() to authenticated, service_role;

-- New sign-ups get a profile with no tenant, so they see nothing until an
-- admin/backend assigns them to a tenant and role.
create function private.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'display_name', new.email));
  return new;
end;
$$;

create trigger on_auth_user_created after insert on auth.users
  for each row execute function private.handle_new_user();

-- Demo tenant for the hackathon (synthetic data only).
insert into public.tenants (slug, name, is_synthetic)
values ('demo-bank', 'Demo Bank (Synthetic)', true);
