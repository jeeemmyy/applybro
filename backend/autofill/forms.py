"""Layer 3 — application form autofill. NEVER SUBMITS.

Drives the system Chrome via Playwright. Given an application workspace
(data/applications/<slug>/ made by `tailor`, containing job.md with the URL and a
verified content.tailored.pdf), it:

  1. opens the job page and finds the application form (Greenhouse, Lever,
     Ashby, SmartRecruiters direct forms; plus a generic label-matching pass),
  2. fills fields whose meaning it can establish from profile.yaml,
  3. uploads the tailored resume PDF,
  4. saves autofill_report.md + filled_form.png to the workspace,
  5. STOPS. In headed mode the browser stays open for the human to review,
     answer anything skipped, and click submit THEMSELVES.

Submission is a human act by design (CLAUDE.md / product rule). There is no
code path that clicks a submit button: `SUBMIT_PATTERNS` exists only so the
filler can recognize and AVOID those buttons.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

import yaml

from . import countries
from ..paths import PROFILE_YAML

# Field-meaning patterns -> profile keys. Matched against the concatenation of
# a control's label / name / id / placeholder / aria-label, lowercased.
FIELD_MAP: List[Tuple[str, str]] = [
    (r"first\s*name|given\s*name", "first_name"),
    (r"last\s*name|family\s*name|surname", "last_name"),
    (r"full\s*name|your\s*name|^name$", "full_name"),
    (r"e-?mail", "email"),
    (r"phone|mobile|\btel(?:ephone)?\b", "phone"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"website|portfolio|personal\s*site", "website"),
    # "current OR previous employer/title" is common — allow words in between.
    (r"(?:current|previous|recent)[\w\s/]{0,20}(?:company|employer)|"
     r"^company$|^org(?:anization)?$", "current_company"),
    (r"(?:current|previous|recent)[\w\s/]{0,20}(?:job\s*)?(?:title|role|position)|"
     r"^title$", "current_title"),
    (r"most recent school|school you attended|school.*attended|name of.*school",
     "education_school"),
    (r"(?:city|location|where.*(?:located|based))", "location"),
    (r"how.*hear|source|referr?al\s*source", "how_did_you_hear"),
    (r"salary|compensation|expected\s*pay", "salary_expectation"),
    (r"notice\s*period|^availability$", "notice_period"),
    (r"start\s*date|available.{0,10}start|earliest.{0,10}start", "preferred_start_date"),
]

SUBMIT_PATTERNS = re.compile(
    r"submit|apply now|send application|finish|complete application", re.I)

# Dropdown (select/combobox) questions -> (profile key, fallback answers).
# The profile value is tried first, then the fallbacks — an answer is only
# picked when it matches one of the dropdown's real options.
COMBO_MAP = [
    # Combined "authorised to work ... (we sponsor) ..." question (Intercom/
    # Greenhouse style) — checked BEFORE the plain work-authorization rule so
    # the specific one wins. The truthful pick for a sponsorship-needing
    # candidate is the "requires sponsorship" option; bare "No" is the last
    # resort for Yes/No phrasings of the same question.
    (re.compile(r"authori[sz]ed to work.{0,200}sponsor", re.I | re.S),
     "work_auth_sponsorship",
     ["My current work authorization requires sponsorship",
      "My current work authorisation requires sponsorship",
      "I require sponsorship", "I will require sponsorship",
      "Requires sponsorship", "No"]),
    (re.compile(r"(?:legally )?authori[sz]ed to work|work authori[sz]ation", re.I),
     "work_authorized", []),
    (re.compile(r"willing to relocate|open to relocat", re.I),
     "willing_to_relocate", ["Yes"]),
    # "sponsorship" phrased without the word: "require <company> to commence
    # an immigration case in order to employ you?" (N26/Greenhouse).
    (re.compile(r"sponsorship|work permit|visa status|"
                r"immigration (case|status|support|process)", re.I),
     "needs_sponsorship", []),
    (re.compile(r"most recent degree|highest.*degree|"
                r"degree.*(obtained|earned|completed)|level of education|"
                r"education level", re.I),
     "education_degree", ["Bachelor", "Bachelor's Degree", "Bachelor of Science"]),
    # Residence country. Some employers only list countries they operate in
    # (Stripe offers 29 + "Other"; Pakistan isn't among them), so "Other" is
    # the truthful fallback — tried only after the real country doesn't match.
    (re.compile(r"country .*(currently )?reside|where .*(currently )?reside|"
                r"country of residence", re.I),
     "country", ["Other"]),
    # Bare "Country" (the picker sitting next to a Phone field) — matched
    # AFTER the residence rule above so the specific one wins.
    (re.compile(r"^\s*country\s*\*?\s*$|country code|phone.*country", re.I),
     "country", []),
    (re.compile(r"ever been employed by|ever worked (for|at)|"
                r"previously (been )?(employed|worked (for|at))|"
                r"former(ly)? employed|"
                r"current or former employee|worked (here|at this company)", re.I),
     "prior_employee", ["No"]),
    (re.compile(r"work remotely|plan to work remote", re.I),
     "remote_work", ["No"]),
    # "We work under a hybrid in-office model. Are you willing to work from
    # our office N days per week?" — in-office is the standing preference.
    (re.compile(r"willing to work from (our|the) office|"
                r"hybrid.{0,80}(office|days)|office.{0,40}days per week", re.I),
     "hybrid_onsite", ["Yes"]),
    # "When are you available to start?" as a DROPDOWN (the text-input
    # variant is FIELD_MAP -> preferred_start_date). The fallback ladder
    # covers the common option shapes for someone immediately available.
    (re.compile(r"availab(le|ility) to start|when (are you available|can you start)",
                re.I),
     "start_availability",
     ["Immediately", "Less than 15 days", "Less than 1 month",
      "Within 1 month", "ASAP", "0-15"]),
    # "Where are you currently based?" as a dropdown of office cities
    # (Berlin/Vienna/Barcelona/Other) — the real location won't be listed
    # for a relocating candidate, so "Other" is the truthful fallback.
    (re.compile(r"where are you (currently )?based|currently based in", re.I),
     "location", ["Other"]),
    (re.compile(r"whatsapp|receive .*(text|sms|message|whatsapp)s? from|"
                r"recruiting (text|message)|email me about|"
                r"future (job )?openings|job alerts", re.I),
     "comms_optin", ["Yes"]),
    (re.compile(r"privacy (notice|policy)|terms|information .* is true|"
                r"true and correct|i confirm|gdpr|data protection|"
                r"(hereby|i) agree|notification to candidates", re.I),
     "accept_terms", ["I confirm", "I agree", "I acknowledge", "I accept",
                      "Acknowledge", "Confirm", "Agree"]),
    # Regulated-industry pre-hire checks: "able to provide professional
    # references and criminal record checks?" (banks/fintech).
    (re.compile(r"criminal record|background check|"
                r"(professional )?references? (and|or|check)", re.I),
     "background_check", ["Yes"]),
    # Conflict-of-interest: "any relatives or domestic partners currently
    # working for <company>?"
    (re.compile(r"relatives|domestic partner|"
                r"family member.{0,30}(work|employ)", re.I),
     "relatives_at_company", ["No"]),
    (re.compile(r"first generation.{0,50}(university|college|graduate)", re.I),
     "first_generation", []),
    # Voluntary self-ID: answered ONLY when profile.yaml provides a value.
    (re.compile(r"sexual orientation", re.I),
     "sexual_orientation", ["Straight"]),
    (re.compile(r"gender", re.I), "gender", ["Man"]),
    (re.compile(r"hispanic|latino|latinx", re.I), "hispanic_latino", ["No"]),
    (re.compile(r"race|ethnic", re.I), "ethnicity", ["Asian"]),
    (re.compile(r"veteran", re.I), "veteran_status", []),
    (re.compile(r"disab", re.I), "disability_status", []),
    (re.compile(r"neurodiver", re.I), "neurodiversity", []),
]

# Self-ID topics with no profile answer are left for the human.
DEMOGRAPHIC = re.compile(
    r"gender|race|ethnic|veteran|disab|pronoun|demographic|orientation|"
    r"neurodiver|religion|survey", re.I)

RESUME_HINT = re.compile(r"resume|cv|curriculum", re.I)


def load_profile(path: str = PROFILE_YAML) -> Dict[str, str]:
    with open(path) as f:
        return {k: ("" if v is None else str(v)) for k, v in yaml.safe_load(f).items()}


def _control_descriptor(el) -> str:
    """All the text that hints at a control's meaning."""
    parts = []
    for attr in ("name", "id", "placeholder", "aria-label", "autocomplete"):
        v = el.get_attribute(attr)
        if v:
            parts.append(v)
    try:
        lid = el.get_attribute("id")
        if lid:
            lab = el.owner_frame().query_selector(f'label[for="{lid}"]')
            if lab:
                parts.append(lab.inner_text())
    except Exception:
        pass
    return " ".join(parts).lower()


def _value_for(desc: str, profile: Dict[str, str]) -> Optional[Tuple[str, str]]:
    for pat, key in FIELD_MAP:
        if re.search(pat, desc):
            v = profile.get(key, "")
            return (key, v) if v else None  # empty profile value = leave for human
    return None


def _combo_label(el) -> str:
    """Accessible label of a combobox/select (aria-labelledby, aria-label,
    or an associated <label>)."""
    return el.evaluate("""e => {
        const ids = e.getAttribute('aria-labelledby');
        if (ids) return ids.split(' ')
            .map(i => (document.getElementById(i)||{}).textContent||'').join(' ');
        if (e.getAttribute('aria-label')) return e.getAttribute('aria-label');
        return (e.labels && e.labels[0]) ? e.labels[0].textContent : '';
    }""") or ""


def _combo_selected(el) -> str:
    """Text currently shown by a react-select-style combobox."""
    return el.evaluate("""e => {
        const c = e.closest('div');
        const box = c ? c.parentElement : null;
        return box ? box.textContent : '';
    }""") or ""


# "Do you have 7+ years experience ...?" — the threshold varies per job, so
# the answer is COMPUTED from profile years_experience, never hardcoded.
# Truthful by construction: >= threshold -> Yes, below -> No (per CLAUDE.md,
# an honest No beats an inflated Yes).
_YEARS_THRESHOLD_Q = re.compile(r"(\d+)\s*\+\s*year", re.I)


def _answer_for(label: str, profile: Dict[str, str]):
    for pat, key, fallbacks in COMBO_MAP:
        if pat.search(label):
            v = (profile.get(key) or "").strip()
            if not v:
                return None       # no profile answer -> left for the human
            return [v] + fallbacks
    m = _YEARS_THRESHOLD_Q.search(label)
    if m and re.search(r"experience", label, re.I):
        try:
            years = float(profile.get("years_experience") or 0)
        except ValueError:
            years = 0
        if years > 0:
            return ["Yes"] if years >= float(m.group(1)) else ["No"]
    if DEMOGRAPHIC.search(label):
        return None
    return None


def _combo_filled(el) -> bool:
    """Does this react-select already show a chosen value? Anchor on the
    `control` element (NOT `[class*=select]`, which also matches the input's
    own `select__input-container` wrapper and has no text): a rendered
    `single-value` node means answered, a `placeholder` node means empty."""
    try:
        return bool(el.evaluate("""e => {
          const c = e.closest('[class*=control]');
          if (!c) return false;
          if (c.querySelector('[class*=single-value],[class*=singleValue],'
                              + '[class*=multi-value],[class*=multiValue]'))
            return true;
          if (c.querySelector('[class*=placeholder]')) return false;
          const t = (c.innerText || '').trim();
          return !!t && !/^select\\.\\.\\./i.test(t);
        }"""))
    except Exception:
        return False


# ---------------------------------------------------------------- submit guard
# CLAUDE.md's hard rule: no code path may EVER submit an application. Filling a
# form is full of gestures that can trip a submit (a stray Enter, a script
# calling form.submit()), so while the filler is touching the page we make
# submission physically impossible, then hand an untouched, unsubmitted form
# back to the human. Belt and braces: we also never send Enter (see
# _choose_combo_option) and never click a SUBMIT_PATTERNS control.
_GUARD_ON_JS = """() => {
  if (window.__applybroGuard) return;
  const g = {blocked: 0};
  g.onSubmit = e => { g.blocked++; e.preventDefault(); e.stopImmediatePropagation(); };
  g.onEnter = e => {
    if (e.key === 'Enter' && e.target && e.target.tagName !== 'TEXTAREA') {
      g.blocked++; e.preventDefault(); e.stopImmediatePropagation();
    }
  };
  document.addEventListener('submit', g.onSubmit, true);
  document.addEventListener('keydown', g.onEnter, true);
  // A script calling form.submit() fires NO submit event, so the listener
  // above cannot see it. Neuter the methods themselves for the duration.
  g.origSubmit = HTMLFormElement.prototype.submit;
  g.origRequest = HTMLFormElement.prototype.requestSubmit;
  HTMLFormElement.prototype.submit = function () { g.blocked++; };
  HTMLFormElement.prototype.requestSubmit = function () { g.blocked++; };
  window.__applybroGuard = g;
}"""

_GUARD_OFF_JS = """() => {
  const g = window.__applybroGuard;
  if (!g) return 0;
  document.removeEventListener('submit', g.onSubmit, true);
  document.removeEventListener('keydown', g.onEnter, true);
  HTMLFormElement.prototype.submit = g.origSubmit;
  HTMLFormElement.prototype.requestSubmit = g.origRequest;
  delete window.__applybroGuard;
  return g.blocked;
}"""


def _guard_on(frame) -> None:
    try:
        frame.evaluate(_GUARD_ON_JS)
    except Exception:
        pass


def _guard_off(frame) -> int:
    """Remove the guard and return how many submit attempts it blocked. Must
    ALWAYS run, or the human is left unable to submit the form themselves."""
    try:
        return int(frame.evaluate(_GUARD_OFF_JS) or 0)
    except Exception:
        return 0


def _combo_value(el) -> str:
    """The text react-select currently displays as the chosen value."""
    try:
        return (el.evaluate("""e => {
          const c = e.closest('[class*=control]');
          if (!c) return '';
          const sv = c.querySelector('[class*=single-value],[class*=singleValue]');
          return sv ? sv.textContent.trim() : '';
        }""") or "").strip()
    except Exception:
        return ""


def _clear_combo(frame, el) -> None:
    """Undo a selection: click react-select's clear "x", else Backspace with
    an empty search input (which react-select treats as 'remove value')."""
    try:
        clr = el.evaluate_handle(
            "e => { const c = e.closest('[class*=control]'); return c ? "
            "c.querySelector('[class*=clear-indicator],[class*=clearIndicator]') "
            ": null; }").as_element()
        if clr:
            clr.click(timeout=2000)
            frame.page.wait_for_timeout(150)
            return
    except Exception:
        pass
    try:
        el.click(timeout=2000)
        el.press("Backspace")
        frame.page.wait_for_timeout(150)
        frame.page.keyboard.press("Escape")
    except Exception:
        pass


def _value_matches(val: str, a: str) -> bool:
    v, b = val.lower().strip(), a.lower().strip()
    return bool(v) and (v == b or v.startswith(b) or b in v)


def _open_combo(frame, el) -> bool:
    """Open a react-select menu. Its real <input> can be ~3px wide (the
    visible box is a styled wrapper), so a center-click sometimes never lands
    — fall back to focusing and dispatching mousedown, which is what
    react-select actually listens for."""
    try:
        el.click(timeout=4000)
        return True
    except Exception:
        pass
    try:
        el.evaluate("e => { e.focus(); "
                    "e.dispatchEvent(new MouseEvent('mousedown', {bubbles: true})); }")
        return True
    except Exception:
        return False


def _visible_options(frame):
    """Options of the menu that is actually open — measured by BOX SIZE.

    A closed react-select menu can keep all its [role=option] nodes in the DOM
    collapsed to 0x0 while still reporting `visibility: visible` (Stripe's
    phone country picker leaves 244 of them there). Playwright's is_visible()
    believes them, so a naive scan hands another question's options to this
    one. Only a node with a real width/height belongs to the open menu."""
    out = []
    for o in frame.query_selector_all('[role="option"]'):
        try:
            box = o.bounding_box()
            if not box or box["width"] < 1 or box["height"] < 1:
                continue
            out.append((o, (o.text_content() or "").strip()))
        except Exception:
            continue
    return out


def _scroll_listbox(frame) -> None:
    """Scroll the open menu so a virtualized list renders more rows. A pure
    mouse/scroll gesture — unlike a keypress it can never submit a form."""
    try:
        frame.evaluate("""() => {
          const opt = [...document.querySelectorAll('[role=option]')].find(o => {
            const r = o.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
          if (!opt) return;
          let n = opt.parentElement;
          for (let i = 0; i < 4 && n; i++, n = n.parentElement) {
            if (n.scrollHeight > n.clientHeight + 4) { n.scrollTop = n.scrollHeight; return; }
          }
        }""")
        frame.page.wait_for_timeout(500)
    except Exception:
        pass


def _match_option(opts, a, exact: bool):
    """Exact match wins over prefix. Critical: the answer "No" prefix-matches
    "No, I do not have a disability and have not had one in the past" — an
    exact-first pass keeps a yes/no question from grabbing that.

    exact=False (city autocomplete) still REQUIRES the suggestion to contain
    what we typed. Blindly taking the first suggestion once picked "Lima,
    Peru" for "Lahore" — a wrong answer on a real application is far worse
    than an empty field, so we leave it for the human instead."""
    if not exact:
        for o, t in opts:
            if a.lower() in t.lower():
                return (o, t)
        return None
    for o, t in opts:
        if t.lower() == a.lower():
            return (o, t)
    for o, t in opts:
        if t.lower().startswith(a.lower()):
            return (o, t)
    return None


def _choose_combo_option(frame, el, answers, exact: bool = True) -> Optional[str]:
    """Pick an option, typing the answer first to FILTER the menu.

    Long lists (country pickers: 200+ entries) are virtualized — react-select
    only renders a slice, so scanning the rendered options finds no "Pakistan".
    Typing narrows the list to what we want. Non-searchable selects ignore the
    typing, so we then retry against the unfiltered options. `exact=False`
    takes the first suggestion (used for city autocompletes, whose suggestion
    text -- "Lahore, Punjab, Pakistan" -- never equals what we typed)."""
    for a in answers:
        if not a:
            continue
        frame.page.keyboard.press("Escape")
        frame.page.wait_for_timeout(120)
        if not _open_combo(frame, el):
            return None
        frame.page.wait_for_timeout(300)
        typed = False
        try:
            el.type(a, delay=15)
            typed = True
            frame.page.wait_for_timeout(900 if not exact else 700)
        except Exception:
            pass

        for attempt in (0, 1):
            hit = None
            # Long lists (200-country pickers) and remote autocompletes need a
            # beat to filter; re-scan once before giving up on this answer.
            for settle in (0, 600, 900):
                if settle:
                    frame.page.wait_for_timeout(settle)
                opts = _visible_options(frame)
                hit = _match_option(opts, a, exact)
                if hit or opts:
                    break
            if hit:
                try:
                    hit[0].click(timeout=4000)
                    frame.page.wait_for_timeout(250)
                    # Leave no menu open: a still-open list overlays the next
                    # control and its options pollute the next option scan.
                    frame.page.keyboard.press("Escape")
                    frame.page.wait_for_timeout(120)
                    return hit[1]
                except Exception:
                    pass
            # Virtualized lists (200+ countries) render only the rows in view,
            # so a filtered match can exist with no clickable node. We used to
            # press Enter here. NEVER DO THAT: Enter inside a form input is the
            # browser's implicit-submit gesture, and when react-select doesn't
            # swallow it the APPLICATION GETS SUBMITTED. It also picked the
            # focused option rather than the typed one ("Australia" for
            # "Pakistan"). Scroll the listbox instead — a mouse-only gesture —
            # and re-scan for a real, clickable node.
            if typed and exact and attempt == 0:
                try:
                    _scroll_listbox(frame)
                    hit = _match_option(_visible_options(frame), a, exact)
                    if hit:
                        hit[0].click(timeout=4000)
                        frame.page.wait_for_timeout(250)
                        frame.page.keyboard.press("Escape")
                        return hit[1]
                except Exception:
                    pass
            # non-searchable select: our typing filtered nothing away, or it
            # filtered everything out. Clear it and look at the full list.
            if attempt == 0 and typed:
                for _ in range(len(a)):
                    try:
                        el.press("Backspace")
                    except Exception:
                        break
                frame.page.wait_for_timeout(500)
            else:
                break
    return None


def _unskip(report: Dict[str, list], label: str) -> None:
    """Drop stale 'skipped' notes for a control we have now filled."""
    key = label[:40]
    for s in list(report["skipped"]):
        if key and key in s:
            report["skipped"].remove(s)


CONSENT_CHECKBOX = re.compile(
    r"privacy (notice|policy)|terms and conditions|i confirm|"
    r"true and correct|i consent|consent to .{0,60}(process|collect|stor)|"
    r"gdpr|(hereby|i) agree|notification to candidates|"
    r"acknowledge|data (transfer|protection)", re.I)


def _do_native_select(frame, el, profile: Dict[str, str],
                      report: Dict[str, list], quiet: bool) -> None:
    """A plain <select>: pick the first option that starts with our answer."""
    try:
        if el.evaluate("e => e.value"):
            return
        label = _combo_label(el)
        answers = _answer_for(label, profile)
        if not answers:
            return
        opts = [o.text_content().strip() for o in el.query_selector_all("option")]
        for a in answers:
            hit = next((o for o in opts if o.lower().startswith(a.lower())), None)
            if hit:
                el.select_option(label=hit)
                report["filled"].append(f"{label[:55]} <- {hit}")
                _unskip(report, label)
                return
    except Exception as e:
        if not quiet:
            report["skipped"].append(f"select ({e})")


def _do_combo(frame, el, profile: Dict[str, str], report: Dict[str, list],
              quiet: bool) -> None:
    """A react-select combobox: the city autocomplete, or a COMBO_MAP answer."""
    label = ""
    try:
        if _combo_filled(el):
            return
        label = _combo_label(el).strip()
        full = (label + " " + _control_descriptor(el)).strip()

        # A real answer always wins. Checking COMBO_MAP first matters: plenty
        # of QUESTIONS contain the word "location" ("authorized to work in the
        # location(s) you selected", "work from a remote location") and would
        # otherwise be hijacked by the city autocomplete below.
        answers = _answer_for(label, profile)
        if answers:
            picked = _choose_combo_option(frame, el, answers)
            if picked:
                report["filled"].append(f"{label[:55]} <- {picked}")
                _unskip(report, label)
            else:
                frame.page.keyboard.press("Escape")
                if not quiet:
                    report["skipped"].append(
                        f"dropdown (no matching option): {label[:55]}")
            return

        # City autocomplete: type the bare city, take a suggestion that
        # actually contains it (never blindly the first one).
        if _LOCATION_COMBO.search(full) and not _NOT_COUNTRY_GROUP.search(full):
            city = (profile.get("city") or profile.get("location") or "")
            city = city.split(",")[0].strip()
            if city:
                picked = _choose_combo_option(frame, el, [city], exact=False)
                if picked:
                    report["filled"].append(f"location <- {picked[:50]}")
                    _unskip(report, label)
                else:
                    frame.page.keyboard.press("Escape")
                    if not quiet:
                        report["skipped"].append(f"location: {label[:50]}")
            return

        if label and not quiet:
            report["skipped"].append(f"dropdown: {label[:60]}")
    except Exception as e:
        if not quiet:
            report["skipped"].append(f"combobox {label[:40]} ({e})")


def _do_checkbox(frame, el, should_check, report: Dict[str, list]) -> None:
    """Consent boxes (privacy/terms — the user accepts these and reviews the
    form before submitting) and the "countries you'd work in" grid."""
    try:
        if el.is_checked():
            return
        label = (_combo_label(el) or "").strip() or _control_descriptor(el)
        if not label:
            return
        if CONSENT_CHECKBOX.search(label):
            el.check(timeout=4000)
            if el.is_checked():
                report["filled"].append(f"checkbox: {label[:55]} <- checked")
            return
        if should_check is None or not should_check(label):
            return
        group = el.evaluate(_GROUP_TEXT_JS) or ""
        if not _COUNTRY_GROUP.search(group) or _NOT_COUNTRY_GROUP.search(group):
            return
        el.check(timeout=4000)
        # Only claim it if it actually stuck (React can revert a click).
        if el.is_checked():
            report["filled"].append(f"country <- {label.strip()[:40]}")
        else:
            report["skipped"].append(f"country checkbox didn't stick: {label[:40]}")
    except Exception as e:
        report["skipped"].append(f"checkbox ({e})")


DECLINE_OPTION = re.compile(
    r"prefer not to answer|decline to (answer|state)|select\.{2,3}$|^n/a$", re.I)


def _fill_essay_selects(frame, profile: Dict[str, str], answers: list,
                        report: Dict[str, list]) -> None:
    """Some 'essay' questions are actually a react-select ('...means to me'
    vs 'I prefer not to answer') that only REVEALS a free-text 'Please
    specify' box once you pick the non-decline option. _fill_answers alone
    can't see that box because it doesn't exist until this selection happens.
    Skips anything COMBO_MAP already owns (work auth/sponsorship/consent/
    demographics) so the two mechanisms never fight over the same control."""
    if not answers:
        return
    ids = frame.evaluate("""() =>
        [...document.querySelectorAll('input[role="combobox"]')]
          .filter(e => e.id).map(e => e.id)""") or []
    for qid in ids:
        try:
            el = frame.query_selector(f'input[id="{qid}"]')
            if el is None or not el.is_visible():
                continue
            label = _combo_label(el).strip()
            if not label or _answer_for(label, profile) is not None:
                continue   # owned by COMBO_MAP (work auth/consent/demographic/etc.)
            ans = next((a for a in answers
                       if str(a.get("match", "")).lower().strip() in label.lower()),
                      None)
            if not ans:
                continue
            before_texts = {c.get_attribute("id") for c in
                            frame.query_selector_all("textarea, input[type='text']")}
            el.click()
            frame.page.wait_for_timeout(400)
            lb = el.get_attribute("aria-controls") or el.get_attribute("aria-owns")
            opts = (frame.query_selector_all(f'[id="{lb}"] [role="option"]')
                    if lb else frame.query_selector_all('[role="option"]'))
            pick = next((o for o in opts
                        if (o.text_content() or "").strip()
                        and not DECLINE_OPTION.search((o.text_content() or "").strip())),
                       None)
            if pick is None:
                frame.page.keyboard.press("Escape")
                report["skipped"].append(f"essay-select (no option to expand): {label[:50]}")
                continue
            pick.click()
            frame.page.wait_for_timeout(600)
            target = next(
                (c for c in frame.query_selector_all("textarea, input[type='text']")
                 if c.is_visible() and not c.input_value()
                 and c.get_attribute("id") not in before_texts), None)
            if target is None:
                report["skipped"].append(
                    f"essay-select (selected, no reveal field found): {label[:50]}")
                continue
            target.fill(str(ans.get("answer", "")).strip())
            report["filled"].append(
                f"essay-select: {label[:45]} <- ({len(str(ans.get('answer','')))} chars)")
            stale = f"dropdown: {label[:60]}"
            if stale in report["skipped"]:
                report["skipped"].remove(stale)
        except Exception as e:
            report["skipped"].append(f"essay-select ({e})")


_GROUP_TEXT_JS = """e => {
  let n = e;
  for (let i = 0; i < 6 && n; i++, n = n.parentElement) {
    if (n.tagName === 'FIELDSET') {
      const lg = n.querySelector('legend');
      if (lg) return lg.textContent || '';
    }
    if (n.getAttribute && n.getAttribute('role') === 'group')
      return n.getAttribute('aria-label') || '';
  }
  let p = e.parentElement;
  for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
    const t = (p.innerText || '');
    if (/countr/i.test(t)) return t.slice(0, 400);
  }
  return '';
}"""

# The multi-select is a country question ONLY if the group asks about working
# there. A citizenship/residence checklist must never be auto-ticked.
_COUNTRY_GROUP = re.compile(r"countr", re.I)
_NOT_COUNTRY_GROUP = re.compile(r"citizen|passport|nationalit|reside", re.I)


# A city field, not a question that merely says "location". Belt and braces:
# _do_combo only reaches this after COMBO_MAP has declined the control.
_LOCATION_COMBO = re.compile(
    r"^\s*location\b|\bcity\b|\btown\b|where are you (located|based)", re.I)

# Controls we must never touch. `submit`/`image` buttons especially: clicking
# one submits the application (CLAUDE.md).
_SKIP_INPUT_TYPES = {"hidden", "submit", "button", "image", "reset", "file"}

_INDEX_JS = """(skip) => {
  let i = 0;
  for (const e of document.querySelectorAll('input, select, textarea')) {
    const t = (e.getAttribute('type') || '').toLowerCase();
    if (e.tagName === 'INPUT' && skip.includes(t)) continue;
    e.setAttribute('data-applybro-idx', String(i++));
  }
  return i;
}"""

_UNINDEX_JS = """() => {
  for (const e of document.querySelectorAll('[data-applybro-idx]'))
    e.removeAttribute('data-applybro-idx');
}"""


def _fill_in_order(frame, profile: Dict[str, str], report: Dict[str, list],
                   should_check, quiet: bool = False) -> None:
    """ONE pass over every fillable control in DOM order, dispatching by kind.

    Filling used to run four separate sweeps (all text, then all dropdowns,
    then all checkboxes, then the city box), which made the page scroll up and
    down repeatedly and looked like it was skipping around. Tagging the
    controls with their document index first and walking that order fills the
    form top to bottom, the way a person would.

    Each control is re-queried by its index right before use: picking a
    react-select option re-renders the tree and would stale a handle grabbed
    earlier."""
    try:
        n = frame.evaluate(_INDEX_JS, sorted(_SKIP_INPUT_TYPES))
    except Exception:
        return
    for i in range(n or 0):
        try:
            el = frame.query_selector(f'[data-applybro-idx="{i}"]')
            if el is None or not el.is_visible() or not el.is_enabled():
                continue
            info = el.evaluate(
                "e => ({tag: e.tagName.toLowerCase(),"
                " type: (e.getAttribute('type') || '').toLowerCase(),"
                " role: e.getAttribute('role') || ''})")
        except Exception:
            continue
        tag, typ, role = info["tag"], info["type"], info["role"]
        try:
            if tag == "input" and typ == "checkbox":
                _do_checkbox(frame, el, should_check, report)
            elif tag == "input" and typ == "radio":
                continue                       # never guess a radio choice
            elif tag == "select":
                _do_native_select(frame, el, profile, report, quiet)
            elif role == "combobox":
                _do_combo(frame, el, profile, report, quiet)
            elif tag in ("input", "textarea"):
                _do_text(frame, el, profile, report, quiet)
        except Exception as e:
            if not quiet:
                report["skipped"].append(f"control #{i} ({e})")
    try:
        frame.evaluate(_UNINDEX_JS)
    except Exception:
        pass


def fill_form(page, profile: Dict[str, str], resume_pdf: str,
              answers: list = ()) -> Dict[str, list]:
    """Fill every recognizable control in EVERY frame (ATS forms are often
    embedded in an iframe on the company's own careers page)."""
    report = {"filled": [], "uploaded": [], "skipped": [], "blocked_submits": 0}
    frames = list(page.frames)
    for frame in frames:
        _guard_on(frame)
    try:
        for frame in frames:
            try:
                _fill_frame(frame, profile, resume_pdf, report, answers)
            except Exception as e:
                report["skipped"].append(f"frame {frame.url[:60]} ({e})")
    finally:
        # ALWAYS lift the guard, even if filling blew up — otherwise the human
        # is left staring at a form they cannot submit.
        for frame in frames:
            report["blocked_submits"] += _guard_off(frame)
    if report["blocked_submits"]:
        report["skipped"].append(
            f"!! blocked {report['blocked_submits']} submit attempt(s) while "
            f"filling — the form was NOT submitted")
    return report


def _fill_answers(frame, profile: Dict[str, str], answers: list,
                  report: Dict[str, list]) -> None:
    """Fill essay/open-text questions from the workspace's answers.yaml
    (each item: {match: <label substring>, answer: <text>}). Runs after the
    generic field pass, so a field it fills may have been logged there as
    'skipped' (e.g. salary, with no static profile value) — remove that
    stale note so the report doesn't contradict itself."""
    if not answers:
        return
    for el in frame.query_selector_all(
            "textarea, input[type='text']:not([role='combobox']), "
            "input:not([type]):not([role='combobox'])"):
        try:
            if not el.is_visible() or el.input_value():
                continue
            base_desc = _control_descriptor(el)
            desc = (base_desc + " " + _combo_label(el)).lower()
            for a in answers:
                m = str(a.get("match", "")).lower().strip()
                if m and m in desc:
                    el.fill(str(a.get("answer", "")).strip())
                    report["filled"].append(f"answer: {m[:50]} <- ({len(str(a.get('answer','')))} chars)")
                    if base_desc[:80] in report["skipped"]:
                        report["skipped"].remove(base_desc[:80])
                    break
        except Exception as e:
            report["skipped"].append(f"answer field ({e})")


# A text input whose label reads like a question (not a data field) — the
# kind an AI answer is written for. Textareas don't need this test: an empty
# visible textarea on an application form IS an open-ended question.
_QUESTION_HINT = re.compile(
    r"\?|^(why|what|how|describe|tell|share|explain)\b|"
    r"\b(why|describe|tell us|tell me|explain|motivat|cover letter)\b", re.I)


def _question_label(el) -> str:
    """Human-readable question text for a control: its <label>, aria-label,
    or placeholder — the text a person answering the form actually reads
    (unlike _control_descriptor, which also concatenates name/id soup)."""
    try:
        lid = el.get_attribute("id")
        if lid:
            lab = el.owner_frame().query_selector(f'label[for="{lid}"]')
            if lab:
                t = (lab.inner_text() or "").strip()
                if t:
                    return t
    except Exception:
        pass
    for attr in ("aria-label", "placeholder"):
        try:
            v = el.get_attribute(attr)
        except Exception:
            v = None
        if v and v.strip():
            return v.strip()
    return ""


def collect_open_questions(page, answers: list = ()) -> List[dict]:
    """The still-unanswered open-ended questions on the form, AFTER the
    normal fill pass: visible empty textareas, plus text inputs whose label
    reads like a question. These are what the AI answerer (tailoring.qa)
    gets to write grounded answers for.

    Never collected: profile-mapped data fields (FIELD_MAP — those are
    facts, not prose), demographic/self-ID questions (human-only by rule),
    resume fields, and anything already covered by an answers.yaml entry.
    Each item: {"match": <substring _fill_answers can find in the control's
    descriptor>, "question": <text shown to the AI>, "kind", "maxlength"}."""
    out: List[dict] = []
    seen = set()
    existing = [str(a.get("match", "")).lower().strip()
                for a in (answers or []) if a.get("match")]
    for frame in page.frames:
        try:
            els = frame.query_selector_all(
                "textarea, input[type='text']:not([role='combobox']), "
                "input:not([type]):not([role='combobox'])")
        except Exception:
            continue
        for el in els:
            try:
                if not el.is_visible() or el.input_value():
                    continue
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                desc = _control_descriptor(el)
                label = _question_label(el) or desc
                if not label:
                    continue
                if DEMOGRAPHIC.search(desc) or RESUME_HINT.search(desc):
                    continue
                if any(re.search(pat, desc) for pat, _ in FIELD_MAP):
                    continue
                if tag != "textarea" and not (
                        _QUESTION_HINT.search(label) or len(label) >= 30):
                    continue
                match = label.lower().strip()[:60]
                if not match or match in seen:
                    continue
                if any(m in (desc + " " + label.lower()) for m in existing):
                    continue
                seen.add(match)
                ml = None
                try:
                    v = el.get_attribute("maxlength")
                    ml = int(v) if v and v.isdigit() else None
                except Exception:
                    pass
                out.append({"match": match, "question": label.strip(),
                            "kind": tag, "maxlength": ml})
            except Exception:
                continue
    return out


def fill_answers_pass(page, profile: Dict[str, str], answers: list,
                      report: Dict[str, list]) -> None:
    """A guarded answers-only pass over every frame — used to type in AI
    answers generated AFTER the main fill (the submit guard must be up
    whenever code touches the page, same as fill_form)."""
    frames = list(page.frames)
    for frame in frames:
        _guard_on(frame)
    try:
        for frame in frames:
            try:
                _fill_essay_selects(frame, profile, answers, report)
                _fill_answers(frame, profile, answers, report)
            except Exception as e:
                report["skipped"].append(f"answers pass {frame.url[:50]} ({e})")
    finally:
        for frame in frames:
            report["blocked_submits"] += _guard_off(frame)


def _attach_file(frame, fi, resume_pdf: str, desc: str,
                 report: Dict[str, list]) -> None:
    """Attach the resume and CONFIRM it took.

    Newer Greenhouse uploaders hide the real <input type=file> (1x1,
    .visually-hidden) behind an "Attach" button and render the chosen filename
    themselves. Setting the input's files is what a file chooser does — the
    File really is on the input — but if the page's own uploader JS never
    reacts, the form still considers the resume missing and only says so at
    submit time. So: set the file, then check the widget actually shows the
    name. Never report an upload we could not confirm; a silent non-attachment
    is how you submit an application with no resume.

    The file input is never clicked: a click opens the OS file dialog, and in
    a shared window that steals focus from the human.
    """
    name = os.path.basename(resume_pdf)
    try:
        fi.set_input_files(resume_pdf)
    except Exception as e:
        report["skipped"].append(f"resume upload FAILED ({e}) — attach it by hand")
        return
    frame.page.wait_for_timeout(1200)

    on_input = False
    try:
        on_input = bool(fi.evaluate("e => !!(e.files && e.files.length)"))
    except Exception:
        pass
    shown = False
    try:
        shown = bool(fi.evaluate("""(e, n) => {
          let x = e;
          for (let i = 0; i < 6 && x; i++, x = x.parentElement) {
            if ((x.innerText || '').includes(n)) return true;
          }
          return false;
        }""", name))
    except Exception:
        pass

    if on_input and shown:
        report["uploaded"].append(desc[:80] or "resume")
    elif on_input:
        # The File is on the input but the page hasn't acknowledged it. Some
        # ATSs simply never echo the filename, so this is a warning, not a
        # failure — but the human must eyeball it before submitting.
        report["uploaded"].append(f"{desc[:60] or 'resume'} (UNCONFIRMED)")
        report["skipped"].append(
            f"!! resume attached to the input but the page never showed "
            f"'{name}' — CHECK the Resume/CV field before submitting")
    else:
        report["skipped"].append(f"!! resume did NOT attach — attach it by hand")


def _do_text(frame, el, profile: Dict[str, str], report: Dict[str, list],
             quiet: bool = False) -> None:
    """One text-like control. Idempotent: never overwrites an existing value,
    so the second pass only fills what is still empty."""
    if el.input_value():
        return                       # never overwrite anything present
    desc = _control_descriptor(el)
    if not desc:
        return
    kv = _value_for(desc, profile)
    if kv is None:
        if not quiet:
            report["skipped"].append(desc[:80])
        return
    key, val = kv
    if key == "phone":
        # Forms with a separate country-code picker (intl-tel-input —
        # class "iti__tel-input" or a ".iti" ancestor) already show the
        # +CC there, so the text field must get the NATIONAL number only
        # (profile.phone_national) or the code ends up duplicated, e.g.
        # "+1" picker + "+15551234567" typed = "+1 5551234567" wrong.
        # Otherwise use the full E.164 number, no spaces/dashes.
        # Also true when a Country select/combobox sits in the same form
        # row (Greenhouse/Ashby/Lever render it as a sibling, not `.iti`).
        try:
            has_country_picker = el.evaluate("""e => {
              if (e.classList.contains('iti__tel-input') || e.closest('.iti'))
                return true;
              let n = e;
              for (let i = 0; i < 4 && n; i++, n = n.parentElement) {
                for (const c of n.querySelectorAll('select, [role="combobox"]')) {
                  const lb = (c.labels && c.labels[0]) ? c.labels[0].textContent : '';
                  const s = [c.getAttribute('aria-label'), c.id, c.name, lb]
                    .filter(Boolean).join(' ');
                  if (/country|dial|code/i.test(s)) return true;
                }
              }
              return false;
            }""")
        except Exception:
            has_country_picker = False
        if has_country_picker and profile.get("phone_national"):
            val = re.sub(r"[\s-]+", "", profile["phone_national"])
        else:
            val = re.sub(r"[\s-]+", "", val)
    try:
        el.fill(val)
        suffix = " (re-filled)" if quiet else ""
        report["filled"].append(f"{desc[:60]} <- {val[:40]}{suffix}")
        _unskip(report, desc[:40])
    except Exception as e:
        if not quiet:
            report["skipped"].append(f"{desc[:60]} ({e})")


def _fill_frame(frame, profile: Dict[str, str], resume_pdf: str,
                report: Dict[str, list], answers: list = ()) -> None:
    # Resume first, before the ordered pass: attaching it makes Greenhouse
    # parse the file and remount the personal-info section. Doing it up front
    # means the pass below fills those fields AFTER the remount, not before.
    for fi in frame.query_selector_all('input[type="file"]'):
        desc = _control_descriptor(fi)
        if RESUME_HINT.search(desc) or not report["uploaded"]:
            _attach_file(frame, fi, resume_pdf, desc, report)

    include = (profile.get("work_countries_include") or "").strip()
    should_check = (countries.selector(
        include, profile.get("work_countries_exclude") or "")
        if include else None)

    # One top-to-bottom sweep over every control, in document order.
    _fill_in_order(frame, profile, report, should_check)

    # Per-workspace essay answers: these target specific labels rather than a
    # position, so order doesn't matter, and the reveal-a-textbox questions
    # need the main pass to have settled first.
    _fill_essay_selects(frame, profile, answers, report)
    _fill_answers(frame, profile, answers, report)

    # Second sweep, same top-to-bottom order. Two reasons, true of most ATSs:
    #  * Forms reveal questions as you answer them (Greenhouse only shows
    #    "identify your race" once Hispanic/Latino is set), so the first
    #    sweep never saw them.
    #  * Greenhouse parses the uploaded resume asynchronously and can remount
    #    the personal-info section, wiping name/email typed before that.
    # The pass is idempotent — it skips controls that already have a value —
    # so re-running only fills what is still empty.
    frame.page.wait_for_timeout(1500)
    _fill_in_order(frame, profile, report, should_check, quiet=True)


def find_application_page(page, job_url: str) -> None:
    """Navigate to the actual form: many boards need an 'Apply' click first."""
    page.goto(job_url, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(2000)
    if any(f.query_selector('input[type="file"]') for f in page.frames):
        return                            # a form (possibly in an iframe) is here
    for pat in (r"^apply", r"apply (now|for|to)"):
        try:
            btn = page.get_by_role("link", name=re.compile(pat, re.I)).first
            if btn.count() == 0:
                btn = page.get_by_role("button", name=re.compile(pat, re.I)).first
            if btn.count() > 0 and SUBMIT_PATTERNS.search(btn.inner_text() or "") is None:
                btn.click(timeout=5000)
                page.wait_for_timeout(2000)
                break
        except Exception:
            continue


def _acquire_page(p, headed: bool):
    """Prefer attaching to the dashboard's own dedicated Chrome window over
    CDP (so application tabs open beside the dashboard, not in a separate
    throwaway browser) — this is how the Apply room shares one window with
    the dashboard even though it fills forms via the SYNC Playwright API
    while the dashboard runs on ASYNC Playwright: connect_over_cdp() attaches
    an independent client to the SAME already-running browser process.
    Verified empirically that a tab opened this way survives even after this
    sync client disconnects, so it's safe to just leave it for the user.
    Falls back to launching a standalone Chrome when the dashboard isn't
    running (e.g. bare `python -m backend.cli apply` without `start`)."""
    from ..constants import CDP_PORT
    try:
        browser = p.chromium.connect_over_cdp(
            f"http://127.0.0.1:{CDP_PORT}", timeout=2000)
        page = browser.contexts[0].new_page()
        return page, browser, True
    except Exception:
        browser = p.chromium.launch(channel="chrome", headless=not headed)
        page = browser.new_page()
        return page, browser, False


def autofill(workdir: str, profile_path: str = PROFILE_YAML,
             headed: bool = True, wait_for_close: bool = True,
             answer_generator=None) -> dict:
    """Open the job in Chrome, fill what we can, report, never submit.
    Returns {"report_path", "url", "filled", "uploaded", "skipped"}.

    `wait_for_close=False` (used by the Apply room's sequential engine) fills
    the form and returns immediately, leaving the tab open for review instead
    of blocking until the human closes it — lets the next job in a batch
    start filling right away while this one awaits review.

    `answer_generator`: optional callable(questions) -> [{match, answer}].
    Called with the open-ended questions still unanswered after the normal
    pass (see collect_open_questions); whatever it returns is typed in via a
    second guarded pass. The caller owns HOW answers are produced (the Apply
    room passes tailoring.qa's AI answerer) — this module only ever types
    them."""
    from playwright.sync_api import sync_playwright

    job_md = os.path.join(workdir, "job.md")
    with open(job_md) as f:
        m = re.search(r"^- URL: (\S+)", f.read(), re.M)
    if not m:
        raise SystemExit(f"No URL found in {job_md}")
    job_url = m.group(1)

    resume = os.path.join(workdir, "content.tailored.pdf")
    if not os.path.exists(resume):
        raise SystemExit(
            f"{resume} missing — run verify first (it compiles the PDF).")

    # Upload under a searchable, human-readable name:
    #   "<Role> - <Full Name> - Resume.pdf"
    profile = load_profile(profile_path)
    with open(job_md) as f:
        head = f.readline().lstrip("# ").strip()          # "<Title> — <Company>"
    role = head.split("—")[0].strip() or "Application"
    nice = re.sub(r'[\\/:*?"<>|]+', "-", f"{role} - {profile.get('full_name','')} - Resume.pdf")
    nice_path = os.path.join(workdir, nice)
    import shutil
    shutil.copyfile(resume, nice_path)
    resume = os.path.abspath(nice_path)

    # Optional per-workspace essay answers (written by Claude on request):
    # answers.yaml = list of {match: <label substring>, answer: <text>}
    answers = []
    ay = os.path.join(workdir, "answers.yaml")
    if os.path.exists(ay):
        with open(ay) as f:
            answers = yaml.safe_load(f) or []

    with sync_playwright() as p:
        page, browser, is_cdp = _acquire_page(p, headed)
        from .workday import is_workday, workday_flow
        if is_workday(job_url):
            page.goto(job_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)
            report = workday_flow(page, profile, resume, interactive=headed)
        else:
            find_application_page(page, job_url)
            report = fill_form(page, profile, resume, answers=answers)

        # AI answers for open-ended questions (JobRight-style): whatever is
        # STILL empty after the normal pass and reads like a question goes to
        # the answer generator (tailoring.qa via apply_engine — grounded in
        # the tailored resume, never invented). Generated answers are typed
        # in with the same guarded machinery, and land in answers.yaml so a
        # re-fill reuses them without another AI call.
        if answer_generator is not None:
            try:
                questions = collect_open_questions(page, answers)
            except Exception:
                questions = []
            if questions:
                generated = answer_generator(questions) or []
                if generated:
                    fill_answers_pass(page, profile,
                                      list(answers) + list(generated), report)

        shot = os.path.join(workdir, "filled_form.png")
        page.screenshot(path=shot, full_page=True)

        rp = os.path.join(workdir, "autofill_report.md")
        with open(rp, "w") as f:
            f.write(f"# Autofill report — NOT SUBMITTED\n\nURL: {job_url}\n\n")
            for section, title in (("uploaded", "Uploaded"), ("filled", "Filled"),
                                   ("skipped", "Left for you"), ("pages", "Pages")):
                if section not in report:
                    continue
                f.write(f"## {title} ({len(report[section])})\n")
                for line in report[section]:
                    f.write(f"- {line}\n")
                f.write("\n")
            f.write("Review the form in the browser, complete anything under "
                    "'Left for you', and submit it yourself.\n")

        # wait_for_close=False (Apply room's batch flow): return immediately,
        # leaving the tab open for review while the next job starts filling.
        # Verified empirically that a CDP-attached tab survives this `with`
        # block exiting. A standalone (non-CDP) launch's lifecycle is less
        # certain once its driver stops, so still wait in that combination —
        # in practice it shouldn't arise: the Apply room only runs with the
        # dashboard up, where CDP-attach always succeeds.
        if wait_for_close or not is_cdp:
            if headed:
                print(f"Browser left open for your review. Report: {rp}")
                print("Complete + submit the form yourself, then close Chrome.")
                page.wait_for_event("close", timeout=0)   # wait until human closes
            browser.close()
    return {
        "report_path": rp,
        "url": job_url,
        "filled": report.get("filled", []),
        "uploaded": report.get("uploaded", []),
        "skipped": report.get("skipped", []),
    }
