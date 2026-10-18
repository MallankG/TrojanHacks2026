-- Disposable PostgreSQL fixture: the test leaves no data behind.
begin;
insert into public.tenants(id,slug,name) values
 ('a0000000-0000-4000-8000-000000000001','contract-test-a','Contract A'),
 ('a0000000-0000-4000-8000-000000000002','contract-test-b','Contract B');
insert into auth.users(id) values ('a1000000-0000-4000-8000-000000000001'),('a1000000-0000-4000-8000-000000000002');
insert into public.profiles(id,tenant_id,role) values
 ('a1000000-0000-4000-8000-000000000001','a0000000-0000-4000-8000-000000000001','data_steward'),
 ('a1000000-0000-4000-8000-000000000002','a0000000-0000-4000-8000-000000000002','data_steward')
on conflict(id) do update set tenant_id=excluded.tenant_id,role=excluded.role;
insert into public.governance_products(id,tenant_id,source_system,definition,created_by) values
 ('a2000000-0000-4000-8000-000000000001','a0000000-0000-4000-8000-000000000001','payments','{"source_system":"payments","contract":{"version":"v1"}}','a1000000-0000-4000-8000-000000000001'),
 ('a2000000-0000-4000-8000-000000000002','a0000000-0000-4000-8000-000000000002','payments','{"source_system":"payments","contract":{"version":"v1"}}','a1000000-0000-4000-8000-000000000002');
insert into public.source_files(id,tenant_id,filename,storage_path,checksum_sha256,source_system,data_owner,schema_version) values
 ('a4000000-0000-4000-8000-000000000001','a0000000-0000-4000-8000-000000000001','payments.csv','contract-test/payments.csv',repeat('a',64),'payments','steward','v1');
insert into public.ingestion_runs(id,tenant_id,governance_product_id,file_id,schema_version) values
 ('a3000000-0000-4000-8000-000000000001','a0000000-0000-4000-8000-000000000001','a2000000-0000-4000-8000-000000000001','a4000000-0000-4000-8000-000000000001','v1');
set local role service_role;
do $$ declare replacement jsonb; old_id uuid := 'a2000000-0000-4000-8000-000000000001';
 tid uuid := 'a0000000-0000-4000-8000-000000000001'; actor uuid := 'a1000000-0000-4000-8000-000000000001';
begin
 replacement := public.replace_governance_product(tid,old_id,actor,
  '{"source_system":"payments","contract":{"version":"v2"}}','Correct reviewed source contract');
 if (replacement->>'revision')::int <> 2 or (replacement->>'replaces_product_id')::uuid <> old_id then
  raise exception 'Replacement lost revision/predecessor'; end if;
 if not exists(select 1 from public.governance_events where product_id=(replacement->>'id')::uuid
  and kind='remediation' and payload->>'reason'='Correct reviewed source contract'
  and payload->>'previous_product_id'=old_id::text) then raise exception 'Replacement receipt missing'; end if;
 if (select definition->'contract'->>'version' from public.governance_products where id=old_id)<>'v1' then
  raise exception 'Historical definition changed'; end if;
 if (select id from public.governance_products where tenant_id=tid and source_system='payments' order by revision desc limit 1)<>(replacement->>'id')::uuid then
  raise exception 'New contract not active'; end if;
 begin
  perform public.replace_governance_product(tid,old_id,actor,'{"source_system":"payments","contract":{"version":"v3"}}','Stale replacement must fail');
  raise exception 'Stale replacement accepted';
 exception when unique_violation then null; end;
 begin
  perform public.replace_governance_product(tid,(replacement->>'id')::uuid,actor,'{"source_system":"payments","contract":{"version":"v2"}}','Unchanged version must fail');
  raise exception 'Same contract version accepted';
 exception when check_violation then null; end;
 begin
  perform public.replace_governance_product(tid,'a2000000-0000-4000-8000-000000000002',actor,'{"source_system":"payments","contract":{"version":"v2"}}','Cross tenant replacement must fail');
  raise exception 'Cross tenant predecessor accepted';
 exception when foreign_key_violation then null; end;
 begin
  update public.governance_products set definition='{}' where id=old_id;
  raise exception 'Immutable definition updated';
 exception when insufficient_privilege then null; end;
 begin
  delete from public.governance_products where id=old_id;
  raise exception 'Immutable definition deleted';
 exception when insufficient_privilege then null; end;
 begin
  update public.ingestion_runs set governance_product_id=(replacement->>'id')::uuid where id='a3000000-0000-4000-8000-000000000001';
  raise exception 'Historical ingestion pin changed';
 exception when check_violation then null; end;
 begin
  update public.ingestion_runs set governance_product_id=null where id='a3000000-0000-4000-8000-000000000001';
  raise exception 'Historical ingestion pin cleared';
 exception when check_violation then null; end;
 begin
  insert into public.ingestion_runs(id,tenant_id,governance_product_id,file_id,schema_version) values('a3000000-0000-4000-8000-000000000002',tid,'a2000000-0000-4000-8000-000000000002','a4000000-0000-4000-8000-000000000001','v1');
  raise exception 'Cross tenant ingestion pin accepted';
 exception when foreign_key_violation then null; end;
end $$;
reset role;
do $$ begin
 if (select count(*) from public.audit_events where object_type='governance_products')<>3 then raise exception 'Product insert audit missing'; end if;
 if not exists(select 1 from public.audit_events where object_type='governance_events') then raise exception 'Remediation audit missing'; end if;
 if has_function_privilege('anon','public.replace_governance_product(uuid,uuid,uuid,jsonb,text)','execute')
 or has_function_privilege('authenticated','public.replace_governance_product(uuid,uuid,uuid,jsonb,text)','execute')
 or not has_function_privilege('service_role','public.replace_governance_product(uuid,uuid,uuid,jsonb,text)','execute') then raise exception 'RPC privilege boundary wrong'; end if;
end $$;
-- Fail the mandatory receipt write to prove replacement and its automatic audit roll back together.
create function private.test_reject_receipt() returns trigger language plpgsql as $$begin raise exception 'Receipt unavailable' using errcode='23514'; end $$;
create trigger test_receipt_failure before insert on public.governance_events for each row execute function private.test_reject_receipt();
do $$ declare latest uuid; products_before int; audits_before int; begin
 select id into latest from public.governance_products where tenant_id='a0000000-0000-4000-8000-000000000001' order by revision desc limit 1;
 select count(*) into products_before from public.governance_products;
 select count(*) into audits_before from public.audit_events;
 begin
  perform public.replace_governance_product('a0000000-0000-4000-8000-000000000001',latest,'a1000000-0000-4000-8000-000000000001','{"source_system":"payments","contract":{"version":"v3"}}','Receipt failure must abort all');
  raise exception 'Missing receipt accepted';
 exception when check_violation then null; end;
 if (select count(*) from public.governance_products)<>products_before or (select count(*) from public.audit_events)<>audits_before then raise exception 'Failed receipt left partial replacement/audit'; end if;
end $$;
set local role authenticated;
select set_config('request.jwt.claim.sub','a1000000-0000-4000-8000-000000000002',true);
do $$ begin
 if exists(select 1 from public.governance_products where tenant_id='a0000000-0000-4000-8000-000000000001') then raise exception 'Product history leaked across tenants'; end if;
 if exists(select 1 from public.governance_events where tenant_id='a0000000-0000-4000-8000-000000000001') then raise exception 'Remediation receipt leaked across tenants'; end if;
 if (select count(*) from public.governance_products)<>1 then raise exception 'Own tenant product unavailable'; end if;
 begin
  perform public.replace_governance_product('a0000000-0000-4000-8000-000000000002','a2000000-0000-4000-8000-000000000002','a1000000-0000-4000-8000-000000000002','{"source_system":"payments","contract":{"version":"v2"}}','Direct authenticated mutation');
  raise exception 'Authenticated RPC execution allowed';
 exception when insufficient_privilege then null; end;
end $$;
reset role;
rollback;
