-- PRD 20.10. Provision user attributes and case policies before enabling enterprise access.
create unique index if not exists profiles_tenant_identity on public.profiles(tenant_id, id);
create table public.investigator_attributes (
  id uuid primary key,
  tenant_id uuid not null references public.tenants(id),
  region text not null,
  clearance text not null check (clearance in ('internal','confidential','restricted')),
  purposes text[] not null,
  updated_at timestamptz not null default now(),
  foreign key (tenant_id,id) references public.profiles(tenant_id,id)
);
create table public.case_access_policies (
  case_id uuid primary key,
  tenant_id uuid not null references public.tenants(id),
  region text not null,
  classification text not null check (classification in ('internal','confidential','restricted')),
  purpose text not null,
  review_at timestamptz not null,
  updated_by uuid not null,
  updated_at timestamptz not null default now(),
  foreign key (tenant_id,case_id) references public.cases(tenant_id,id)
);
create table public.case_collaborators (
  tenant_id uuid not null,
  case_id uuid not null,
  user_id uuid not null,
  case_role text not null check (case_role in ('lead','investigator','reviewer','observer')),
  created_by uuid not null,
  updated_at timestamptz not null default now(),
  primary key(case_id,user_id),
  foreign key (tenant_id,case_id) references public.cases(tenant_id,id),
  foreign key (tenant_id,user_id) references public.profiles(tenant_id,id)
);
create table public.case_room_events (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null,
  case_id uuid not null,
  actor_id uuid not null,
  client_id uuid not null,
  revision integer not null,
  kind text not null check (kind in ('comment','assignment','bookmark','hypothesis','vote','review_request','conflict','resolution')),
  payload jsonb not null,
  payload_hash text not null,
  request_id text,
  config_version text not null default 'enterprise-v1',
  created_at timestamptz not null default now(),
  unique(case_id,revision),
  unique(case_id,actor_id,client_id),
  foreign key (tenant_id,case_id) references public.cases(tenant_id,id),
  foreign key (tenant_id,actor_id) references public.profiles(tenant_id,id)
);
create table public.service_metrics (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  operation text not null check (operation in ('ingestion_delay','score_latency','graph_query','copilot_verification','policy_retrieval','evidence_export')),
  duration_ms double precision not null check(duration_ms >= 0 and duration_ms <= 604800000),
  success boolean not null,
  source_ref text not null,
  observed_at timestamptz not null,
  created_at timestamptz not null default now()
);
create index service_metrics_tenant_time on public.service_metrics(tenant_id,observed_at desc);
create table public.drift_reports (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id),
  created_by uuid,
  report jsonb not null,
  created_at timestamptz not null default now()
);

create function private.enterprise_member(p_tenant uuid, p_case uuid, p_user uuid)
returns boolean language sql stable security definer set search_path = '' as $$
 select exists (
  select 1 from public.cases c
  join public.case_access_policies p on p.case_id=c.id and p.tenant_id=c.tenant_id
  join public.investigator_attributes a on a.id=p_user and a.tenant_id=c.tenant_id
  join public.profiles u on u.id=p_user and u.tenant_id=c.tenant_id
  left join public.case_collaborators m on m.case_id=c.id and m.user_id=p_user and m.tenant_id=c.tenant_id
  where c.id=p_case and c.tenant_id=p_tenant and a.region=p.region
    and p.purpose=any(a.purposes)
    and array_position(array['internal','confidential','restricted'], a.clearance) >= array_position(array['internal','confidential','restricted'],p.classification)
    and (m.user_id is not null or c.assigned_to=p_user or c.created_by=p_user)
 );
$$;
create function private.enterprise_case_visible(p_tenant uuid, p_case uuid)
returns boolean language sql stable security definer set search_path = '' as $$
 select p_tenant=(select private.current_tenant_id())
  and private.enterprise_member(p_tenant,p_case,(select auth.uid()));
$$;

-- Replace, rather than OR with, the earlier tenant-wide policies on case material.
do $$ declare t text; col text; begin
 foreach t in array array['cases','case_alerts','case_evidence','agent_runs','case_narratives','case_decisions'] loop
  execute format('drop policy if exists %I on public.%I','Tenant members can read '||t,t);
  col := case when t='cases' then 'id' else 'case_id' end;
  execute format('create policy "Case scoped reads" on public.%I for select to authenticated using(private.enterprise_case_visible(tenant_id,%I))',t,col);
 end loop;
 foreach t in array array['narrative_claims','narrative_citations'] loop
  execute format('drop policy if exists %I on public.%I','Tenant members can read '||t,t);
  execute format('create policy "Narrative scoped reads" on public.%I for select to authenticated using(exists(select 1 from public.case_narratives n where n.id=%I.narrative_id and n.tenant_id=%I.tenant_id and private.enterprise_case_visible(n.tenant_id,n.case_id)))',t,t,t);
 end loop;
end $$;

do $$ declare t text; begin
 foreach t in array array['investigator_attributes','case_access_policies','case_collaborators','case_room_events','service_metrics','drift_reports'] loop
  execute format('alter table public.%I enable row level security',t);
  execute format('revoke all on public.%I from anon, authenticated',t);
  execute format('grant select on public.%I to authenticated',t);
  execute format('grant all on public.%I to service_role',t);
 end loop;
end $$;
create policy "Own attributes" on public.investigator_attributes for select to authenticated using(id=(select auth.uid()) and tenant_id=(select private.current_tenant_id()));
create policy "Case policy reads" on public.case_access_policies for select to authenticated using(private.enterprise_member(tenant_id,case_id,(select auth.uid())));
create policy "Case collaborator reads" on public.case_collaborators for select to authenticated using(private.enterprise_member(tenant_id,case_id,(select auth.uid())));
create policy "Case room reads" on public.case_room_events for select to authenticated using(private.enterprise_member(tenant_id,case_id,(select auth.uid())));
create policy "Tenant operation metrics" on public.service_metrics for select to authenticated using(tenant_id=(select private.current_tenant_id()));
create policy "Tenant drift reports" on public.drift_reports for select to authenticated using(tenant_id=(select private.current_tenant_id()));
create trigger case_room_events_immutable before update or delete on public.case_room_events for each row execute function private.prevent_audit_mutation();
create trigger drift_reports_immutable before update or delete on public.drift_reports for each row execute function private.prevent_audit_mutation();

create function public.append_case_room_event(p_tenant uuid,p_case uuid,p_actor uuid,p_client uuid,p_expected integer,p_kind text,p_payload jsonb,p_hash text,p_request text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare previous public.case_room_events; inserted public.case_room_events; rev integer; role_name text; begin
 perform pg_advisory_xact_lock(hashtextextended(p_case::text,0));
 if not private.enterprise_member(p_tenant,p_case,p_actor) then raise exception 'Case access denied' using errcode='42501'; end if;
 select coalesce(m.case_role,'lead') into role_name from public.cases c left join public.case_collaborators m on m.case_id=c.id and m.user_id=p_actor where c.id=p_case;
 if role_name='observer' or not exists(select 1 from public.profiles where id=p_actor and tenant_id=p_tenant and role::text in ('admin','investigator','analyst','reviewer')) then raise exception 'Read-only role' using errcode='42501'; end if;
 if p_kind in ('assignment','resolution') and (role_name not in ('lead','reviewer') or not exists(select 1 from public.profiles where id=p_actor and tenant_id=p_tenant and role::text in ('admin','investigator','reviewer'))) then raise exception 'Lead required' using errcode='42501'; end if;
 select * into previous from public.case_room_events where case_id=p_case and actor_id=p_actor and client_id=p_client;
 if found then
  if previous.payload_hash<>p_hash or previous.payload<>p_payload then raise exception 'Idempotency key reused with different payload' using errcode='23505'; end if;
  return to_jsonb(previous);
 end if;
 select coalesce(max(revision),0) into rev from public.case_room_events where case_id=p_case;
 if rev<>p_expected then raise exception 'Case changed; reload the room and resolve the conflict' using errcode='23505'; end if;
 insert into public.case_room_events(tenant_id,case_id,actor_id,client_id,revision,kind,payload,payload_hash,request_id)
 values(p_tenant,p_case,p_actor,p_client,rev+1,p_kind,p_payload,p_hash,p_request) returning * into inserted;
 if p_kind='assignment' then update public.cases set assigned_to=(p_payload->>'target_user_id')::uuid where tenant_id=p_tenant and id=p_case; end if;
 perform private.write_audit_event(p_tenant,p_actor,'user','collaboration.recorded','case',p_case::text,jsonb_build_object('event_id',inserted.id,'revision',rev+1,'kind',p_kind,'payload_hash',p_hash,'request_id',p_request,'result','succeeded','config_version','enterprise-v1'));
 return to_jsonb(inserted);
end $$;
revoke all on function public.append_case_room_event(uuid,uuid,uuid,uuid,integer,text,jsonb,text,text) from public,anon,authenticated;
grant execute on function public.append_case_room_event(uuid,uuid,uuid,uuid,integer,text,jsonb,text,text) to service_role;

create function public.list_authorized_cases(p_tenant uuid,p_actor uuid,p_status text,p_assigned boolean,p_limit integer,p_offset integer)
returns jsonb language sql stable security definer set search_path='' as $$
 with allowed as (
  select c.* from public.cases c where c.tenant_id=p_tenant
   and (p_status is null or c.status::text=p_status)
   and (not p_assigned or c.assigned_to=p_actor)
   and private.enterprise_member(p_tenant,c.id,p_actor)
 ), limited as (select * from allowed order by updated_at desc limit greatest(1,least(p_limit,500)) offset greatest(p_offset,0))
 select jsonb_build_object('items',coalesce((select jsonb_agg(to_jsonb(limited)) from limited),'[]'::jsonb),'total',(select count(*) from allowed),'limit',p_limit,'offset',p_offset);
$$;
revoke all on function public.list_authorized_cases(uuid,uuid,text,boolean,integer,integer) from public,anon,authenticated;
grant execute on function public.list_authorized_cases(uuid,uuid,text,boolean,integer,integer) to service_role;

create function private.audit_enterprise_control()
returns trigger language plpgsql security definer set search_path='' as $$ begin
 perform private.write_audit_event(new.tenant_id, private.current_actor(), 'user', 'enterprise.control_changed', tg_table_name,
  coalesce(to_jsonb(new)->>'case_id',to_jsonb(new)->>'id'),
  jsonb_build_object('payload_hash',encode(sha256(convert_to(to_jsonb(new)::text,'UTF8')),'hex'),'result','succeeded','config_version','enterprise-v1'));
 return new;
end $$;
create trigger enterprise_attributes_audit after insert or update on public.investigator_attributes for each row execute function private.audit_enterprise_control();
create trigger enterprise_policies_audit after insert or update on public.case_access_policies for each row execute function private.audit_enterprise_control();
create trigger enterprise_collaborators_audit after insert or update on public.case_collaborators for each row execute function private.audit_enterprise_control();

-- Private Storage exports must not retain the old tenant-wide read bypass.
create function private.enterprise_export_visible(object_name text)
returns boolean language plpgsql stable security definer set search_path='' as $$
declare tid text; cid text; uid uuid; begin
 tid:=split_part(object_name,'/',1); cid:=split_part(object_name,'/',3); uid:=(select auth.uid());
 if split_part(object_name,'/',2)<>'cases' or tid!~'^[0-9a-fA-F-]{36}$' or cid!~'^[0-9a-fA-F-]{36}$' then return false; end if;
 if tid::uuid<>(select private.current_tenant_id()) or (select auth.jwt()->>'aal') is distinct from 'aal2'
    or not private.enterprise_member(tid::uuid,cid::uuid,uid) then return false; end if;
 return exists(select 1 from public.cases c join public.profiles u on u.id=uid and u.tenant_id=c.tenant_id
  left join public.case_collaborators m on m.case_id=c.id and m.user_id=uid
  where c.id=cid::uuid and c.tenant_id=tid::uuid and u.role::text in ('admin','investigator','reviewer')
    and coalesce(m.case_role,case when c.assigned_to=uid or c.created_by=uid then 'lead' end) in ('lead','reviewer'));
exception when invalid_text_representation then return false;
end $$;
drop policy if exists "Tenant members can read their source files and case exports" on storage.objects;
create policy "Tenant source reads" on storage.objects for select to authenticated
 using(bucket_id='source-files' and split_part(name,'/',1)=(select private.current_tenant_id())::text);
create policy "Case scoped export reads" on storage.objects for select to authenticated
 using(bucket_id='case-exports' and private.enterprise_export_visible(name));
