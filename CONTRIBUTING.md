# Contributing to ApplyBro

Thanks for taking an interest. This project automates something with real
consequences — a wrong answer on a submitted job application can't be taken
back — so it has stricter rules than most tools its size. Please read this
and [CLAUDE.md](CLAUDE.md) before writing code.

## The three rules that override everything

A pull request that breaks any of these will be declined regardless of how
well it is written.

**1. Nothing may submit an application.** The autofiller and the extension
fill forms and leave them for a human. That means: never call `.submit()` or
`.requestSubmit()`, never click a submit control, and never press Enter in a
form field (Enter inside an input is the browser's implicit-submit gesture —
this was broken once, for real, on a live application). Run:

```bash
python3 scripts/check_never_submits.py
python3 scripts/check_extension_never_submits.py
```

**2. Never invent experience.** Tailoring may reword, reorder and re-emphasise
what the user's resume genuinely says. It may never add a skill, tool,
employer, date, metric or achievement that isn't already true. If a job needs
something the user lacks, leave it out.

**3. Never hide a shortfall.** If a scan couldn't cover a board, couldn't read
a description, or hit a limit, that must surface as a number the user can see.
A cap that silently truncates is a bug even when the code "works". Say *what*
is missing; only claim *why* when you actually measured it.

## Setup

```bash
git clone https://github.com/jeeemmyy/applybro.git
cd applybro
bash scripts/install.sh
cp config.example.yaml config.yaml
cp profile.example.yaml profile.yaml
python3 -m backend.cli start
```

For extension work, load `extension/` unpacked at `chrome://extensions`, and
hit the reload arrow after every change.

## Before you open a PR

```bash
python3 scripts/check_never_submits.py            # never-submit, backend
python3 scripts/check_extension_never_submits.py  # never-submit, extension
python3 scripts/check_safe_logging.py             # no secrets in logs
python3 scripts/release_check.py                  # no personal data tracked
cd frontend && npx vite build                     # dashboard still builds
```

**Never test against real data.** Tests must point storage paths at a temp
directory. A test that ran against the real queue once destroyed 166 staged
jobs. If a test needs a store, monkeypatch the path.

## Style

- Prefer editing an existing file over adding a new one.
- Match the surrounding code's naming and comment density.
- Comments should explain *why*, especially when the reason is a bug that was
  actually hit — much of this codebase's comment weight is scar tissue, and it
  is load-bearing.
- Commit messages: a short imperative subject, then a body explaining the
  reasoning and any measurement behind the change.

## Reporting bugs

Scan bugs are the most valuable and the easiest to get wrong. Please include
the careers URL, what the panel's funnel showed (every row and number), any
warning band text, and what the site itself claimed its job count was. "It
found fewer jobs than it should" is very hard to act on without those numbers.

## Security

Don't open a public issue for a security problem — see [SECURITY.md](SECURITY.md).

## License

By contributing you agree that your contributions are licensed under the
Apache License 2.0, the same terms that cover the project.
