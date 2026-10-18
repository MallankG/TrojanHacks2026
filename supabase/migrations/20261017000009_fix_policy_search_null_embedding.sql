-- When no query embedding is supplied (embedding model unavailable), the semantic branch must
-- return nothing instead of ranking chunks in arbitrary order. Retrieval then degrades to keyword search.

create or replace function public.match_policy_chunks(
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
    case when c.embedding is null or query_embedding is null then null
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
