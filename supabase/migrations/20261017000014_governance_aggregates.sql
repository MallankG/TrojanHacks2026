-- Read-only aggregates for the governance center (model registry, drift and data health, detector comparison).
-- Grouping happens in Postgres so the backend never pages through every signal or data-quality issue.
-- Only the backend (service role) may call these; tenant scoping is part of every WHERE clause.

-- Signal counts per model run, split by source, typology and whether the signal triggered.
create function public.governance_signal_stats(p_tenant_id uuid, p_run_ids uuid[])
returns table (
  model_run_id uuid,
  source public.signal_source,
  typology public.typology,
  triggered boolean,
  signal_count bigint,
  scored_count bigint,
  score_min numeric,
  score_max numeric
)
language sql
stable
security invoker
set search_path = ''
as $$
  select s.model_run_id, s.source, s.typology, s.triggered, count(*), count(s.score), min(s.score), max(s.score)
  from public.risk_signals s
  where s.tenant_id = p_tenant_id and s.model_run_id = any (p_run_ids)
  group by s.model_run_id, s.source, s.typology, s.triggered;
$$;

-- Score histogram per model run: ten equal bins over [0, 1]; scores outside the range land in the edge bins.
create function public.governance_score_histogram(p_tenant_id uuid, p_run_ids uuid[])
returns table (model_run_id uuid, bin integer, signal_count bigint)
language sql
stable
security invoker
set search_path = ''
as $$
  select s.model_run_id, least(greatest(width_bucket(s.score, 0, 1, 10), 1), 10) as bin, count(*)
  from public.risk_signals s
  where s.tenant_id = p_tenant_id and s.model_run_id = any (p_run_ids) and s.score is not null
  group by 1, 2;
$$;

-- Distinct transactions flagged by rules, by models, or by both, across the given runs.
create function public.governance_signal_overlap(p_tenant_id uuid, p_run_ids uuid[])
returns table (rules_only bigint, both_sources bigint, models_only bigint)
language sql
stable
security invoker
set search_path = ''
as $$
  with flagged as (
    select s.transaction_id, bool_or(s.source = 'rule') as by_rule, bool_or(s.source <> 'rule') as by_model
    from public.risk_signals s
    where s.tenant_id = p_tenant_id and s.model_run_id = any (p_run_ids) and s.triggered and s.transaction_id is not null
    group by s.transaction_id
  )
  select count(*) filter (where by_rule and not by_model),
         count(*) filter (where by_rule and by_model),
         count(*) filter (where by_model and not by_rule)
  from flagged;
$$;

-- Data-quality issue mix per ingestion run.
create function public.governance_dq_mix(p_tenant_id uuid, p_run_ids uuid[])
returns table (run_id uuid, issue_type public.dq_issue_type, severity public.dq_severity, issue_count bigint)
language sql
stable
security invoker
set search_path = ''
as $$
  select d.run_id, d.issue_type, d.severity, count(*)
  from public.data_quality_issues d
  where d.tenant_id = p_tenant_id and d.run_id = any (p_run_ids)
  group by d.run_id, d.issue_type, d.severity;
$$;

-- Alerts published per model run.
create function public.governance_alert_counts(p_tenant_id uuid, p_run_ids uuid[])
returns table (model_run_id uuid, alert_count bigint)
language sql
stable
security invoker
set search_path = ''
as $$
  select a.model_run_id, count(*)
  from public.alerts a
  where a.tenant_id = p_tenant_id and a.model_run_id = any (p_run_ids)
  group by a.model_run_id;
$$;

revoke execute on function public.governance_alert_counts(uuid, uuid[]) from public, anon, authenticated;
grant execute on function public.governance_alert_counts(uuid, uuid[]) to service_role;
revoke execute on function public.governance_signal_stats(uuid, uuid[]) from public, anon, authenticated;
revoke execute on function public.governance_score_histogram(uuid, uuid[]) from public, anon, authenticated;
revoke execute on function public.governance_signal_overlap(uuid, uuid[]) from public, anon, authenticated;
revoke execute on function public.governance_dq_mix(uuid, uuid[]) from public, anon, authenticated;
grant execute on function public.governance_signal_stats(uuid, uuid[]) to service_role;
grant execute on function public.governance_score_histogram(uuid, uuid[]) to service_role;
grant execute on function public.governance_signal_overlap(uuid, uuid[]) to service_role;
grant execute on function public.governance_dq_mix(uuid, uuid[]) to service_role;
