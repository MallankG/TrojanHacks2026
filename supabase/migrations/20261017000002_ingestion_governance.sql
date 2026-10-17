-- Ingestion and data governance: source files, ingestion runs, raw source rows, data-quality issues.
-- Child tables use composite (tenant_id, id) foreign keys so rows can never reference another tenant's data.

create table public.source_files (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  filename text not null,
  storage_bucket text not null default 'source-files',
  storage_path text not null,
  checksum_sha256 text not null check (checksum_sha256 ~ '^[a-f0-9]{64}$'),
  byte_size bigint check (byte_size >= 0),
  source_system text not null,
  data_owner text not null,
  schema_version text not null,
  is_synthetic boolean not null default true,
  uploaded_by uuid references public.profiles (id) on delete set null,
  uploaded_at timestamptz not null default now(),
  unique (tenant_id, id),
  unique (tenant_id, checksum_sha256),
  unique (storage_bucket, storage_path)
);
alter table public.source_files enable row level security;
create index source_files_uploaded_by_idx on public.source_files (uploaded_by);

create table public.ingestion_runs (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  file_id uuid not null,
  status public.ingestion_status not null default 'pending',
  schema_version text not null,
  field_mapping jsonb not null default '{}'::jsonb,
  pipeline_version text,
  row_count integer not null default 0 check (row_count >= 0),
  accepted_count integer not null default 0 check (accepted_count >= 0),
  warning_count integer not null default 0 check (warning_count >= 0),
  rejected_count integer not null default 0 check (rejected_count >= 0),
  error_message text,
  triggered_by uuid references public.profiles (id) on delete set null,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (tenant_id, id),
  foreign key (tenant_id, file_id) references public.source_files (tenant_id, id) on delete cascade,
  check (accepted_count + rejected_count <= row_count),
  check (warning_count <= accepted_count),
  check (completed_at is null or completed_at >= started_at)
);
alter table public.ingestion_runs enable row level security;
create index ingestion_runs_file_idx on public.ingestion_runs (tenant_id, file_id);
create index ingestion_runs_started_idx on public.ingestion_runs (tenant_id, started_at desc);
create index ingestion_runs_triggered_by_idx on public.ingestion_runs (triggered_by);

-- Every raw CSV row is kept verbatim so any transaction or DQ issue can be traced to its source.
create table public.source_rows (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  run_id uuid not null,
  row_number integer not null check (row_number >= 1),
  raw_data jsonb not null,
  row_hash text not null,
  status public.source_row_status not null,
  created_at timestamptz not null default now(),
  unique (tenant_id, id),
  unique (run_id, row_number),
  foreign key (tenant_id, run_id) references public.ingestion_runs (tenant_id, id) on delete cascade
);
alter table public.source_rows enable row level security;
create index source_rows_run_idx on public.source_rows (tenant_id, run_id, status);

create table public.data_quality_issues (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  run_id uuid not null,
  source_row_id uuid, -- null for file-level issues such as schema mismatch
  field_name text,
  issue_type public.dq_issue_type not null,
  severity public.dq_severity not null,
  rule_id text not null,
  message text not null,
  observed_value text,
  expected_value text,
  status public.dq_issue_status not null default 'open',
  remediation_note text,
  resolved_by uuid references public.profiles (id) on delete set null,
  resolved_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, id),
  foreign key (tenant_id, run_id) references public.ingestion_runs (tenant_id, id) on delete cascade,
  foreign key (tenant_id, source_row_id) references public.source_rows (tenant_id, id) on delete cascade,
  check (status not in ('remediated', 'waived') or resolved_at is not null)
);
alter table public.data_quality_issues enable row level security;
create index dq_issues_run_idx on public.data_quality_issues (tenant_id, run_id, severity);
create index dq_issues_source_row_idx on public.data_quality_issues (tenant_id, source_row_id);
create index dq_issues_status_idx on public.data_quality_issues (tenant_id, status);
create index dq_issues_resolved_by_idx on public.data_quality_issues (resolved_by);
create trigger dq_issues_set_updated_at before update on public.data_quality_issues
  for each row execute function private.set_updated_at();
