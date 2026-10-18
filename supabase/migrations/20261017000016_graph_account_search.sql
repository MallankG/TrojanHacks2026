-- Account search for the graph workbench: accounts matching a reference fragment with their
-- transaction degree and volume, aggregated in the database so the API never pulls every
-- transaction to rank starting points. Called by the backend (service role) only; the backend
-- passes the caller's tenant explicitly, like every other service-role query.

create or replace function public.graph_account_search(
  p_tenant_id uuid,
  p_query text default null,
  p_limit integer default 25
)
returns table (
  id uuid,
  account_ref text,
  institution_ref text,
  is_internal boolean,
  status public.account_status,
  in_count bigint,
  out_count bigint,
  degree bigint,
  counterparty_count bigint,
  volume_usd numeric,
  first_seen timestamptz,
  last_seen timestamptz
)
language sql
stable
set search_path = ''
as $$
  with matched as (
    select a.id, a.account_ref, a.institution_ref, a.is_internal, a.status
    from public.accounts a
    where a.tenant_id = p_tenant_id
      and (p_query is null or p_query = '' or a.account_ref ilike '%' || p_query || '%')
  ),
  legs as (
    select t.origin_account_id as account_id, t.destination_account_id as other_id, 'out'::text as side,
           t.amount_usd, t.occurred_at
    from public.transactions t
    where t.tenant_id = p_tenant_id and t.origin_account_id in (select m.id from matched m)
    union all
    select t.destination_account_id, t.origin_account_id, 'in', t.amount_usd, t.occurred_at
    from public.transactions t
    where t.tenant_id = p_tenant_id and t.destination_account_id in (select m.id from matched m)
  )
  select m.id, m.account_ref, m.institution_ref, m.is_internal, m.status,
         count(l.account_id) filter (where l.side = 'in'),
         count(l.account_id) filter (where l.side = 'out'),
         count(l.account_id),
         count(distinct l.other_id),
         coalesce(sum(l.amount_usd), 0),
         min(l.occurred_at),
         max(l.occurred_at)
  from matched m
  left join legs l on l.account_id = m.id
  group by m.id, m.account_ref, m.institution_ref, m.is_internal, m.status
  order by count(l.account_id) desc, m.account_ref
  limit least(greatest(coalesce(p_limit, 25), 1), 100)
$$;

revoke execute on function public.graph_account_search(uuid, text, integer) from public, anon, authenticated;
grant execute on function public.graph_account_search(uuid, text, integer) to service_role;
