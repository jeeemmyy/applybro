-- ApplyBro schema v2 — MULTI-USER. Run in the Supabase SQL editor.
-- Replaces v1 (schema.sql): every table becomes per-user (user_id +
-- composite primary keys), RLS restricts each row to its owner, and two new
-- tables hold what used to live in local files: user_settings (AI provider,
-- API keys, models) and resumes (content.yaml + profile.yaml text).
--
-- Safe to run on a v1 database: the v1 tables are dropped (they are empty —
-- v1 was never successfully migrated into because RLS blocked it).

drop table if exists companies;
drop table if exists jobs;
drop table if exists kv;
drop table if exists applications;
drop table if exists email_log;
drop table if exists tailored_resumes;

create table companies (
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  key        text not null,               -- careers-page host, or lowercased name
  data       jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (user_id, key)
);

create table jobs (
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  url        text not null,               -- the job posting URL
  data       jsonb not null,              -- the full queue entry
  updated_at timestamptz not null default now(),
  primary key (user_id, url)
);

-- Small per-user singletons: ui_state, queue_meta.
create table kv (
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  key        text not null,
  data       jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (user_id, key)
);

-- The applications ledger (what jobs the user actually applied to).
create table applications (
  id           bigint generated always as identity primary key,
  user_id      uuid not null default auth.uid() references auth.users (id) on delete cascade,
  company      text not null default '',
  title        text not null default '',
  url          text not null default '',
  location     text not null default '',
  fit          text not null default '',
  resume_file  text not null default '',
  date_applied text not null default '',
  status       text not null default 'applied',
  created_at   timestamptz not null default now()
);
create index applications_user_idx on applications (user_id, id);

-- Gmail outcome classifications. Append-only, deduped per user by Gmail id.
create table email_log (
  user_id        uuid not null default auth.uid() references auth.users (id) on delete cascade,
  gmail_id       text not null,
  date           text not null default '',
  company        text not null default '',
  classification text not null default '',
  sender         text not null default '',
  subject        text not null default '',
  matched_by     text not null default '',
  created_at     timestamptz not null default now(),
  primary key (user_id, gmail_id)
);

-- Tailored resume PDFs, one per job per user, auto-expired after 24 hours.
create table tailored_resumes (
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  job_url    text not null,
  filename   text not null,
  pdf_base64 text not null,
  created_at timestamptz not null default now(),
  primary key (user_id, job_url)
);

-- Per-user app settings: {"ai": {"provider": ..., "openai_api_key": ...,
-- "models": {...}}} — what used to live in the local config.yaml.
create table user_settings (
  user_id    uuid primary key default auth.uid() references auth.users (id) on delete cascade,
  data       jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

-- The resume master + application profile (raw YAML text — the local files
-- become synced caches of these).
create table resumes (
  user_id      uuid primary key default auth.uid() references auth.users (id) on delete cascade,
  content_yaml text not null default '',
  profile_yaml text not null default '',
  updated_at   timestamptz not null default now()
);

-- ------------------------------------------------------------------- RLS
-- Owner-only access on every table; the publishable key alone (no signed-in
-- user) can neither read nor write anything.
alter table companies        enable row level security;
alter table jobs             enable row level security;
alter table kv               enable row level security;
alter table applications     enable row level security;
alter table email_log        enable row level security;
alter table tailored_resumes enable row level security;
alter table user_settings    enable row level security;
alter table resumes          enable row level security;

create policy "own rows" on companies        for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own rows" on jobs             for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own rows" on kv               for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own rows" on applications     for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own rows" on email_log        for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own rows" on tailored_resumes for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own rows" on user_settings    for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own rows" on resumes          for all using (user_id = auth.uid()) with check (user_id = auth.uid());

-- 24h TTL for tailored resumes (the app also purges lazily on upload).
-- Wrapped so a re-run doesn't fail if the job already exists.
create extension if not exists pg_cron;
do $$
begin
  perform cron.unschedule('purge-tailored-resumes');
exception when others then null;
end $$;
select cron.schedule(
  'purge-tailored-resumes', '*/30 * * * *',
  $$delete from tailored_resumes where created_at < now() - interval '24 hours'$$
);
