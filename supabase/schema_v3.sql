-- ApplyBro schema v3 — ACCOUNTS + ADMIN + AI POLICY. Run in the Supabase SQL
-- editor AFTER schema_v2.sql. Additive: nothing from v2 is dropped.
--
-- What this adds (SaaS MVP Phase 1, docs/SAAS_MVP_PLAN.md):
--   profiles   — account status (pending -> active) + subscribed flag.
--                New sign-ups are PENDING and UNSUBSCRIBED until the admin
--                approves them; every AI request checks this.
--   admins     — who may approve users and edit AI policy. Seeded below.
--   ai_policy  — ADMIN-controlled per-user AI access: which providers are
--                allowed (Claude CLI is allow-listed, default off), primary/
--                fallback routing, per-task models, and the OpenAI key the
--                user's app is permitted to use. Users can read their own
--                row, never write it.
--   audit_log  — record of every admin change.

-- Helper: is the CURRENT user an admin? (security definer dodges RLS
-- recursion when policies on other tables call it.)
create table if not exists admins (
  user_id uuid primary key references auth.users (id) on delete cascade
);

create or replace function is_admin() returns boolean
language sql security definer stable as
$$ select exists (select 1 from admins where user_id = auth.uid()) $$;

create table if not exists profiles (
  user_id    uuid primary key references auth.users (id) on delete cascade,
  email      text not null default '',
  status     text not null default 'pending'
             check (status in ('pending','active','suspended','rejected')),
  subscribed boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists ai_policy (
  user_id          uuid primary key references auth.users (id) on delete cascade,
  allow_openai     boolean not null default true,
  allow_claude_cli boolean not null default false,
  primary_provider text not null default 'openai'
                   check (primary_provider in ('openai','claude_cli')),
  fallback_provider text null
                   check (fallback_provider in ('openai','claude_cli')),
  models           jsonb not null default '{}'::jsonb,
  openai_api_key   text not null default '',
  updated_at       timestamptz not null default now()
);

create table if not exists audit_log (
  id          bigint generated always as identity primary key,
  admin_id    uuid not null default auth.uid(),
  target_user uuid null,
  action      text not null,
  detail      jsonb not null default '{}'::jsonb,
  at          timestamptz not null default now()
);

-- Every new sign-up gets a pending profile + a default policy row.
create or replace function handle_new_user() returns trigger
language plpgsql security definer as $$
begin
  insert into profiles (user_id, email) values (new.id, coalesce(new.email, ''))
    on conflict (user_id) do nothing;
  insert into ai_policy (user_id) values (new.id)
    on conflict (user_id) do nothing;
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function handle_new_user();

-- Backfill rows for users who signed up before v3 existed.
insert into profiles (user_id, email)
  select id, coalesce(email, '') from auth.users
  on conflict (user_id) do nothing;
insert into ai_policy (user_id)
  select id from auth.users
  on conflict (user_id) do nothing;

-- --------------------------------------------------------------------- RLS
alter table admins    enable row level security;
alter table profiles  enable row level security;
alter table ai_policy enable row level security;
alter table audit_log enable row level security;

-- admins: a user may check their own membership; only SQL (here) edits it.
drop policy if exists "own membership" on admins;
create policy "own membership" on admins for select using (user_id = auth.uid());

-- profiles: owner reads own; admin reads/updates everyone. No API inserts
-- (the trigger owns creation), so status/subscribed are admin-only.
drop policy if exists "own profile" on profiles;
create policy "own profile" on profiles for select using (user_id = auth.uid());
drop policy if exists "admin read profiles" on profiles;
create policy "admin read profiles" on profiles for select using (is_admin());
drop policy if exists "admin update profiles" on profiles;
create policy "admin update profiles" on profiles
  for update using (is_admin()) with check (is_admin());

-- ai_policy: owner reads own (their app must know its route + key);
-- ONLY the admin writes.
drop policy if exists "own policy" on ai_policy;
create policy "own policy" on ai_policy for select using (user_id = auth.uid());
drop policy if exists "admin read policy" on ai_policy;
create policy "admin read policy" on ai_policy for select using (is_admin());
drop policy if exists "admin write policy" on ai_policy;
create policy "admin write policy" on ai_policy
  for update using (is_admin()) with check (is_admin());

-- audit_log: admin-only, append + read.
drop policy if exists "admin insert audit" on audit_log;
create policy "admin insert audit" on audit_log
  for insert with check (is_admin());
drop policy if exists "admin read audit" on audit_log;
create policy "admin read audit" on audit_log for select using (is_admin());

-- ------------------------------------------------------------------ seeding
-- The product owner is the admin: active, subscribed, Claude CLI allowed.
insert into admins (user_id)
  select id from auth.users where email = 'zaeem.javed1@gmail.com'
  on conflict (user_id) do nothing;

update profiles set status = 'active', subscribed = true
  where user_id in (select id from auth.users
                    where email = 'zaeem.javed1@gmail.com');

update ai_policy set allow_claude_cli = true, primary_provider = 'claude_cli',
                     fallback_provider = 'openai'
  where user_id in (select id from auth.users
                    where email = 'zaeem.javed1@gmail.com');
