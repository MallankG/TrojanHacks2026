-- Case review SLA, four-eyes escalation requests and hashed evidence-package exports (PRD §9, §14, §22.4).
-- Additive only: existing cases keep due_at null ("no SLA set") until someone sets one.

alter table public.cases
  add column due_at timestamptz,
  add column escalation_requested_by uuid references public.profiles (id) on delete set null,
  add column escalation_requested_at timestamptz,
  add column escalation_reason text,
  add constraint cases_escalation_request_complete check (escalation_requested_by is null or escalation_requested_at is not null);

create index cases_due_at_idx on public.cases (tenant_id, due_at) where due_at is not null;
create index cases_escalation_requested_by_idx on public.cases (escalation_requested_by);

-- One row per SAR-support evidence package written to the case-exports bucket.
-- manifest_sha256 hashes the canonical manifest (content only, no export id or timestamp), so an
-- unchanged case re-exports to the same hash. package_sha256 hashes the SHA256SUMS file listing every file.
create table public.evidence_exports (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  case_id uuid not null,
  storage_bucket text not null,
  storage_prefix text not null,
  manifest_sha256 text not null check (manifest_sha256 ~ '^[0-9a-f]{64}$'),
  package_sha256 text not null check (package_sha256 ~ '^[0-9a-f]{64}$'),
  files jsonb not null default '[]'::jsonb,
  manifest jsonb not null default '{}'::jsonb,
  checklist jsonb not null default '[]'::jsonb,
  created_by uuid references public.profiles (id) on delete set null,
  created_at timestamptz not null default now(),
  unique (tenant_id, id),
  foreign key (tenant_id, case_id) references public.cases (tenant_id, id) on delete cascade
);

alter table public.evidence_exports enable row level security;
create index evidence_exports_case_idx on public.evidence_exports (tenant_id, case_id, created_at desc);
create index evidence_exports_created_by_idx on public.evidence_exports (created_by);

-- Browser users read their tenant's exports; only the backend (service role) writes them.
revoke all on public.evidence_exports from anon;
revoke insert, update, delete, truncate on public.evidence_exports from authenticated;
create policy "Tenant members can read evidence_exports" on public.evidence_exports
  for select to authenticated
  using (tenant_id = (select private.current_tenant_id()));

-- Export records are evidence of what was released: never edited in place (cascading case deletes still apply).
create function private.prevent_evidence_export_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  raise exception 'evidence_exports rows are immutable' using errcode = '42501';
end;
$$;

create trigger evidence_exports_immutable before update on public.evidence_exports
  for each row execute function private.prevent_evidence_export_update();
