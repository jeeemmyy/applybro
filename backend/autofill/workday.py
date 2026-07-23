"""Layer 3b — Workday application autofill. NEVER SUBMITS.

Workday portals (<tenant>.wdN.myworkdayjobs.com) are multi-page wizards behind
an account wall, so this flow is INTERACTIVE by design:

  1. opens the job, clicks Apply -> "Apply Manually" when offered,
  2. if a sign-in/create-account wall appears, PAUSES so the human signs in
     (credentials are never handled by this tool),
  3. fills the current page's recognizable fields via Workday's stable
     data-automation-id attributes + uploads the tailored resume,
  4. shows what it filled and asks before clicking "Next" — and it refuses
     to click any button whose text looks like a final submit,
  5. at the review/submit page it stops: submission is the human's act.

Non-interactive runs (tests / --headless): fills the first reachable page,
reports, and exits — no page advancing without a human present.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Dict, List

SUBMIT_RE = re.compile(r"submit|send application", re.I)

# data-automation-id substring -> profile key
WD_FIELDS = [
    ("legalNameSection_firstName", "first_name"),
    ("legalNameSection_lastName", "last_name"),
    ("firstName", "first_name"),
    ("lastName", "last_name"),
    ("email", "email"),
    ("phone-number", "phone"),
    ("phoneNumber", "phone"),
    ("addressSection_city", "city"),
    ("city", "city"),
    ("linkedinQuestion", "linkedin"),
    ("linkedin", "linkedin"),
]


def is_workday(url: str) -> bool:
    return "myworkdayjobs.com" in (url or "") or "/wday/" in (url or "")


def _click_if(page, selector: str, timeout: int = 4000) -> bool:
    try:
        el = page.locator(selector).first
        if el.count() and el.is_visible():
            txt = (el.inner_text() or "")
            if SUBMIT_RE.search(txt):
                return False              # never click submit-like controls
            el.click(timeout=timeout)
            page.wait_for_timeout(1800)
            return True
    except Exception:
        pass
    return False


def _signin_wall(page) -> bool:
    for sel in ('[data-automation-id="signInContent"]',
                '[data-automation-id="createAccountContent"]',
                'input[data-automation-id="password"]'):
        try:
            if page.locator(sel).first.is_visible():
                return True
        except Exception:
            continue
    return False


def fill_workday_page(page, profile: Dict[str, str], resume_pdf: str,
                      report: Dict[str, list]) -> None:
    # resume upload (Workday accepts it on the "My Experience" page)
    for sel in ('input[data-automation-id="file-upload-input-ref"]',
                'input[type="file"]'):
        try:
            fi = page.locator(sel).first
            if fi.count():
                fi.set_input_files(resume_pdf)
                report["uploaded"].append("resume (workday uploader)")
                break
        except Exception:
            continue

    for auto_id, key in WD_FIELDS:
        val = profile.get(key, "")
        if not val:
            continue
        try:
            el = page.locator(f'input[data-automation-id*="{auto_id}"]').first
            if el.count() and el.is_visible() and not el.input_value():
                el.fill(val)
                report["filled"].append(f"{auto_id} <- {val[:40]}")
        except Exception as e:
            report["skipped"].append(f"{auto_id} ({e})")

    # anything else visible that we didn't touch -> report for the human
    try:
        for el in page.locator("input:visible, textarea:visible").all():
            if not el.input_value():
                aid = el.get_attribute("data-automation-id") or \
                      el.get_attribute("id") or "?"
                if not any(aid in f for f in report["filled"]):
                    report["skipped"].append(aid[:70])
    except Exception:
        pass


def workday_flow(page, profile: Dict[str, str], resume_pdf: str,
                 interactive: bool) -> Dict[str, list]:
    report = {"filled": [], "uploaded": [], "skipped": [], "pages": []}

    # 1. reach the application: Apply -> Apply Manually
    _click_if(page, '[data-automation-id="adventureButton"]')  # "Apply"
    _click_if(page, 'a[data-automation-id="applyManually"], '
                    'button[data-automation-id="applyManually"]')

    # 2. account wall -> human signs in (or we stop in non-interactive mode)
    if _signin_wall(page):
        if not (interactive and sys.stdin.isatty()):
            report["pages"].append("stopped at sign-in wall (no human present)")
            return report
        print("\n>> Workday wants an account. Sign in / create the account in "
              "the browser window (I never touch credentials).")
        input(">> Press Enter here once you're past the sign-in page... ")

    # 3. page loop
    page_no = 1
    while True:
        page.wait_for_timeout(1500)
        fill_workday_page(page, profile, resume_pdf, report)
        report["pages"].append(f"page {page_no} filled")
        nxt = page.locator('button[data-automation-id="bottom-navigation-next-button"]').first
        try:
            nxt_text = nxt.inner_text() if nxt.count() else ""
        except Exception:
            nxt_text = ""
        if not nxt_text or SUBMIT_RE.search(nxt_text):
            print("\n>> Reached the final/submit page. Review everything and "
                  "click Submit yourself. I will not.")
            break
        if not (interactive and sys.stdin.isatty()):
            report["pages"].append("stopped: advancing pages needs a human")
            break
        ans = input(f">> Page {page_no} filled. Review it in the browser. "
                    f"Press Enter to let me click '{nxt_text.strip()}' "
                    f"(or type q to stop): ").strip().lower()
        if ans == "q":
            break
        nxt.click()
        page_no += 1
    return report
