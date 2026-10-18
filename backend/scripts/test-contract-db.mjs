import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import { readFileSync } from 'node:fs';
const runtime = process.argv[2] ? pathToFileURL(path.resolve(process.argv[2])).href : '@electric-sql/pglite';
const { PGlite } = await import(runtime);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = (name) => readFileSync(path.join(root, 'supabase', name), 'utf8');
const db = new PGlite();
try {
  await db.exec(`
    create role anon; create role authenticated; create role service_role bypassrls;
    create schema auth;
    create table auth.users(id uuid primary key,email text,raw_user_meta_data jsonb default '{}');
    create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;
    grant usage on schema auth,public to authenticated,service_role;
  `);
  await db.exec(read('migrations/20261017000001_foundation_tenancy.sql').replace(/create extension[^;]+;/g, ''));
  // Minimal ingestion fixture; tenant identity and audit functions below are actual migrations.
  await db.exec(`create table public.ingestion_runs(id uuid primary key, tenant_id uuid not null references public.tenants(id));
    grant all on public.ingestion_runs to service_role;`);
  await db.exec(read('migrations/20261017000006_audit.sql').split('create function private.actor_kind')[0]);
  await db.exec(read('migrations/20261018000001_governance_superpowers.sql'));
  await db.exec(read('migrations/20261018000003_source_contract_versions.sql'));
  await db.exec(read('tests/contract_versions.sql'));
  console.log('Contract PostgreSQL tests passed: replacement, audit atomicity, stale revision, immutable history/pins, tenant isolation and RPC privileges.');
} catch (error) {
  console.error(error.message, error.code, error.where ?? "");
  process.exitCode = 1;
} finally {
  await db.close();
}
