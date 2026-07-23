-- ApplyBro schema v4 — BASE RESUME PDFs (SaaS Phase 2). Run in the Supabase
-- SQL editor after schema_v3.sql. Additive.
--
-- Users upload their resume as a PDF (decided 2026-07-14: PDF only, no
-- typst/YAML exposure). The original file is kept here; the PARSED
-- candidate facts live in resumes.content_yaml as before, so scoring,
-- tailoring and rendering are unchanged.

create table if not exists base_resumes (
  user_id     uuid primary key default auth.uid() references auth.users (id) on delete cascade,
  filename    text not null default '',
  pdf_base64  text not null default '',
  uploaded_at timestamptz not null default now()
);

alter table base_resumes enable row level security;
drop policy if exists "own rows" on base_resumes;
create policy "own rows" on base_resumes
  for all using (user_id = auth.uid()) with check (user_id = auth.uid());
