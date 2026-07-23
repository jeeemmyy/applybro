-- ApplyBro Supabase schema. Run ONCE in the Supabase dashboard SQL editor
-- (or `supabase db push` once the CLI is wired up).
--
-- Design notes:
-- * companies/jobs/kv keep their JSON-file shape in a jsonb column — this is
--   a lift-and-shift of the local files, not a remodel. When auth/multi-user
--   lands, add a user_id column + RLS policies to every table.
-- * RLS stays DISABLED for now: the app authenticates with the project's
--   publishable key and there is exactly one user. Before any SaaS exposure,
--   enable RLS and move the backend to the secret key.

create table if not exists companies (
  key        text primary key,          -- careers-page host, or lowercased name
  data       jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table if not exists jobs (
  url        text primary key,          -- the job posting URL
  data       jsonb not null,            -- the full queue.json entry
  updated_at timestamptz not null default now()
);

-- Small singletons: ui_state, queue_meta.
create table if not exists kv (
  key        text primary key,
  data       jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

-- The applications ledger (was the "Applications" tab of the Google Sheet).
create table if not exists applications (
  id           bigint generated always as identity primary key,
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

-- Gmail outcome classifications (was the "Email Log" tab). Append-only,
-- deduplicated by Gmail message id.
create table if not exists email_log (
  gmail_id       text primary key,
  date           text not null default '',
  company        text not null default '',
  classification text not null default '',
  sender         text not null default '',
  subject        text not null default '',
  matched_by     text not null default '',
  created_at     timestamptz not null default now()
);

-- Tailored resume PDFs, one per job, auto-expired after 24 hours.
create table if not exists tailored_resumes (
  job_url    text primary key,
  filename   text not null,
  pdf_base64 text not null,
  created_at timestamptz not null default now()
);

-- 24h TTL via pg_cron (every 30 min). The backend ALSO purges expired rows
-- lazily on every upload, so the TTL still holds if pg_cron is unavailable.
create extension if not exists pg_cron;
select cron.schedule(
  'purge-tailored-resumes', '*/30 * * * *',
  $$delete from tailored_resumes where created_at < now() - interval '24 hours'$$
);
