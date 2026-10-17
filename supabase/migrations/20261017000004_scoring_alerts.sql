-- Scoring and alerts: model/rule runs, features, risk signals, alerts and their supporting links.

create table public.model_runs (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  model_name text not null,
  model_kind public.model_kind not null,
  model_version text not null,
  feature_set_version text,
  dataset_version text not null,
  code_version text,
  config jsonb not null default '{}'::jsonb,
  split_config jsonb not null default '{}'::jsonb,
  random_seed integer,
  metrics jsonb not null default '{}'::jsonb,
  metrics_are_demonstration boolean not null default true,
  artifact_bucket text,
  artifact_path text,
  status public.run_status not null default 'running',
  error_message text,
  triggered_by uuid references public.profiles (id) on delete set null,
  started_at timestamptz not null default now(),
  completed_at timestamptz,
  unique (tenant_id, id),
  check (completed_at is null or completed_at >= started_at),
  check ((artifact_bucket is null) = (artifact_path is null))
);
alter table public.model_runs enable row level security;
create index model_runs_kind_idx on public.model_runs (tenant_id, model_kind, started_at desc);
create index model_runs_triggered_by_idx on public.model_runs (triggered_by);

-- Point-in-time features: as_of_timestamp marks the information cutoff (no future leakage).
create table public.features (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  entity_type public.feature_entity not null,
  transaction_id uuid,
  account_id uuid,
  feature_set_version text not null,
  as_of_timestamp timestamptz not null,
  features jsonb not null,
  computed_at timestamptz not null default now(),
  unique (tenant_id, id),
  unique nulls not distinct (tenant_id, feature_set_version, entity_type, transaction_id, account_id, as_of_timestamp),
  foreign key (tenant_id, transaction_id) references public.transactions (tenant_id, id) on delete cascade,
  foreign key (tenant_id, account_id) references public.accounts (tenant_id, id) on delete cascade,
  check (
    (entity_type = 'transaction' and transaction_id is not null and account_id is null)
    or (entity_type = 'account' and account_id is not null and transaction_id is null)
  )
);
alter table public.features enable row level security;
create index features_transaction_idx on public.features (tenant_id, transaction_id);
create index features_account_idx on public.features (tenant_id, account_id);

-- One row per rule/model output. Network-level signals may have neither transaction_id nor account_id
-- and rely on evidence_transaction_ids.
create table public.risk_signals (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  model_run_id uuid not null,
  source public.signal_source not null,
  signal_name text not null,
  signal_version text not null,
  typology public.typology,
  transaction_id uuid,
  account_id uuid,
  feature_id uuid,
  score numeric(12, 6),
  threshold numeric(12, 6),
  triggered boolean not null,
  window_start timestamptz,
  window_end timestamptz,
  details jsonb not null default '{}'::jsonb,
  evidence_transaction_ids uuid[] not null default '{}',
  created_at timestamptz not null default now(),
  unique (tenant_id, id),
  foreign key (tenant_id, model_run_id) references public.model_runs (tenant_id, id) on delete cascade,
  foreign key (tenant_id, transaction_id) references public.transactions (tenant_id, id) on delete cascade,
  foreign key (tenant_id, account_id) references public.accounts (tenant_id, id) on delete cascade,
  foreign key (tenant_id, feature_id) references public.features (tenant_id, id) on delete set null (feature_id),
  check (window_start is null or window_end is null or window_end >= window_start)
);
alter table public.risk_signals enable row level security;
create index risk_signals_run_idx on public.risk_signals (tenant_id, model_run_id, triggered);
create index risk_signals_transaction_idx on public.risk_signals (tenant_id, transaction_id);
create index risk_signals_account_idx on public.risk_signals (tenant_id, account_id);
create index risk_signals_feature_idx on public.risk_signals (tenant_id, feature_id);

create table public.alerts (
  id uuid primary key default gen_random_uuid(),
  alert_number bigint generated always as identity unique,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  scope public.alert_scope not null,
  primary_account_id uuid,
  primary_transaction_id uuid,
  typology public.typology not null,
  priority_band public.priority_band not null,
  priority_rank integer check (priority_rank > 0),
  rank_score numeric(12, 6),
  ranking_strategy text not null,
  component_signals jsonb not null default '[]'::jsonb,
  window_start timestamptz,
  window_end timestamptz,
  total_amount numeric(20, 4) check (total_amount >= 0),
  amount_currency text check (amount_currency ~ '^[A-Z]{3}$'),
  transaction_count integer check (transaction_count >= 0),
  connected_account_count integer check (connected_account_count >= 0),
  has_dq_caveat boolean not null default false,
  dq_caveat_notes text,
  limitations text,
  explanation jsonb not null default '{}'::jsonb,
  model_run_id uuid not null,
  status public.alert_status not null default 'new',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, id),
  foreign key (tenant_id, primary_account_id) references public.accounts (tenant_id, id),
  foreign key (tenant_id, primary_transaction_id) references public.transactions (tenant_id, id),
  foreign key (tenant_id, model_run_id) references public.model_runs (tenant_id, id),
  check (window_start is null or window_end is null or window_end >= window_start),
  check (total_amount is null or amount_currency is not null),
  check (scope <> 'transaction' or primary_transaction_id is not null),
  check (scope <> 'account' or primary_account_id is not null)
);
alter table public.alerts enable row level security;
create index alerts_queue_idx on public.alerts (tenant_id, status, priority_band, priority_rank);
create index alerts_model_run_idx on public.alerts (tenant_id, model_run_id);
create index alerts_primary_account_idx on public.alerts (tenant_id, primary_account_id);
create index alerts_primary_transaction_idx on public.alerts (tenant_id, primary_transaction_id);
create trigger alerts_set_updated_at before update on public.alerts
  for each row execute function private.set_updated_at();

create table public.alert_signals (
  alert_id uuid not null,
  signal_id uuid not null,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  primary key (alert_id, signal_id),
  foreign key (tenant_id, alert_id) references public.alerts (tenant_id, id) on delete cascade,
  foreign key (tenant_id, signal_id) references public.risk_signals (tenant_id, id) on delete cascade
);
alter table public.alert_signals enable row level security;
create index alert_signals_alert_idx on public.alert_signals (tenant_id, alert_id);
create index alert_signals_signal_idx on public.alert_signals (tenant_id, signal_id);

create table public.alert_transactions (
  alert_id uuid not null,
  transaction_id uuid not null,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  role text not null default 'supporting' check (role in ('trigger', 'supporting', 'context')),
  primary key (alert_id, transaction_id),
  foreign key (tenant_id, alert_id) references public.alerts (tenant_id, id) on delete cascade,
  foreign key (tenant_id, transaction_id) references public.transactions (tenant_id, id)
);
alter table public.alert_transactions enable row level security;
create index alert_transactions_alert_idx on public.alert_transactions (tenant_id, alert_id);
create index alert_transactions_txn_idx on public.alert_transactions (tenant_id, transaction_id);

create table public.alert_accounts (
  alert_id uuid not null,
  account_id uuid not null,
  tenant_id uuid not null references public.tenants (id) on delete cascade,
  role text not null default 'counterparty' check (role in ('subject', 'counterparty', 'intermediary')),
  primary key (alert_id, account_id),
  foreign key (tenant_id, alert_id) references public.alerts (tenant_id, id) on delete cascade,
  foreign key (tenant_id, account_id) references public.accounts (tenant_id, id)
);
alter table public.alert_accounts enable row level security;
create index alert_accounts_alert_idx on public.alert_accounts (tenant_id, alert_id);
create index alert_accounts_account_idx on public.alert_accounts (tenant_id, account_id);
