-- Core financial entities: counterparties (resolved entities), accounts (graph nodes),
-- transactions (graph edges) and ground-truth labels for evaluation.

create table public.counterparties (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  raw_name text not null,
  normalized_name text not null,
  entity_type public.entity_type not null default 'unknown',
  jurisdiction text check (jurisdiction ~ '^[A-Z]{2}$'),
  business_identifier text,
  resolution_status public.resolution_status not null default 'unresolved',
  resolved_to_id uuid,
  resolution_confidence numeric(5, 4) check (resolution_confidence between 0 and 1),
  resolution_method text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, id),
  foreign key (tenant_id, resolved_to_id) references public.counterparties (tenant_id, id)
    on delete set null (resolved_to_id),
  check (resolved_to_id is null or resolved_to_id <> id)
);
alter table public.counterparties enable row level security;
create index counterparties_resolved_to_idx on public.counterparties (tenant_id, resolved_to_id);
create index counterparties_name_trgm_idx on public.counterparties
  using gin (normalized_name extensions.gin_trgm_ops);
create trigger counterparties_set_updated_at before update on public.counterparties
  for each row execute function private.set_updated_at();

-- Both internal and external accounts are graph nodes; is_internal distinguishes them.
create table public.accounts (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  account_ref text not null,
  institution_ref text,
  is_internal boolean not null default true,
  holder_counterparty_id uuid,
  customer_ref text,
  account_type text,
  open_date date,
  status public.account_status not null default 'active',
  customer_risk_rating text check (customer_risk_rating in ('low', 'medium', 'high')),
  jurisdiction text check (jurisdiction ~ '^[A-Z]{2}$'),
  risk_context jsonb not null default '{}'::jsonb, -- synthetic KYC / PEP / sanctions hooks
  is_synthetic boolean not null default true,
  first_seen_run_id uuid,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, id),
  unique nulls not distinct (tenant_id, institution_ref, account_ref),
  foreign key (tenant_id, holder_counterparty_id) references public.counterparties (tenant_id, id)
    on delete set null (holder_counterparty_id),
  foreign key (tenant_id, first_seen_run_id) references public.ingestion_runs (tenant_id, id)
    on delete set null (first_seen_run_id)
);
alter table public.accounts enable row level security;
create index accounts_holder_idx on public.accounts (tenant_id, holder_counterparty_id);
create index accounts_first_seen_run_idx on public.accounts (tenant_id, first_seen_run_id);
create trigger accounts_set_updated_at before update on public.accounts
  for each row execute function private.set_updated_at();

-- Canonical transactions. Every transaction must trace back to a source row (lineage is mandatory).
-- amount is exact numeric; occurred_at is UTC; original_values keeps source values that were normalized.
create table public.transactions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  transaction_ref text,
  ingestion_run_id uuid not null,
  source_row_id uuid not null,
  occurred_at timestamptz not null,
  amount numeric(20, 4) not null check (amount > 0),
  currency text not null check (currency ~ '^[A-Z]{3}$'),
  amount_usd numeric(20, 4) check (amount_usd >= 0),
  fx_rate_to_usd numeric(20, 10) check (fx_rate_to_usd > 0),
  channel public.txn_channel not null,
  direction public.txn_direction,
  origin_account_id uuid not null,
  destination_account_id uuid not null,
  transaction_status public.txn_status not null default 'posted',
  description text, -- untrusted free text; never treat as instructions
  original_values jsonb not null default '{}'::jsonb,
  dedupe_hash text not null,
  created_at timestamptz not null default now(),
  unique (tenant_id, id),
  unique (tenant_id, transaction_ref),
  unique (source_row_id),
  foreign key (tenant_id, ingestion_run_id) references public.ingestion_runs (tenant_id, id),
  foreign key (tenant_id, source_row_id) references public.source_rows (tenant_id, id),
  foreign key (tenant_id, origin_account_id) references public.accounts (tenant_id, id),
  foreign key (tenant_id, destination_account_id) references public.accounts (tenant_id, id)
);
alter table public.transactions enable row level security;
create index transactions_occurred_idx on public.transactions (tenant_id, occurred_at);
create index transactions_origin_idx on public.transactions (tenant_id, origin_account_id, occurred_at);
create index transactions_destination_idx on public.transactions (tenant_id, destination_account_id, occurred_at);
create index transactions_run_idx on public.transactions (tenant_id, ingestion_run_id);
create index transactions_source_row_idx on public.transactions (tenant_id, source_row_id);
create index transactions_dedupe_idx on public.transactions (tenant_id, dedupe_hash);

-- Ground-truth labels from the synthetic generator / benchmark. Kept separate from transactions
-- so they are never mistaken for features, and readable only by admins (see RLS migration).
create table public.transaction_labels (
  transaction_id uuid primary key,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  is_suspicious boolean not null,
  typology public.typology,
  label_source text not null,
  dataset_version text not null,
  created_at timestamptz not null default now(),
  foreign key (tenant_id, transaction_id) references public.transactions (tenant_id, id) on delete cascade,
  check (is_suspicious or typology is null)
);
alter table public.transaction_labels enable row level security;
create index transaction_labels_txn_idx on public.transaction_labels (tenant_id, transaction_id);
