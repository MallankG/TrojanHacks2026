-- Storage buckets, realtime publication and hybrid policy search.

-- ---------------------------------------------------------------------------
-- Storage: private buckets. Object paths must start with '<tenant_id>/'.
-- Uploads happen through the backend (service role); browsers may only read their tenant's files.
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values
  ('source-files', 'source-files', false, 52428800, array['text/csv', 'application/vnd.ms-excel', 'text/plain']),
  ('case-exports', 'case-exports', false, 52428800, null),
  ('model-artifacts', 'model-artifacts', false, 209715200, null)
on conflict (id) do nothing;

create policy "Tenant members can read their source files and case exports" on storage.objects
  for select to authenticated
  using (
    bucket_id in ('source-files', 'case-exports')
    and (storage.foldername(name))[1] = (select private.current_tenant_id())::text
  );

-- ---------------------------------------------------------------------------
-- Realtime: live progress for ingestion, agent runs, narratives and the alert queue (RLS applies).
-- ---------------------------------------------------------------------------
do $$
begin
  if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    alter publication supabase_realtime add table
      public.ingestion_runs, public.agent_runs, public.case_narratives, public.alerts;
  end if;
end;
$$;

-- ---------------------------------------------------------------------------
-- Hybrid (keyword + vector) policy retrieval with reciprocal rank fusion.
-- security invoker: browser callers are filtered by RLS; the backend (service role)
-- passes filter_tenant_id; without it only the shared public corpus is searched.
-- ---------------------------------------------------------------------------
create function public.match_policy_chunks(
  query_embedding extensions.vector(768),
  query_text text,
  match_count integer default 8,
  filter_tenant_id uuid default null,
  full_text_weight double precision default 1.0,
  semantic_weight double precision default 1.0,
  rrf_k integer default 50
)
returns table (
  chunk_id uuid,
  document_id uuid,
  title text,
  publisher text,
  source_url text,
  section text,
  content text,
  similarity double precision,
  rrf_score double precision
)
language sql
stable
security invoker
set search_path = ''
as $$
  with eligible as (
    select c.*
    from public.policy_chunks c
    where c.tenant_id is null or c.tenant_id = filter_tenant_id
  ),
  full_text as (
    select e.id,
      row_number() over (order by ts_rank_cd(e.fts, websearch_to_tsquery('english', query_text)) desc) as rank_ix
    from eligible e
    where e.fts @@ websearch_to_tsquery('english', query_text)
    order by rank_ix
    limit least(match_count, 30) * 2
  ),
  semantic as (
    select e.id,
      row_number() over (order by e.embedding operator(extensions.<=>) query_embedding) as rank_ix
    from eligible e
    where e.embedding is not null
    order by rank_ix
    limit least(match_count, 30) * 2
  )
  select
    c.id,
    c.document_id,
    d.title,
    d.publisher,
    d.source_url,
    c.section,
    c.content,
    case when c.embedding is null then null
      else 1 - (c.embedding operator(extensions.<=>) query_embedding) end,
    coalesce(1.0 / (rrf_k + ft.rank_ix), 0.0) * full_text_weight
      + coalesce(1.0 / (rrf_k + s.rank_ix), 0.0) * semantic_weight
  from full_text ft
  full outer join semantic s on s.id = ft.id
  join public.policy_chunks c on c.id = coalesce(ft.id, s.id)
  join public.policy_documents d on d.id = c.document_id
  order by 9 desc
  limit least(match_count, 30);
$$;

revoke execute on function public.match_policy_chunks(extensions.vector, text, integer, uuid, double precision, double precision, integer) from public, anon;
grant execute on function public.match_policy_chunks(extensions.vector, text, integer, uuid, double precision, double precision, integer) to authenticated, service_role;
