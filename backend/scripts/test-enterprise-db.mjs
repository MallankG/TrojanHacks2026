import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
const runtime = process.argv[2] ? pathToFileURL(path.resolve(process.argv[2])).href : '@electric-sql/pglite';
const { PGlite } = await import(runtime);
import {readFileSync} from 'node:fs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const db=new PGlite();
await db.exec(`
create role anon; create role authenticated; create role service_role bypassrls;
create schema auth; create schema private; create schema storage;
grant usage on schema auth,private,public to authenticated,service_role;
create table auth.users(id uuid primary key,email text,raw_user_meta_data jsonb default '{}');
create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;
create function auth.jwt() returns jsonb language sql stable as $$select coalesce(nullif(current_setting('request.jwt.claims',true),''),'{}')::jsonb$$;
create table storage.objects(id uuid primary key default gen_random_uuid(),bucket_id text,name text);
alter table storage.objects enable row level security;
grant usage on schema storage to authenticated; grant select on storage.objects to authenticated;
`);
// Apply the actual foundation minus extensions not relevant to case authorization.
let foundation=readFileSync(root+'/supabase/migrations/20261017000001_foundation_tenancy.sql','utf8').replace(/create extension[^;]+;/g,'');
await db.exec(foundation);
// Financial/evidence stubs preserve the same keys/columns required by migration 20.10.
await db.exec(`
create table public.cases(id uuid primary key default gen_random_uuid(),tenant_id uuid not null references public.tenants(id),title text not null,created_by uuid,assigned_to uuid,status text default 'open',updated_at timestamptz default now(),unique(tenant_id,id));
create table public.case_alerts(tenant_id uuid,case_id uuid);
create table public.case_evidence(tenant_id uuid,case_id uuid);
create table public.agent_runs(tenant_id uuid,case_id uuid);
create table public.case_narratives(id uuid,tenant_id uuid,case_id uuid);
create table public.case_decisions(tenant_id uuid,case_id uuid);
create table public.narrative_claims(tenant_id uuid,narrative_id uuid);
create table public.narrative_citations(tenant_id uuid,narrative_id uuid);
create function private.prevent_audit_mutation() returns trigger language plpgsql as $$begin raise exception 'Append only' using errcode='42501'; end;$$;
create table public.test_audit(tenant_id uuid,actor_id uuid,action text,metadata jsonb);
create function private.write_audit_event(uuid,uuid,public.actor_type,text,text,text,jsonb) returns void language sql security definer as $$insert into public.test_audit values($1,$2,$4,$7)$$;
create function private.current_actor() returns uuid language sql stable as $$select auth.uid()$$;
grant select on all tables in schema public to authenticated;
do $$ declare t text; begin foreach t in array array['cases','case_alerts','case_evidence','agent_runs','case_narratives','case_decisions','narrative_claims','narrative_citations'] loop execute format('alter table public.%I enable row level security',t); end loop; end $$;
do $$ declare t text; begin foreach t in array array['cases','case_alerts','case_evidence','agent_runs','case_narratives','case_decisions','narrative_claims','narrative_citations'] loop execute format('create policy %I on public.%I for select to authenticated using(tenant_id=(select private.current_tenant_id()))','Tenant members can read '||t,t); end loop; end $$;
create policy "Tenant members can read their source files and case exports" on storage.objects for select to authenticated using(split_part(name,'/',1)=(select private.current_tenant_id())::text);
`);
await db.exec(readFileSync(root+'/supabase/migrations/20261018000002_enterprise_collaboration.sql','utf8'));
await db.exec(readFileSync(root+'/supabase/tests/enterprise_rls.sql','utf8'));
console.log('Enterprise migration and PostgreSQL RLS / observer denial / idempotency / stale-revision tests passed on isolated case-schema fixtures.');
await db.close();
