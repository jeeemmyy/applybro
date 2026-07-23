-- Fresh start for the extension-first pivot (2026-07-22)
-- ---------------------------------------------------------------------------
-- Clears the data behind the removed dashboard tabs (Companies, Jobs, Apply
-- room) and the old tracking view, so Tracking can be rebuilt around "jobs
-- you applied to through the extension".
--
-- KEPT, deliberately: auth.users, profiles, admins, ai_policy, base_resumes,
-- resumes, user_settings, kv. Nothing about who you are, your resume, or how
-- AI is configured is touched.
--
-- HOW TO RUN: paste into the Supabase SQL editor. Run STEP 1 first and read
-- the counts. Only then run STEP 2. This cannot be undone — take a backup
-- first if you want one (Database -> Backups).
--
-- SCOPE: by default this deletes ONLY your own rows, matched by email. That
-- is the safe default on a multi-user project. To wipe every user's job data,
-- use the all-users variant in STEP 3 instead — read it carefully first.

-- ===========================================================================
-- STEP 1 — look before you delete. Run this alone and read the numbers.
-- ===========================================================================
with me as (select id from auth.users where email = 'zaeem.javed1@gmail.com')
select 'companies'       as table_name, count(*) from companies       where user_id = (select id from me)
union all select 'jobs',              count(*) from jobs              where user_id = (select id from me)
union all select 'applications',      count(*) from applications      where user_id = (select id from me)
union all select 'email_log',         count(*) from email_log         where user_id = (select id from me)
union all select 'tailored_resumes',  count(*) from tailored_resumes  where user_id = (select id from me)
order by table_name;

-- ===========================================================================
-- STEP 2 — the delete, your rows only. Run after reading STEP 1's counts.
-- ===========================================================================
-- tailored_resumes is derived from jobs (a tailored CV for a job that no
-- longer exists is orphaned), so it goes with them. Comment the line out if
-- you would rather keep the PDFs.
begin;
  with me as (select id from auth.users where email = 'zaeem.javed1@gmail.com')
  delete from tailored_resumes where user_id = (select id from me);

  with me as (select id from auth.users where email = 'zaeem.javed1@gmail.com')
  delete from email_log        where user_id = (select id from me);

  with me as (select id from auth.users where email = 'zaeem.javed1@gmail.com')
  delete from applications     where user_id = (select id from me);

  with me as (select id from auth.users where email = 'zaeem.javed1@gmail.com')
  delete from jobs             where user_id = (select id from me);

  with me as (select id from auth.users where email = 'zaeem.javed1@gmail.com')
  delete from companies        where user_id = (select id from me);
commit;

-- ===========================================================================
-- STEP 3 — ALL USERS variant. Do NOT run unless you mean it: this wipes every
-- account's jobs, companies, applications and tracking history, not just
-- yours. Run the counts first (no where clause = every row in the table).
-- ===========================================================================
-- select 'companies' as table_name, count(*) from companies
-- union all select 'jobs',             count(*) from jobs
-- union all select 'applications',     count(*) from applications
-- union all select 'email_log',        count(*) from email_log
-- union all select 'tailored_resumes', count(*) from tailored_resumes;
--
-- begin;
--   delete from tailored_resumes;
--   delete from email_log;
--   delete from applications;
--   delete from jobs;
--   delete from companies;
-- commit;
