# ApplyBro — AI privacy (SaaS Phase 6)

What ApplyBro sends to AI providers, and what it does NOT keep.

## What's implemented

- **OpenAI is stateless.** Every call sets `store: false` and uses only the
  Chat Completions endpoint — no Assistants, Threads, or Files. There is no
  persistent OpenAI conversation object or uploaded file to leave behind.
  (backend/ai/openai_provider.py)
- **Resumes are parsed on our side.** A PDF's text is extracted locally
  (pypdf) and only the extracted TEXT is sent to the model — the PDF file is
  never uploaded to a provider. (backend/tailoring/parse_resume.py)
- **Claude CLI runs in a neutral temp dir**, never inside the repo or a user
  workspace, and the CLI is invoked non-interactively with no transcript
  persistence. When hosted, the Mac runner uses one isolated temp dir per job,
  deleted after the result is stored. (backend/ai/claude_cli.py, MVP-702)
- **Logs carry no payloads.** Only safe metadata (feature, model, status,
  counts, truncated error text) is logged — never a prompt, resume, answer,
  email body, or API key. Enforced by
  `scripts/check_safe_logging.py`. (MVP-703)
- **API keys never leave the server side.** The OpenAI key lives in
  admin-controlled config; it is never returned to the dashboard or the
  extension, never logged, never in an error message. (MVP-704, admin API
  returns only `has_openai_key`.)
- **SSRF-guarded fetches.** Every URL the extension or a user supplies is
  validated to resolve to a PUBLIC address before the backend (or its headless
  browser) fetches it, on every redirect hop — the server can't be tricked
  into hitting cloud-metadata or internal hosts. (backend/net.py, MVP-704)

## The honest limitation

Setting `store: false` and not uploading files keeps data out of *retrievable*
OpenAI storage. It does **not** by itself guarantee that OpenAI's standard
abuse-monitoring retention is bypassed — without a Zero-Data-Retention
agreement on the account, normal API retention rules may still apply.

**The privacy promise shown to users must match the actual OpenAI account
configuration in use.** If you enable ZDR on the ApplyBro OpenAI
organization/project, you may state it; otherwise use wording no stronger
than:

> "ApplyBro doesn't use persistent OpenAI conversations or file storage. We
> parse your resume ourselves and send only the text needed, disable
> model-training data sharing, request stateless processing, and keep
> resumes, prompts and answers out of our logs."

## Not yet done (tracked)

- Zero-Data-Retention agreement on the OpenAI account (an account action, not
  code) before making a ZDR claim to users.
- Redacting contact details from prompts where a field doesn't need them
  (currently the resume text is sent whole for scoring/tailoring, which does
  need it).
