// Full application-schema migration check. Only Supabase-owned auth/storage are fixtures.
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';
import { readFileSync, readdirSync } from 'node:fs';
const runtimeDir = process.argv[2] ? path.resolve(process.argv[2]) : null;
const load = (packageName, entry, specifier = packageName) => import(runtimeDir
  ? pathToFileURL(path.join(runtimeDir, packageName, entry)).href : specifier);
const { PGlite } = await load('@electric-sql/pglite', 'dist/index.js');
const { vector } = await load('@electric-sql/pglite-pgvector', 'dist/index.js');
const { pg_trgm } = await load('@electric-sql/pglite', 'dist/contrib/pg_trgm.js', '@electric-sql/pglite/contrib/pg_trgm');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const db = new PGlite({ extensions: { vector, pg_trgm } });
let stage = 'Supabase platform fixtures';
try {
  await db.exec(`
    create role anon; create role authenticated; create role service_role bypassrls;
    create schema auth; create schema storage; create schema extensions;
    grant usage on schema auth,storage,extensions,public to authenticated,service_role;
    alter default privileges in schema public grant all on tables to service_role;
    alter default privileges in schema public grant all on sequences to service_role;
    alter default privileges in schema public grant select on tables to authenticated;
    create table auth.users(id uuid primary key,email text,raw_user_meta_data jsonb default '{}');
    create function auth.uid() returns uuid language sql stable as $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;
    create function auth.jwt() returns jsonb language sql stable as $$select coalesce(nullif(current_setting('request.jwt.claims',true),''),'{}')::jsonb$$;
    create table storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint,allowed_mime_types text[]);
    create table storage.objects(id uuid primary key default gen_random_uuid(),bucket_id text references storage.buckets(id),name text);
    alter table storage.objects enable row level security;
    grant select on storage.objects to authenticated;
    create function storage.foldername(name text) returns text[] language sql immutable as $$select string_to_array(name,'/')$$;
    create publication supabase_realtime;
  `);
  const migrations = readdirSync(path.join(root, 'supabase/migrations')).filter(n => n.endsWith('.sql')).sort();
  for (const migration of migrations) {
    stage = migration;
    await db.exec(readFileSync(path.join(root, 'supabase/migrations', migration), 'utf8'));
    console.log(`Applied ${migration}`);
  }
  for (const test of ['enterprise_rls.sql', 'contract_versions.sql']) {
    stage = test;
    await db.exec(readFileSync(path.join(root, 'supabase/tests', test), 'utf8'));
    console.log(`Passed ${test}`);
  }
  console.log(`All ${migrations.length} application migrations passed unchanged, including pgvector, pg_trgm and Realtime publication setup.`);
} catch (error) {
  console.error(`Failed at ${stage}: ${error.message} (${error.code ?? 'unknown'}) ${error.where ?? ''}`);
  process.exitCode = 1;
} finally {
  await db.close();
}
