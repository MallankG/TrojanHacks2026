-- Semantic policy search must only compare vectors from the same embedding model, inside pgvector
-- (HNSW index), instead of pulling every embedding into the API. Adds filter_embedding_model; a
-- new parameter needs drop + create (an overload would make PostgREST calls ambiguous).

drop function if exists public.match_policy_chunks(extensions.vector, text, integer, uuid, double precision, double precision, integer);

create function public.match_policy_chunks(
  query_embedding extensions.vector(768),
  query_text text,
  match_count integer default 8,
  filter_tenant_id uuid default null,
  full_text_weight double precision default 1.0,
  semantic_weight double precision default 1.0,
  rrf_k integer default 50,
  filter_embedding_model text default null
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
    where query_text is not null
      and e.fts @@ websearch_to_tsquery('english', query_text)
    order by rank_ix
    limit least(match_count, 30) * 2
  ),
  semantic as (
    select e.id,
      row_number() over (order by e.embedding operator(extensions.<=>) query_embedding) as rank_ix
    from eligible e
    where query_embedding is not null
      and e.embedding is not null
      and (filter_embedding_model is null or e.embedding_model = filter_embedding_model)
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
    case when c.embedding is null or query_embedding is null
           or (filter_embedding_model is not null and c.embedding_model is distinct from filter_embedding_model)
      then null
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

revoke execute on function public.match_policy_chunks(extensions.vector, text, integer, uuid, double precision, double precision, integer, text) from public, anon;
grant execute on function public.match_policy_chunks(extensions.vector, text, integer, uuid, double precision, double precision, integer, text) to authenticated, service_role;
