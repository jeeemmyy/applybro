# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.**

Report it privately through GitHub's
[private vulnerability reporting](https://github.com/jeeemmyy/applybro/security/advisories/new)
on this repository. Include what you found, how to reproduce it, and what an
attacker could do with it. You'll get an acknowledgement, and credit in the
fix unless you'd rather not be named.

## What is in scope

ApplyBro handles a user's resume, their application answers, their AI provider
key and — when Supabase is configured — their account. Reports that matter
most:

- Anything that could cause an application to be **submitted** without the
  user clicking Submit themselves.
- Anything that leaks a user's OpenAI key, Supabase session token, resume or
  profile into logs, errors, the page, git, or a third party.
- Anything that lets one signed-in user read or modify another's data
  (row-level security bypass).
- Injection into the extension's page-injected code, or a careers page being
  able to drive the panel.
- Anything that causes tailoring to put false claims on a resume.

## What is not in scope

- Findings that require an already-compromised machine or browser profile.
- A user deliberately putting false information in their own `content.yaml`.
- Rate limits or job boards blocking scans — that's expected behaviour and is
  reported to the user, not hidden.

## Supported versions

This is a beta project developed on `main`. Fixes land there; there is no
backport branch. Please test against the latest `main` before reporting.

## Handling of secrets

If you find a leaked credential in this repository's history or files, report
it privately as above rather than opening an issue, so it can be rotated
first.
