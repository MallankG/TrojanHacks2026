-- Access model:
--   * anon: no access to any table.
--   * authenticated (browser): read-only, scoped to the user's tenant via profiles.
--   * service_role (FastAPI backend only): performs all writes after validating the user's JWT and role.

revoke all on all tables in schema public from anon;
revoke all on all sequences in schema public from anon;
revoke all on all functions in schema public from anon;
revoke insert, update, delete, truncate on all tables in schema public from authenticated;

create policy "Members can read their tenant" on public.tenants
  for select to authenticated
  using (id = (select private.current_tenant_id()));

create policy "Users can read own and same-tenant profiles" on public.profiles
  for select to authenticated
  using (id = (select auth.uid()) or tenant_id = (select private.current_tenant_id()));

-- Standard tenant-scoped read policy.
do $$
declare
  t text;
begin
  foreach t in array array[
    'source_files', 'ingestion_runs', 'source_rows', 'data_quality_issues',
    'counterparties', 'accounts', 'transactions',
    'model_runs', 'features', 'risk_signals',
    'alerts', 'alert_signals', 'alert_transactions', 'alert_accounts',
    'cases', 'case_alerts', 'case_evidence', 'agent_runs',
    'case_narratives', 'narrative_claims', 'narrative_citations', 'case_decisions',
    'audit_events'
  ]
  loop
    execute format(
      'create policy %I on public.%I for select to authenticated using (tenant_id = (select private.current_tenant_id()))',
      'Tenant members can read ' || t, t
    );
  end loop;
end;
$$;

-- Ground-truth labels: admins only, so investigators cannot see the answer key.
create policy "Tenant admins can read ground-truth labels" on public.transaction_labels
  for select to authenticated
  using (
    tenant_id = (select private.current_tenant_id())
    and (select private.current_app_role()) = 'admin'
  );

-- Policy corpus: shared public guidance (tenant_id null) plus the tenant's own documents.
create policy "Members can read shared and tenant policy documents" on public.policy_documents
  for select to authenticated
  using (tenant_id is null or tenant_id = (select private.current_tenant_id()));

create policy "Members can read shared and tenant policy chunks" on public.policy_chunks
  for select to authenticated
  using (tenant_id is null or tenant_id = (select private.current_tenant_id()));
