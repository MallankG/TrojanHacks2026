-- PRD 20.2. Versioned catalog definitions, immutable assessments and governance decisions.
create table public.governance_products (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  source_system text not null,
  definition jsonb not null check (jsonb_typeof(definition) = 'object'),
  created_by uuid not null,
  created_at timestamptz not null default now(),
  unique(tenant_id, source_system),
  unique(tenant_id, id)
);
create table public.governance_events (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  product_id uuid not null,
  actor_id uuid not null,
  kind text not null check (kind in ('assessment','access_request','exception','waiver','remediation','mapping','incident_note','incident_resolution','steward_query')),
  payload jsonb not null check (jsonb_typeof(payload) = 'object'),
  created_at timestamptz not null default now(),
  foreign key (tenant_id, product_id) references public.governance_products(tenant_id, id)
);
-- An immutable request can receive only one terminal decision, including concurrent requests.
create unique index governance_single_decision on public.governance_events(tenant_id, (payload->>'request_id'))
  where payload->>'decision' in ('approved', 'rejected');
create index governance_events_product_time on public.governance_events(tenant_id, product_id, created_at desc);
alter table public.governance_products enable row level security;
alter table public.governance_events enable row level security;
revoke all on public.governance_products, public.governance_events from anon, authenticated;
grant select on public.governance_products, public.governance_events to authenticated;
grant all on public.governance_products, public.governance_events to service_role;
create policy "Tenant governance products" on public.governance_products for select to authenticated
  using (tenant_id = (select private.current_tenant_id()));
create policy "Tenant governance events" on public.governance_events for select to authenticated
  using (tenant_id = (select private.current_tenant_id()));
create trigger governance_events_immutable before update or delete on public.governance_events
  for each row execute function private.prevent_audit_mutation();
create trigger governance_products_immutable before update or delete on public.governance_products
  for each row execute function private.prevent_audit_mutation();
create function private.audit_governance()
returns trigger language plpgsql security definer set search_path = '' as $$
begin
  perform private.write_audit_event(new.tenant_id,
    case when tg_table_name = 'governance_products' then (to_jsonb(new)->>'created_by')::uuid else (to_jsonb(new)->>'actor_id')::uuid end,
    'user'::public.actor_type, 'governance.recorded', tg_table_name, new.id::text,
    jsonb_build_object('kind', coalesce(to_jsonb(new)->>'kind', 'catalog_registration')));
  return new;
end; $$;
create trigger governance_products_audit after insert on public.governance_products
  for each row execute function private.audit_governance();
create trigger governance_events_audit after insert on public.governance_events
  for each row execute function private.audit_governance();
