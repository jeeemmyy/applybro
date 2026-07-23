# ApplyBro

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)
[![Chrome extension](https://img.shields.io/badge/Chrome-extension-4285F4.svg?logo=googlechrome&logoColor=white)](extension/)
[![Status: beta](https://img.shields.io/badge/status-beta-orange.svg)](#project-status)

**Find the jobs actually worth applying to, tailor your resume to each one
without inventing anything, and fill the form in your own browser — while you
stay the only one who ever clicks Submit.**

ApplyBro is a Chrome extension backed by a local Python service. You open a
company's careers page, click **Find Relevant Jobs**, and it reads the whole
board, judges each opening against your real resume, and shows you its
reasoning. Nothing is scraped behind your back and nothing is sent anywhere
you didn't ask for — the extension works inside *your* browser session, with
*your* logins and *your* filters.

> **Two guarantees this project treats as non-negotiable.**
> **It never submits an application** — the form filler is mechanically
> prevented from clicking Submit or pressing Enter in a field, and a test
> fails the build if that regresses.
> **It never invents experience** — tailoring may reword and reorder what is
> genuinely on your resume, never add to it.

---

## Contents

- [How it works](#how-it-works)
- [Install](#install)
- [The scan, and why it tells you what it missed](#the-scan-and-why-it-tells-you-what-it-missed)
- [Safety rules](#safety-rules)
- [Architecture](#architecture)
- [Project status](#project-status)
- [Contributing](#contributing)
- [License](#license)

---

## How it works

The **extension is the product**. Everything you do day to day happens in a
panel inside the page you're already looking at:

| Where you are | What the panel offers |
| --- | --- |
| A company's careers list | **Find Relevant Jobs** — walk the board, score every opening against your resume |
| A single job posting | **Apply to this job** — read the posting, tailor your resume, start an application |
| An application form | **Autofill this step** — fill what it can, highlight what it can't, submit nothing |

The dashboard is deliberately small: **Tracking** (what you applied to and
what came back) and **Settings** (your resume, your answers, your targets).
There is no "jobs" list to curate — ApplyBro tracks *applications*, not jobs,
and scan results stay in the panel until you say you applied.

## Install

Requires Python 3.9+, Google Chrome, and [typst](https://typst.app/) for
resume rendering.

```bash
git clone https://github.com/jeeemmyy/applybro.git
cd applybro
bash scripts/install.sh      # idempotent; checks deps, installs requirements.txt
python3 -m backend.cli start # opens the dashboard
```

Then load the extension:

1. Open `chrome://extensions` and turn on **Developer mode**.
2. **Load unpacked** → select the `extension/` folder.
3. Open any careers page — a floating **A** bubble appears; click it.

The panel shows *"Connected as ..."* once it can reach the local service. See
[extension/README.md](extension/README.md) for the full behaviour reference.

On a fresh checkout the dashboard opens a **setup wizard** that walks you
through your profile, your resume, and your AI provider.

## The scan, and why it tells you what it missed

Job boards lie about their size, throttle fast readers, hide jobs behind
"Show more", and re-serve page 1 when you page too quickly. Every scan bug
this project has had was a **silent undercount** — and four separate times the
code blamed the website when the cause was ApplyBro's own limit.

So the scan is built to be auditable rather than confident:

- **ATS APIs first.** Greenhouse, Lever, Ashby, SmartRecruiters, Workday,
  Personio, Recruitee and Workable are asked directly. Reading the rendered
  page is the fallback, and both paths always exist.
- **Every page is checked against the site's own numbers** — its stated range
  ("Displaying 241 to 260 of 463") *and* the previous page, because a board
  that re-serves page 1 passes every check that looks at one page in isolation.
- **A cap that truncates must be reported as a measured number**, or not exist.
- **It says what it missed, not why** — unless the cause was actually measured.
- A bot check stops the walk and says so, rather than recording empty pages.

If a scan can't cover a board, it tells you so in the panel. A "no relevant
jobs" result is only shown when it can be trusted.

## Safety rules

These are enforced by tests, not by good intentions:

| Rule | Enforced by |
| --- | --- |
| Nothing may submit an application | `scripts/check_never_submits.py`, `scripts/check_extension_never_submits.py` |
| No secrets or personal data in logs | `scripts/check_safe_logging.py` |
| No personal data in tracked files | `scripts/release_check.py` |
| Tailoring can't change your facts | `backend.cli verify` |

Your OpenAI key lives only in gitignored config or `OPENAI_API_KEY`. Your
resume and profile are yours — the autofill layer runs in your browser
specifically so your data never has to pass through anyone's server.

## Architecture

```
extension/          the product — in-page panel, scan walk, autofill
  background.js       service worker: pagination walk, backend HTTP, apply session
  content.js          the panel UI, injected into the page
  apply.js            form filler (may not click ANYTHING)
backend/
  app/                FastAPI service: scan sessions, apply sessions, tracking
  discovery/          ATS adapters + career-page readers
  scoring/            AI fit scoring (reasoning, never keyword matching)
  tailoring/          per-job resume workspaces + no-fabrication verify
  autofill/           Playwright filler for standard ATS forms
  store/              one switch: Supabase Postgres (multi-user, RLS) or local JSON
  ai/                 the ONE place any model is called
frontend/           React dashboard — Tracking + Settings only
resume/             template.typ (layout ONLY) + content.yaml (facts ONLY)
scripts/            install + the safety checks above
```

Two rules hold this together: **layout and content never mix** (so rewording
can never change how your PDF looks), and **all AI goes through
`backend/ai/`** (so there is one place to audit what is sent to a model).

## Project status

**Beta.** Actively developed against real job boards; expect rough edges.
The extension is the supported surface. What is known not to work yet is
listed honestly in [extension/README.md](extension/README.md#known-gaps).

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
Please read [CLAUDE.md](CLAUDE.md) first: it is the project's real rulebook,
and a change that violates the tailoring or never-submit doctrine will be
declined however good the code is.

Security issues: see [SECURITY.md](SECURITY.md) — please don't open a public
issue.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
