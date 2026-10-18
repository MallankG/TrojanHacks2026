-- Run against a disposable/local Supabase database after migrations. Entire test rolls back.
begin;
insert into public.tenants(id,slug,name) values
 ('f0000000-0000-4000-8000-000000000001','enterprise-test-a','Enterprise RLS A'),
 ('f0000000-0000-4000-8000-000000000002','enterprise-test-b','Enterprise RLS B');
insert into auth.users(id) values
 ('f1000000-0000-4000-8000-000000000001'),('f1000000-0000-4000-8000-000000000002'),
 ('f1000000-0000-4000-8000-000000000003'),('f1000000-0000-4000-8000-000000000004');
insert into public.profiles(id,tenant_id,role) values
 ('f1000000-0000-4000-8000-000000000001','f0000000-0000-4000-8000-000000000001','investigator'),
 ('f1000000-0000-4000-8000-000000000002','f0000000-0000-4000-8000-000000000001','investigator'),
 ('f1000000-0000-4000-8000-000000000003','f0000000-0000-4000-8000-000000000001','investigator'),
 ('f1000000-0000-4000-8000-000000000004','f0000000-0000-4000-8000-000000000002','investigator')
on conflict(id) do update set tenant_id=excluded.tenant_id, role=excluded.role;
insert into public.cases(id,tenant_id,title,created_by,assigned_to) values
 ('f2000000-0000-4000-8000-000000000001','f0000000-0000-4000-8000-000000000001','Enterprise fixture','f1000000-0000-4000-8000-000000000001','f1000000-0000-4000-8000-000000000001');
insert into public.investigator_attributes(id,tenant_id,region,clearance,purposes) values
 ('f1000000-0000-4000-8000-000000000001','f0000000-0000-4000-8000-000000000001','US','restricted',array['investigation']),
 ('f1000000-0000-4000-8000-000000000002','f0000000-0000-4000-8000-000000000001','US','restricted',array['investigation']),
 ('f1000000-0000-4000-8000-000000000003','f0000000-0000-4000-8000-000000000001','EU','restricted',array['investigation']),
 ('f1000000-0000-4000-8000-000000000004','f0000000-0000-4000-8000-000000000002','US','restricted',array['investigation']);
insert into public.case_access_policies(case_id,tenant_id,region,classification,purpose,review_at,updated_by) values
 ('f2000000-0000-4000-8000-000000000001','f0000000-0000-4000-8000-000000000001','US','confidential','investigation',now()+interval '7 days','f1000000-0000-4000-8000-000000000001');
insert into public.case_collaborators(tenant_id,case_id,user_id,case_role,created_by) values
 ('f0000000-0000-4000-8000-000000000001','f2000000-0000-4000-8000-000000000001','f1000000-0000-4000-8000-000000000002','observer','f1000000-0000-4000-8000-000000000001'),
 ('f0000000-0000-4000-8000-000000000001','f2000000-0000-4000-8000-000000000001','f1000000-0000-4000-8000-000000000003','investigator','f1000000-0000-4000-8000-000000000001');
select public.append_case_room_event('f0000000-0000-4000-8000-000000000001','f2000000-0000-4000-8000-000000000001','f1000000-0000-4000-8000-000000000001','f3000000-0000-4000-8000-000000000001',0,'hypothesis','{"kind":"hypothesis","text":"Synthetic lead"}',repeat('a',64),'rls-test');
-- Same actor/client key returns the original event even with the original expected revision.
select public.append_case_room_event('f0000000-0000-4000-8000-000000000001','f2000000-0000-4000-8000-000000000001','f1000000-0000-4000-8000-000000000001','f3000000-0000-4000-8000-000000000001',0,'hypothesis','{"kind":"hypothesis","text":"Synthetic lead"}',repeat('a',64),'rls-retry');
do $$ begin
 if (select count(*) from public.case_room_events where case_id='f2000000-0000-4000-8000-000000000001')<>1 then raise exception 'Retry duplicated event'; end if;
 begin
  perform public.append_case_room_event('f0000000-0000-4000-8000-000000000001','f2000000-0000-4000-8000-000000000001','f1000000-0000-4000-8000-000000000001','f3000000-0000-4000-8000-000000000002',0,'hypothesis','{"kind":"hypothesis","text":"Conflict"}',repeat('b',64),'rls-conflict');
  raise exception 'Stale revision accepted';
 exception when unique_violation then null; end;
 begin
  perform public.append_case_room_event('f0000000-0000-4000-8000-000000000001','f2000000-0000-4000-8000-000000000001','f1000000-0000-4000-8000-000000000002','f3000000-0000-4000-8000-000000000003',1,'comment','{"kind":"comment","text":"Observer mutation"}',repeat('c',64),'rls-observer');
  raise exception 'Observer write accepted';
 exception when insufficient_privilege then null; end;
end $$;
insert into storage.objects(bucket_id,name) values('case-exports','f0000000-0000-4000-8000-000000000001/cases/f2000000-0000-4000-8000-000000000001/test.json');
set local role authenticated;
select set_config('request.jwt.claim.sub','f1000000-0000-4000-8000-000000000001',true);
do $$ begin
 if (select count(*) from public.cases where id='f2000000-0000-4000-8000-000000000001')<>1 then raise exception 'Lead case read denied'; end if;
 if (select count(*) from public.case_room_events where case_id='f2000000-0000-4000-8000-000000000001')<>1 then raise exception 'Lead room read denied'; end if;
end $$;
do $$ begin
 if exists(select 1 from storage.objects where bucket_id='case-exports') then raise exception 'Export did not require step-up'; end if;
end $$;
select set_config('request.jwt.claims','{"aal":"aal2"}',true);
do $$ begin
 if (select count(*) from storage.objects where bucket_id='case-exports')<>1 then raise exception 'Authorized export read denied'; end if;
end $$;
select set_config('request.jwt.claim.sub','f1000000-0000-4000-8000-000000000002',true);
do $$ begin
 if exists(select 1 from storage.objects where bucket_id='case-exports') then raise exception 'Observer accessed sensitive export'; end if;
end $$;
select set_config('request.jwt.claim.sub','f1000000-0000-4000-8000-000000000003',true);
do $$ begin
 if exists(select 1 from public.cases where id='f2000000-0000-4000-8000-000000000001') then raise exception 'Region mismatch leaked case'; end if;
 if exists(select 1 from public.case_room_events where case_id='f2000000-0000-4000-8000-000000000001') then raise exception 'Region mismatch leaked room'; end if;
end $$;
select set_config('request.jwt.claim.sub','f1000000-0000-4000-8000-000000000004',true);
do $$ begin
 if exists(select 1 from public.cases where id='f2000000-0000-4000-8000-000000000001') then raise exception 'Cross-tenant case leaked'; end if;
 if exists(select 1 from public.case_room_events where case_id='f2000000-0000-4000-8000-000000000001') then raise exception 'Cross-tenant room leaked'; end if;
end $$;
reset role;
rollback;
