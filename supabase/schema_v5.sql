-- ApplyBro schema v5 — CLAUDE RUNNER QUEUE (SaaS Phase 6, MVP-505).
-- Run in the Supabase SQL editor after v4. Additive.
--
-- SCAFFOLD, activated at HOSTING time (docs/DEPLOY.md). While the backend
-- runs locally on the admin's Mac, claude_cli runs in-process and this queue
-- is unused. Once the backend is hosted it can't shell out to Claude, so AI
-- jobs for users whose policy routes to claude_cli are ENQUEUED here; a small
-- runner on the admin's Mac claims them, runs Claude CLI in an isolated temp
-- dir, and posts the result back. OpenAI users never touch this queue.
--
-- Privacy: rows hold the prompt + result only until the job completes; a
-- pg_cron purge removes finished/stale rows so payloads don't linger.

create table if not exists ai_jobs (
  id          bigint generated always as identity primary key,
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  feature     text not null default '',
  model       text not null default '',
  prompt      text not null default '',
  status      text not null default 'queued'
              check (status in ('queued','claimed','done','error')),
  result      text not null default '',
  error       text not null default '',
  claimed_by  text not null default '',
  claimed_at  timestamptz null,
  created_at  timestamptz not null default now(),
  finished_at timestamptz null
);
create index if not exists ai_jobs_queue_idx on ai_jobs (status, created_at);

alter table ai_jobs enable row level security;
-- Owner may enqueue + read their own jobs; only an admin (the runner signs in
-- as the admin) may claim/complete across users.
drop policy if exists "own jobs" on ai_jobs;
create policy "own jobs" on ai_jobs
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());
drop policy if exists "admin runs jobs" on ai_jobs;
create policy "admin runs jobs" on ai_jobs
  for all using (is_admin()) with check (is_admin());

-- Runner health: a single heartbeat row the admin panel reads.
create table if not exists runner_status (
  id           int primary key default 1 check (id = 1),
  online       boolean not null default false,
  last_beat    timestamptz null,
  current_job  bigint null,
  note         text not null default ''
);
alter table runner_status enable row level security;
drop policy if exists "admin runner status" on runner_status;
create policy "admin runner status" on runner_status
  for all using (is_admin()) with check (is_admin());

-- Purge finished jobs after 1h and stale claims (a crashed runner) after 30m.
do $$ begin perform cron.unschedule('purge-ai-jobs'); exception when others then null; end $$;
select cron.schedule('purge-ai-jobs', '*/15 * * * *', $$
  delete from ai_jobs where status in ('done','error') and finished_at < now() - interval '1 hour';
  update ai_jobs set status='queued', claimed_by='', claimed_at=null
    where status='claimed' and claimed_at < now() - interval '30 minutes';
$$);
