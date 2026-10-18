-- Immutable contract history. Highest revision is active; existing rows remain revision 1.
alter table public.governance_products
  drop constraint governance_products_tenant_id_source_system_key,
  add column revision integer not null default 1 check (revision > 0),
  add column replaces_product_id uuid,
  add constraint governance_products_predecessor_fk foreign key (tenant_id, replaces_product_id)
    references public.governance_products(tenant_id, id),
  add constraint governance_products_source_revision_key unique (tenant_id, source_system, revision);

alter table public.ingestion_runs add column governance_product_id uuid,
  add constraint ingestion_runs_governance_product_fk foreign key (tenant_id, governance_product_id)
    references public.governance_products(tenant_id, id);

-- Pin once, so later source revisions cannot change the meaning of a historical run.
create function private.prevent_contract_repin() returns trigger language plpgsql set search_path = '' as $$
begin
  if old.governance_product_id is not null and new.governance_product_id is distinct from old.governance_product_id then
    raise exception 'An ingestion contract pin is immutable' using errcode = '23514';
  end if;
  return new;
end; $$;
create trigger ingestion_contract_pin_immutable before update on public.ingestion_runs
  for each row execute function private.prevent_contract_repin();

create function public.replace_governance_product(p_tenant_id uuid, p_product_id uuid,
  p_actor_id uuid, p_definition jsonb, p_reason text)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
  previous public.governance_products;
  replacement public.governance_products;
begin
  select * into previous from public.governance_products where tenant_id = p_tenant_id and id = p_product_id;
  if not found then
    raise exception 'Governed product not found' using errcode = '23503';
  end if;
  -- Serialize replacements for this source, then reject stale callers rather than lose edits.
  perform pg_advisory_xact_lock(hashtextextended(p_tenant_id::text || ':' || previous.source_system, 0));
  if exists (select 1 from public.governance_products where tenant_id = p_tenant_id
      and source_system = previous.source_system and revision > previous.revision) then
    raise exception 'Product was already replaced; refresh the current revision' using errcode = '23505';
  end if;
  if p_definition->>'source_system' is distinct from previous.source_system
      or nullif(btrim(p_reason), '') is null or length(btrim(p_reason)) < 10
      or nullif(p_definition->'contract'->>'version', '') is null
      or p_definition->'contract'->>'version' = previous.definition->'contract'->>'version' then
    raise exception 'Preserve source_system, provide a reason and a new contract version' using errcode = '23514';
  end if;
  insert into public.governance_products (tenant_id, source_system, definition, created_by, revision, replaces_product_id)
    values (p_tenant_id, previous.source_system, p_definition, p_actor_id, previous.revision + 1, previous.id)
    returning * into replacement;
  insert into public.governance_events (tenant_id, product_id, actor_id, kind, payload)
    values (p_tenant_id, replacement.id, p_actor_id, 'remediation', jsonb_build_object(
      'operation', 'contract_replacement', 'previous_product_id', previous.id,
      'previous_revision', previous.revision, 'revision', replacement.revision, 'reason', p_reason));
  return to_jsonb(replacement);
end; $$;
revoke all on function public.replace_governance_product(uuid, uuid, uuid, jsonb, text) from public, anon, authenticated;
grant execute on function public.replace_governance_product(uuid, uuid, uuid, jsonb, text) to service_role;
