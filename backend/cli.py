"""ApplyBro Layer 1 CLI.

    python -m backend.cli auth                 # one-time Google OAuth
    python -m backend.cli discover             # crawl + log to the Sheet
    python -m backend.cli discover --dry-run   # crawl + print, write nothing
    python -m backend.cli discover --limit 5   # only the first 5 companies
    python -m backend.cli start                # launch the dashboard
"""
from __future__ import annotations

import argparse
import os

from .config import Config
from .discovery.discover import discover_company
from .paths import (CONFIG_YAML, CONTENT_YAML, JOB_LOG_CSV, JOB_SCORES_CSV,
                    PROFILE_YAML)


def _sheet(cfg: Config):
    from .google.sheet_factory import get_sheet
    return get_sheet(cfg)


def cmd_auth(cfg: Config) -> None:
    if cfg.auth_mode != "oauth":
        print("auth_mode is 'public' — no auth needed. Reads the public sheet "
              f"directly and logs to {cfg.log_csv}. Set google.auth_mode: oauth "
              f"in {CONFIG_YAML} to enable direct sheet writes.")
        return
    from .google.sheets import Sheet
    Sheet(cfg)  # constructing it runs the OAuth flow and stores the token
    print(f"Authorized. Token saved to {cfg.token_file}")


def cmd_discover(cfg: Config, limit: int, dry_run: bool, rows: str) -> None:
    sheet = _sheet(cfg)
    companies = sheet.read_companies()
    if rows:
        from .app.runner import parse_rows
        try:
            lo, hi = parse_rows(rows, len(companies))
        except ValueError as e:
            raise SystemExit(f"--rows {rows}: {e}")
        companies = companies[lo:hi]
        print(f"Processing rows {lo + 1}..{min(hi, lo + len(companies))} "
              f"({len(companies)} companies)")
    if limit > 0:
        companies = companies[:limit]
    existing = set() if dry_run else sheet.existing_urls()

    total_new, unresolved = 0, []
    for c in companies:
        res = discover_company(c["company"], c["career_url"], cfg)
        jobs = res["jobs"]
        if res["error"] and not jobs:
            unresolved.append(c["company"])
            print(f"  !  {c['company']}: {res['error']}")
            continue
        new = [j for j in jobs if j.key() not in existing]
        for j in new:
            existing.add(j.key())
        print(f"  -  {c['company']}  [{res['resolved_url']}]  "
              f"{len(jobs)} jobs, {len(new)} new")
        if new and not dry_run:
            sheet.append_jobs(new)
        total_new += len(new)

    tag = "(dry-run) " if dry_run else ""
    print(f"\n{tag}Done. {total_new} new job(s) across {len(companies)} "
          f"companies. Unresolved/empty: {len(unresolved)} {unresolved}")


def cmd_score(cfg: Config, input_csv: str, output_csv: str,
              min_score: int, top: int, min_reqs: int) -> None:
    import csv
    from .scoring.score import load_profile_skills, score_job

    profile = load_profile_skills(CONTENT_YAML)
    print(f"Profile skills found in {CONTENT_YAML} ({len(profile)}): "
          f"{', '.join(sorted(profile))}\n")

    with open(input_csv, newline="") as f:
        rows = list(csv.DictReader(f))
    scored = []
    for r in rows:
        fit = score_job(r.get("Description", ""), profile, cfg.years_experience)
        scored.append((fit, r))
    # Rank by score, then by how many stated skills the JD had (more = the
    # percentage rests on more evidence).
    scored.sort(key=lambda x: (x[0].score, x[0].req_count), reverse=True)

    thin = sum(1 for f_, _ in scored if 0 < f_.req_count < min_reqs)
    kept = [(f_, r) for f_, r in scored
            if f_.score >= min_score and f_.req_count >= min_reqs]
    with open(output_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Fit %", "Skills in JD", "Matched", "Company", "Title",
                    "Location", "URL", "Matched Skills", "Missing Skills",
                    "Req Years", "Meets Years", "Source", "Date Found"])
        for f_, r in kept:
            w.writerow([f_.score, f_.req_count, len(f_.matched), r["Company"],
                        r["Title"], r["Location"], r["URL"],
                        ", ".join(f_.matched), ", ".join(f_.missing),
                        f_.req_years or "", "yes" if f_.meets_years else "NO",
                        r.get("Source", ""), r.get("Date Found", "")])

    print(f"Scored {len(rows)} jobs -> {len(kept)} at >= {min_score}% with >= "
          f"{min_reqs} stated skills -> {output_csv}  "
          f"({thin} skipped as too-thin JDs)")
    print("\nNote: fit % = share of the JD's stated skills that appear in "
          f"{CONTENT_YAML} (x experience factor). It is NOT a response-rate "
          "prediction.\n")
    for f_, r in kept[:top]:
        yrs = f" [needs {f_.req_years}y]" if not f_.meets_years else ""
        print(f"  {f_.score:3d}% ({len(f_.matched)}/{f_.req_count})  "
              f"{r['Company'][:20]:<20} {r['Title'][:48]:<48}{yrs}")


def cmd_tailor(scores_csv: str, log_csv: str, pick: int, url: str) -> None:
    import csv
    from .tailoring.tailor import make_package

    with open(scores_csv, newline="") as f:
        rows = list(csv.DictReader(f))
    if url:
        row = next((r for r in rows if r["URL"].strip() == url.strip()), None)
        if row is None:
            raise SystemExit(f"URL not found in {scores_csv}")
    else:
        if not (1 <= pick <= len(rows)):
            raise SystemExit(f"--pick must be 1..{len(rows)} (rows of {scores_csv})")
        row = rows[pick - 1]

    # scores csv has no Description column; join it back from the job log
    row = dict(row)
    with open(log_csv, newline="") as f:
        for lr in csv.DictReader(f):
            if lr["URL"].strip() == row["URL"].strip():
                row["Description"] = lr.get("Description", "")
                break

    d = make_package(row)

    # Per-job salary estimate: market band, never a static profile value
    from .scoring.salary import estimate as est_salary
    from .tailoring.tailor import upsert_auto_answer
    cfg_ = Config.load()
    sal = est_salary(row["Title"], row.get("Description", ""), row["Location"],
                     my_years=cfg_.years_experience,
                     req_years=int(row.get("Req Years") or 0) if str(row.get("Req Years") or "").isdigit() else 0)
    with open(os.path.join(d, "job.md"), "a") as f:
        f.write(f"\n## Salary estimate — {sal.render()}\n\n")
        for r in sal.reasoning:
            f.write(f"- {r}\n")
    for m in ("salary", "compensation"):
        upsert_auto_answer(d, m, sal.render())
    print(f"  💰 salary estimate: {sal.render()}  (edit answers.yaml to override)")

    # Rejection-aware: attach this company's outcome history to the workspace
    try:
        cfg = Config.load()
        if cfg.auth_mode == "oauth":
            from googleapiclient.discovery import build
            from .google.sheets import Sheet
            from .tracking.insights import company_history, render_history
            sheet = Sheet(cfg)
            gmail = build("gmail", "v1", credentials=sheet.creds)
            hist = company_history(sheet, row["Company"], gmail)
            if hist:
                with open(os.path.join(d, "job.md"), "a") as f:
                    f.write(render_history(hist, row["Company"]))
                print(f"  ⚠ past outcomes with {row['Company']} added to job.md "
                      f"({len(hist)} email(s))")
    except Exception as e:
        print(f"  (history lookup skipped: {e})")
    print(f"Tailoring workspace created: {d}/")
    print("  job.md                 — JD + matched/missing evidence")
    print("  content.tailored.yaml  — edit THIS (reword/reorder only)")
    print("  RULES.md               — the rules verify will enforce")
    print(f"Next: reword, then  python3 -m backend.cli verify --dir {d}")


def cmd_verify(dir_: str) -> None:
    from .tailoring.tailor import verify

    ty = os.path.join(dir_, "content.tailored.yaml")
    if not os.path.exists(ty):
        raise SystemExit(f"{ty} not found")
    ok, problems = verify(ty)
    if ok:
        print(f"✅ VERIFY PASSED — {ty} obeys the tailoring rules; "
              f"PDF compiled next to it.")
    else:
        print(f"❌ VERIFY FAILED ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(1)


def cmd_track(cfg: Config, days: int, dry_run: bool, limit: int) -> None:
    from googleapiclient.discovery import build
    from .google.sheets import Sheet
    from .tracking.track import analyze, write_log

    if cfg.auth_mode != "oauth":
        raise SystemExit("track needs auth_mode: oauth (Gmail read requires it).")
    sheet = Sheet(cfg)
    gmail = build("gmail", "v1", credentials=sheet.creds)

    companies = sheet.read_companies(extra_cols=["applied"])
    applied = [c for c in companies if c.get("applied", "").strip().lower()
               in ("yes", "y", "true", "1")]
    print(f"Tracking outcomes for {len(applied)} applied companies, "
          f"last {days} days of Gmail (read-only)...\n")

    emails = analyze(gmail, applied, days, max_results=limit)
    matched = [e for e in emails if e.company]
    review = [e for e in emails if e.classification == "needs review" and e.company]
    for e in matched:
        print(f"  {e.date}  {e.classification.upper():<22} {e.company:<20} "
              f"{e.subject[:55]}")
    print(f"\n{len(emails)} recruiting-looking emails scanned, "
          f"{len(matched)} matched to applied companies, "
          f"{len(review)} need review.")
    if dry_run:
        print("(dry-run) Nothing written to the sheet.")
        return
    n = write_log(sheet, emails)
    print(f"Appended {n} new row(s) to the 'Email Log' tab "
          f"(dedup by Gmail id; your hand-written cells untouched).")


def cmd_migrate_supabase() -> None:
    """One-time lift of everything local (and the Google Sheet's ledger +
    email log, if reachable) into Supabase. Reads the LOCAL FILES directly —
    on purpose, since once the supabase block exists the store layer already
    answers from the cloud and would hide what still needs migrating. Safe to
    re-run: companies/jobs/kv are full-state writes, the ledger is only
    seeded when its table is still empty, and the email log dedupes by
    Gmail id."""
    import json

    import yaml

    from . import store
    from .paths import (COMPANIES_JSON, QUEUE_JSON, QUEUE_META_JSON,
                        UI_STATE_JSON)

    if not store.enabled():
        raise SystemExit("No supabase: block in config.yaml (needs url + key).")
    if not store.logged_in():
        raise SystemExit("Not signed in — run `python3 -m backend.cli login` "
                         "first (data is per-user now).")

    def _read_json(path, default):
        if not os.path.exists(path):
            return default
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default

    companies = _read_json(COMPANIES_JSON, [])
    store.companies_save(companies)
    print(f"companies: {len(companies)} pushed")

    jobs = _read_json(QUEUE_JSON, [])
    store.jobs_save(jobs)
    print(f"jobs (review queue): {len(jobs)} pushed")

    ui = _read_json(UI_STATE_JSON, {})
    if ui:
        store.kv_set("ui_state", ui)
    qmeta = _read_json(QUEUE_META_JSON, {})
    if qmeta:
        store.kv_set("queue_meta", qmeta)
    print("ui_state / queue meta: pushed")

    # Ledger + email log from the Google Sheet, if one is still configured.
    cfg = Config.try_load()
    if cfg and cfg.auth_mode == "oauth" and cfg.spreadsheet_id:
        try:
            from .google.sheet_factory import get_sheet
            from .tracking.insights import LOG_ROW_KEYS
            sheet = get_sheet(cfg)

            if store.applications_read_all():
                print("applications ledger: already has rows in Supabase — skipped")
            else:
                rows = sheet.values("Applications")
                header = [h.strip().lower() for h in rows[0]] if rows else []

                def col(r, name):
                    i = header.index(name) if name in header else -1
                    return r[i].strip() if 0 <= i < len(r) else ""

                n = 0
                for r in rows[1:]:
                    store.applications_log({
                        "company": col(r, "company"), "title": col(r, "title"),
                        "url": col(r, "url"), "location": col(r, "location"),
                        "fit": col(r, "fit %"), "resume_file": col(r, "resume file"),
                        "date_applied": col(r, "date applied"),
                        "status": col(r, "status") or "applied",
                    })
                    n += 1
                print(f"applications ledger: {n} rows migrated from the sheet")

            log_rows = sheet.values("Email Log")
            entries = []
            for r in log_rows[1:]:
                r = r + [""] * (7 - len(r))
                e = dict(zip(LOG_ROW_KEYS, r[:7]))
                if e.get("gmail_id"):
                    entries.append(e)
            store.email_log_append(entries)
            print(f"email log: {len(entries)} rows migrated from the sheet")
        except Exception as e:
            print(f"sheet ledger/email log: skipped ({e})")
    else:
        print("no Google Sheet configured — ledger/email log start empty in Supabase")

    # Resume master + application profile + AI settings — per-user now.
    from .paths import CONTENT_YAML as _CY, PROFILE_YAML as _PY

    def _read_text(p):
        try:
            with open(p) as f:
                return f.read()
        except OSError:
            return ""

    content, profile = _read_text(_CY), _read_text(_PY)
    if content or profile:
        store.resumes_set(content_yaml=content or None,
                          profile_yaml=profile or None)
        print("resume master + profile: pushed")

    settings = store.user_settings_get() or {}
    if "ai" not in settings:
        with open(CONFIG_YAML) as f:
            ai_block = (yaml.safe_load(f) or {}).get("ai", {}) or {}
        if ai_block:
            settings["ai"] = ai_block
            store.user_settings_set(settings)
            print("AI settings (provider/key/models): copied to your account")

    print("\nDone. The app now reads and writes Supabase; the local JSON "
          "files remain on disk as a pre-migration backup.")


def cmd_login(create: bool = False) -> None:
    """Terminal sign-in (same email+password auth as the dashboard) — needed
    once before migrate-supabase, since all cloud data is per-user. The
    password is read with getpass and passed straight to Supabase; nothing
    but the session token is stored."""
    import getpass

    from . import store
    if not store.enabled():
        raise SystemExit("No supabase: block in config.yaml (needs url + key).")
    if store.logged_in():
        print(f"Already signed in as {(store.session() or {}).get('email', '?')}.")
        return
    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")
    if create:
        res = store.signup(email, password)
        if res.get("needs_confirmation"):
            print(f"Account created — confirm via the email sent to {email}, "
                  "then run `login`.")
            return
    else:
        res = store.login(email, password)
    print(f"Signed in as {res.get('email', email)}.")


def main() -> None:
    ap = argparse.ArgumentParser(prog="backend")
    ap.add_argument("--config", default=CONFIG_YAML)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="run the one-time Google OAuth flow")
    d = sub.add_parser("discover", help="crawl career pages and log jobs")
    d.add_argument("--rows", default="",
                   help="company row range, 1-based inclusive, e.g. 10:25 "
                        "(first data row under the header = 1)")
    d.add_argument("--limit", type=int, default=0, help="max companies to process")
    d.add_argument("--dry-run", action="store_true", help="print, don't write")

    s = sub.add_parser("score", help=f"fit-score discovered jobs against {CONTENT_YAML}")
    s.add_argument("--input", default=JOB_LOG_CSV)
    s.add_argument("--output", default=JOB_SCORES_CSV)
    s.add_argument("--min-score", type=int, default=0)
    s.add_argument("--min-reqs", type=int, default=4,
                   help="skip JDs stating fewer than this many skills (weak signal)")
    s.add_argument("--top", type=int, default=20, help="how many to print")

    t = sub.add_parser("tailor", help="create a tailoring workspace for one scored job")
    t.add_argument("--scores", default=JOB_SCORES_CSV)
    t.add_argument("--log", default=JOB_LOG_CSV)
    t.add_argument("--pick", type=int, default=0, help="1-based row of the scores csv")
    t.add_argument("--url", default="", help="or select the job by its URL")

    v = sub.add_parser("verify", help="check a tailored yaml against the rules + compile")
    v.add_argument("--dir", required=True, help="the data/applications/<slug> directory")

    a = sub.add_parser("apply", help="autofill the application form (NEVER submits)")
    a.add_argument("--dir", required=True, help="the data/applications/<slug> directory")
    a.add_argument("--profile", default=PROFILE_YAML)
    a.add_argument("--headless", action="store_true",
                   help="fill + screenshot without showing the browser (testing only)")

    st = sub.add_parser("start", help="launch the ApplyBro dashboard in its own Chrome window")
    st.add_argument("--headless", action="store_true", help="testing only")

    ins = sub.add_parser("insights", help="rejection-aware report from the Email Log")
    ins.add_argument("--output", default="insights.md")

    tr = sub.add_parser("track", help="classify application-outcome emails (read-only Gmail)")
    tr.add_argument("--days", type=int, default=30, help="how far back to scan")
    tr.add_argument("--limit", type=int, default=200, help="max emails to scan")
    tr.add_argument("--dry-run", action="store_true", help="print, don't write to sheet")

    sub.add_parser("migrate-supabase",
                   help="one-time push of the local JSON data (and the Google "
                        "Sheet ledger/email log, if configured) into Supabase")
    lg = sub.add_parser("login", help="sign in to ApplyBro (email + password)")
    lg.add_argument("--signup", action="store_true",
                    help="create the account first (sends one confirmation email)")

    args = ap.parse_args()
    if args.cmd == "login":
        cmd_login(create=args.signup)
        return
    if args.cmd == "migrate-supabase":
        cmd_migrate_supabase()
        return
    if args.cmd == "start":
        from .app.launch import run as launch_run
        launch_run(headless=args.headless)
        return
    cfg = Config.load(args.config)
    if args.cmd == "auth":
        cmd_auth(cfg)
    elif args.cmd == "tailor":
        cmd_tailor(args.scores, args.log, args.pick, args.url)
    elif args.cmd == "verify":
        cmd_verify(args.dir)
    elif args.cmd == "apply":
        from .autofill.forms import autofill
        from .tailoring import qa

        def _answers(questions):
            print(f"AI-answering {len(questions)} open question(s)…")
            ok, res = qa.answer_questions(args.dir, questions)
            if not ok:
                print(f"  couldn't generate answers ({res}) — left for you")
                return []
            return res

        result = autofill(args.dir, args.profile, headed=not args.headless,
                          answer_generator=_answers)
        print(f"Autofill done (nothing submitted). Report: {result['report_path']}")
    elif args.cmd == "track":
        cmd_track(cfg, args.days, args.dry_run, args.limit)
    elif args.cmd == "insights":
        from googleapiclient.discovery import build
        from .google.sheets import Sheet
        from .tracking.insights import report
        sheet = Sheet(cfg)
        gmail = build("gmail", "v1", credentials=sheet.creds)
        applied = {c["company"]: c.get("applied_date", "")
                   for c in sheet.read_companies(extra_cols=["applied_date"])}
        md = report(sheet, gmail, applied)
        with open(args.output, "w") as f:
            f.write(md)
        print(md)
        print(f"(saved to {args.output})")
    elif args.cmd == "score":
        cmd_score(cfg, args.input, args.output, args.min_score, args.top,
                  args.min_reqs)
    else:
        cmd_discover(cfg, args.limit, args.dry_run, args.rows)


if __name__ == "__main__":
    main()
