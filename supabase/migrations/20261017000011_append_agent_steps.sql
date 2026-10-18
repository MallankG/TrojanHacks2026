-- Atomic append to agent_runs.steps so concurrent step reports from agent nodes cannot overwrite each other.
-- Only the backend (service role) may call it; tenant scoping is part of the WHERE clause.

create function public.append_agent_steps(
  p_run_id uuid,
  p_tenant_id uuid,
  p_steps jsonb,
  p_changes jsonb default '{}'::jsonb
)
returns setof public.agent_runs
language sql
security invoker
set search_path = ''
as $$
  update public.agent_runs
  set steps = steps || coalesce(p_steps, '[]'::jsonb),
      status = coalesce((p_changes ->> 'status')::public.agent_run_status, status),
      current_step = coalesce(p_changes ->> 'current_step', current_step),
      error_message = coalesce(p_changes ->> 'error_message', error_message),
      started_at = coalesce(started_at, (p_changes ->> 'started_at')::timestamptz),
      completed_at = coalesce((p_changes ->> 'completed_at')::timestamptz, completed_at)
  where id = p_run_id and tenant_id = p_tenant_id
  returning *;
$$;

revoke execute on function public.append_agent_steps(uuid, uuid, jsonb, jsonb) from public, anon, authenticated;
grant execute on function public.append_agent_steps(uuid, uuid, jsonb, jsonb) to service_role;
