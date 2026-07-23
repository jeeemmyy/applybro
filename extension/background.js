// ApplyBro service worker — the extension's brain since the in-page panel
// refactor (2026-07-19, side panel removed). The UI (content.js) is injected
// into the viewed page and DIES on every navigation, so everything that must
// survive one lives here: backend HTTP (extension fetch bypasses the page's
// CORS/CSP — a content script's own fetch would be blocked), the multi-page
// scan capture loop (it navigates the tab through pagination), the
// rendered-tab fallback worker, and the apply session. content.js is a thin
// view over this state and re-attaches after each navigation.
//
// Never-submit doctrine applies here exactly as everywhere else: this file
// navigates tabs and injects read-only capture functions — it never clicks,
// never dispatches keys, never submits (scripts/check_extension_never_submits.py).

const DEFAULT_BACKEND = "http://127.0.0.1:8765";
// No cap: a 24-page board (ServiceNow) was silently truncated at 10 (user
// report 2026-07-22). The real stops are MAX_LINKS, the user's Stop button,
// and a next-link that repeats or disappears.
const MAX_SCAN_PAGES = Infinity;
// Raw-anchor budget. It is NOT a job budget: ServiceNow carries ~250 nav and
// filter links per page, so a 3000 cap stopped a 24-page board at page 12
// with 30 jobs collected out of 462 (user report 2026-07-22). Jobs are
// counted separately, below.
const MAX_LINKS = 60000;
// The real stop: how many JOB CARDS we've gathered. Bounded so one runaway
// board can't exhaust the worker, but far above any real careers page.
// This is the ONLY card bound left in this file — the payload used to be
// sliced to 300 separately, which is a 463-job board losing a third of itself
// after 24 pages of correct capture (cap audit 2026-07-22).
const MAX_SCAN_CARDS = 2000;
const TERMINAL = ["complete", "partial", "stopped", "error"];

async function backendUrl() {
  const v = await chrome.storage.sync.get(["backend"]);
  return (v.backend || DEFAULT_BACKEND).replace(/\/+$/, "");
}

async function api(path, opts) {
  const res = await fetch((await backendUrl()) + path, {
    headers: { "Content-Type": "application/json" },
    ...(opts || {}),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body;
}

// ------------------------------------------------- injected page functions
// These are serialized into the PAGE by chrome.scripting — they must stay
// self-contained (no closures) and read-only: no clicks, no form access.
// SELF-CONTAINED IS LITERAL: executeScript({func}) serializes ONE function,
// so every helper must be NESTED inside it. Calling a sibling top-level
// helper throws ReferenceError in the page and the capture silently returns
// nothing — that shipped once (2026-07-21) and made every scan find 0 jobs.

// Reveal jobs hidden behind "Show more" / "Load more" (user request
// 2026-07-22). This is the ONLY place in the extension allowed to click, and
// the rules are deliberately narrow:
//   - it runs during a SCAN, on a careers LIST page, and nowhere else;
//   - it refuses any control inside a <form>, any submit/image/reset input,
//     and anything whose text isn't a show-more phrase;
//   - the capture-phase submit guard is installed for the whole pass and
//     removed in a finally, exactly like apply.js does while filling.
// apply.js still may not click ANYTHING — scripts/check_extension_never_
// submits.py enforces that split. The never-submit rule is unchanged: no
// application can be submitted from here, because there is no form here.
async function expandListings() {
  const MORE = /^(show|load|view|see)\s+(more|all)( jobs| results| positions)?$|^more( jobs| results)?$|^load more$/i;
  const blocked = (e) => { e.preventDefault(); e.stopImmediatePropagation(); return false; };
  document.addEventListener("submit", blocked, true);
  const origSubmit = HTMLFormElement.prototype.submit;
  const origRequest = HTMLFormElement.prototype.requestSubmit;
  HTMLFormElement.prototype.submit = function () {};
  HTMLFormElement.prototype.requestSubmit = function () {};
  // A click is only WORTH keeping if it revealed job links. Both passes below
  // stop the moment a round adds none — that self-check is what lets this be
  // general (any site) instead of a per-site rule, and what keeps it from
  // spinning on a decorative toggle.
  const clickable = (c) => {
    if (c.closest("form")) return false;                 // never in a form
    const t = (c.type || "").toLowerCase();
    if (t === "submit" || t === "image" || t === "reset") return false;
    if (c.disabled || c.getAttribute("aria-disabled") === "true") return false;
    const r = c.getBoundingClientRect();
    return r.width >= 2 && r.height >= 2;                 // must be visible
  };
  let clicks = 0;
  // BOTH passes below click through this ONE helper — the single sanctioned
  // click site in the whole extension (scripts/check_extension_never_submits.py
  // enforces exactly one). It only ever receives a `clickable()` control while
  // the submit guard is installed, so no pass can submit an application.
  const doClick = (c) => { c.click(); clicks++; };
  try {
    // Rounds, not jobs: a board that reveals 10 per click needed 46 rounds to
    // show 463, and the old limit of 40 stopped at 400 without saying so (cap
    // audit 2026-07-22). The honest stop is "no new links appeared", below;
    // this bound and the wall clock only exist so a broken button can't spin
    // forever. 300 rounds x 1.2s is capped by the 4-minute budget in practice.
    const deadline = Date.now() + 240000;
    for (let round = 0; round < 300 && Date.now() < deadline; round++) {
      const before = document.querySelectorAll("a[href]").length;
      const el = Array.from(
        document.querySelectorAll("button, a[role='button'], [role='button']")
      ).find((c) => clickable(c) &&
        MORE.test((c.innerText || c.getAttribute("aria-label") || "")
          .replace(/\s+/g, " ").trim()));
      if (!el) break;
      doClick(el);
      await new Promise((r) => setTimeout(r, 1200));
      // No new links appeared — the button is done; stop rather than loop.
      if (document.querySelectorAll("a[href]").length <= before) break;
    }

    // Second pass: ARIA accordions. Many careers pages (fin.ai, Base-UI /
    // Radix disclosure widgets) hide every role inside collapsed team sections
    // that aren't in the DOM until expanded — a scan of fin.ai's 130 jobs
    // collected 1 because none were rendered yet (user report 2026-07-23).
    // Open every aria-expanded=false disclosure OUTSIDE nav/header (those are
    // menus, not listings) and keep going while job links keep appearing. A
    // filter dropdown that opens instead adds no job links, so the round-level
    // growth check drops it harmlessly — never a fin.ai-specific branch.
    for (let round = 0; round < 60 && Date.now() < deadline; round++) {
      const before = document.querySelectorAll("a[href]").length;
      const toggles = Array.from(
        document.querySelectorAll("[aria-expanded='false']")
      ).filter((c) => clickable(c) && !c.closest("nav, header"));
      if (!toggles.length) break;
      for (const t of toggles) { try { doClick(t); } catch (e) { /* keep going */ } }
      await new Promise((r) => setTimeout(r, 900));
      if (document.querySelectorAll("a[href]").length <= before) break;
    }
  } finally {
    document.removeEventListener("submit", blocked, true);
    HTMLFormElement.prototype.submit = origSubmit;
    HTMLFormElement.prototype.requestSubmit = origRequest;
  }
  return { clicks };
}

// Poll until the anchor count is stable across two checks (or we give up).
// Read-only: it counts links and waits, nothing else.
async function waitForListToSettle() {
  let last = -1;
  let stable = 0;
  for (let i = 0; i < 40; i++) {           // ~16s worst case; exits on stability
    const n = document.querySelectorAll("a[href]").length;
    if (n === last) {
      if (++stable >= 2) return { links: n, settled: true };
    } else {
      stable = 0;
      last = n;
    }
    await new Promise((r) => setTimeout(r, 400));
  }
  return { links: last, settled: false };
}

// NOTE ON THE NUMBERS IN HERE: this function is serialized into the page, so
// it cannot reference MAX_* from the top of the file — every bound has to be
// an inline literal. They are sized for a "Show all" page that puts a whole
// 500-job board in one document, because that is exactly the page we ask the
// user to open (cap audit 2026-07-22).
function capturePage() {
  // One JobPosting ld+json per card is a real pattern; 30 quietly kept the
  // first 30 jobs of such a board.
  const jsonld = Array.from(
    document.querySelectorAll('script[type="application/ld+json"]')
  ).map((s) => s.textContent).filter(Boolean).slice(0, 500);
  const anchors = Array.from(document.querySelectorAll("a[href]"))
    .slice(0, 10000)
    .map((a) => ({
      text: (a.innerText || "").trim().slice(0, 160),
      href: a.href,
    }))
    .filter((a) => a.href.startsWith("http"));
  return {
    url: location.href,
    title: document.title,
    jsonld,
    anchors,
    cards: captureJobCards(),
    pageTotal: capturePageTotal(),
    pageRange: capturePageRange(),
    challenge: captureChallenge(),
    visibleText: (document.body.innerText || "").slice(0, 60000),
  };

  // Job CARDS, read structurally from the rendered DOM (2026-07-21). The
  // backend used to infer jobs from raw anchors, which fails both ways on real
  // sites: on HubSpot the only job-URL anchors are the "Apply" buttons (title
  // "Apply", correctly rejected) while a dozen marketing pages like
  // /careers/benefits matched the job-URL pattern and became "jobs" — 100
  // candidates on a 15-job list, all junk.
  //
  // The signal a careers list actually gives you is REPETITION: job links share
  // one URL template and appear many times; nav links are one-offs. So group
  // links by template, keep the crowded templates, and take each one's title
  // from the card around it when the anchor's own text is useless.
  function captureJobCards() {
    const NOT_TITLE =
      /^(apply( now)?|learn more|find out more|read more|view( job| all)?|see (all|more)|details|share|save|log ?in|sign in|next|previous|back|show all)$/i;
    const clean = (t) => (t || "").replace(/\s+/g, " ").trim();
    const usable = (t) => t.length >= 6 && t.length <= 140 && !NOT_TITLE.test(t);

    // A URL's shape, with ids blanked out: /careers/jobs/7005836 -> /careers/jobs/#
        // Everything after the id is per-job noise: ServiceNow's
        // /jobs/<id>/<slug>/ gave every posting its own template, so no
        // group ever reached 3 and a 462-job board yielded 24 (user report
        // 2026-07-22). Cut at the id so all of a board's jobs group together.
    const template = (u) => {
      try {
        const p = new URL(u);
        return p.host + p.pathname
          .replace(/\/[0-9]{3,}(?=\/|$)/g, "/#")
          .replace(/\/[0-9a-f]{8}-?[0-9a-f-]{4,}(?=\/|$)/gi, "/#")
          .replace(/\/[^/]*\d[^/]*(?=\/|$)/g, "/#")
          .replace(/\/#\/.*$/, "/#");
      } catch (e) {
        return "";
      }
    };

    // Repetition alone isn't enough — site nav repeats too (/products/*,
    // /partners/* all cluster on HubSpot). Repetition AND a job-ish path is.
    const JOBISH =
      /(^|\/)(jobs?|careers?|positions?|vacanc\w*|openings?)(\/|$)|greenhouse|lever\.co|myworkdayjobs|smartrecruiters|ashbyhq|recruitee|workable|personio/i;
    const links = Array.from(document.querySelectorAll("a[href]"))
      .filter((a) => /^https?:/.test(a.href));
    const groups = {};
    for (const a of links) {
      const t = template(a.href);
      if (!t) continue;
      if (!JOBISH.test(t)) continue;   // full template: host counts too
      (groups[t] = groups[t] || []).push(a);
    }
    let listy = Object.keys(groups).filter((k) => groups[k].length >= 3);
    // A template carrying an id (/careers/jobs/#) IS the job-detail shape —
    // when one exists, the sibling /careers/<slug> nav pages are noise.
    const withId = listy.filter((k) => k.indexOf("/#") >= 0);
    if (withId.length) listy = withId;
    if (!listy.length) return [];

    const out = [];
    const seen = {};
    for (const key of listy) {
      for (const a of groups[key]) {
        let title = clean(a.innerText);
        if (!usable(title)) title = titleFromCard(a, clean, usable);
        if (!usable(title)) continue;
        const href = a.href.split("#")[0];
        if (seen[href]) continue;
        seen[href] = 1;
        out.push({ title: title.slice(0, 140), url: href,
                   location: locationFromCard(a, clean) });
        if (out.length >= 2000) return out;   // matches MAX_SCAN_CARDS
      }
    }
    return out;

    // The title of the card this link sits in: a heading, else the longest
    // usable text among the card's other links. Never invents anything — if
    // nothing readable is there, the job is skipped and counted as unread.
    function titleFromCard(a, clean, usable) {
      let el = a;
      for (let i = 0; i < 5 && el.parentElement; i++) {
        el = el.parentElement;
        const h = el.querySelector("h1,h2,h3,h4,h5,h6,[role='heading']");
        if (h) {
          const t = clean(h.innerText);
          if (usable(t)) return t;
        }
        const texts = Array.from(el.querySelectorAll("a[href]"))
          .map((x) => clean(x.innerText))
          .filter(usable)
          .sort((x, y) => y.length - x.length);
        if (texts.length) return texts[0];
      }
      return "";
    }

    // The card's location, so the backend can filter on it. Deliberately
    // NARROW: only elements that SAY they're a location (class name, data
    // attribute, aria-label, schema.org) count. Guessing "the short line under
    // the title" would invent a location, and a wrong location gets a real job
    // filtered out silently — the exact failure this scan keeps having. An
    // unreadable location returns "" and the backend then KEEPS the job.
    function locationFromCard(a, clean) {
      const SEL = "[class*='location' i],[class*='Location'],[data-location]," +
                  "[itemprop='jobLocation'],[aria-label*='location' i]";
      let el = a;
      for (let i = 0; i < 5 && el.parentElement; i++) {
        el = el.parentElement;
        const n = el.querySelector(SEL);
        if (!n) continue;
        const t = clean(n.getAttribute("data-location") || n.innerText || "");
        if (t && t.length <= 80) return t;
      }
      return "";
    }
  }

  // The count the PAGE itself claims ("Showing 13-15 of 15", "75 result(s)").
  // Used only to cross-check coverage — a scan that finds a wildly different
  // number than the site advertises must say so rather than report confidently.
  // How many rows THIS page claims to show: "Displaying 461 to 463 of 463",
// "Showing 13-15 of 15", "201 - 220 of 462". Returns {from, to, total} when
// the page states a range. This is the site's OWN ground truth, so it works
// anywhere the pattern appears — no per-site rules.
// Is this page a bot check rather than the job list? Cloudflare/Akamai/
// PerimeterX interstitials render a normal-looking page with no jobs, so the
// capture happily records zero and the scan reports a clean undercount
// (user report 2026-07-22: a Cloudflare challenge appeared mid-scan and the
// walk silently stopped producing jobs). Detect it and say so.
function captureChallenge() {
  const t = (document.body.innerText || "").slice(0, 3000);
  const hit =
    /verify (you are|you're) human|checking your browser|needs to review the security|just a moment|attention required|are you a robot|enable javascript and cookies/i
      .test(t) ||
    !!document.querySelector(
      "#challenge-form, #cf-challenge-running, [id*='px-captcha'], iframe[src*='challenges.cloudflare.com']");
  return hit ? (t.split("\n").find((l) => l.trim().length > 10) || "").trim().slice(0, 120) : null;
}

function capturePageRange() {
  const text = (document.body.innerText || "").slice(0, 20000);
  const m = text.match(
    /\b([\d,]{1,7})\s*(?:to|[-–—])\s*([\d,]{1,7})\s+of\s+([\d,]{1,7})\b/i);
  if (!m) return null;
  const n = (x) => parseInt(String(x).replace(/,/g, ""), 10);
  const from = n(m[1]), to = n(m[2]), total = n(m[3]);
  if (!(from > 0 && to >= from && total >= to)) return null;
  return { from, to, total, expected: to - from + 1 };
}

function capturePageTotal() {
    const text = (document.body.innerText || "").slice(0, 20000);
    const pats = [
      // "of 462 matching jobs", "of 133 open roles" — allow adjectives between
    // the number and the noun (ServiceNow's wording slipped past, so a scan
    // that saw 23 of 462 reported no warning at all).
    /\bof\s+([\d,]{1,7})\s+(?:\w+\s+){0,2}(?:results?|jobs?|positions?|openings?|roles?|vacancies)\b/i,
      /\bshowing\s+[\d,]+\s*[-–—to]+\s*[\d,]+\s+of\s+([\d,]{1,7})/i,
      /\b([\d,]{1,7})\s+(?:results?|jobs?|positions?|openings?|vacancies)\b/i,
      /\b([\d,]{1,7})\s+result\(s\)/i,
    ];
    for (const re of pats) {
      const m = text.match(re);
      if (m) {
        const n = parseInt(m[1].replace(/,/g, ""), 10);
        if (n > 0 && n < 100000) return n;
      }
    }
    return null;
  }
}

// The next pagination page's URL, if any. Navigation only — no clicking.
// rel=next first, then next-ish link text, then a page=N+1 param.
function findNextHref() {
  const rel = document.querySelector('a[rel="next"], link[rel="next"]');
  if (rel && rel.href) return rel.href;
  const anchors = Array.from(document.querySelectorAll("a[href]"));
  const byText = anchors.find((a) => {
    const t = (a.innerText || a.getAttribute("aria-label") || "").trim().toLowerCase();
    return ["next", "next page", "›", ">", "»"].includes(t);
  });
  if (byText && /^https?:/.test(byText.href)) return byText.href;
  try {
    const cur = new URL(location.href);
    const p = parseInt(cur.searchParams.get("page") || "1", 10);
    for (const a of anchors) {
      try {
        const u = new URL(a.href);
        if (u.origin === cur.origin && u.pathname === cur.pathname &&
            parseInt(u.searchParams.get("page") || "0", 10) === p + 1) return a.href;
      } catch (e) { /* not a URL */ }
    }
    // JS-driven numbered pagination (javascript:pagination(N) — Delivery
    // Hero's attrax widget and friends): learn the max page number from the
    // pager labels, then build the next URL with the page param ourselves.
    let maxPage = 0;
    for (const el of document.querySelectorAll("a, button")) {
      const label = (el.getAttribute("aria-label") || el.innerText || "").trim();
      const m = label.match(/^(?:go to page\s+)?(\d{1,3})$/i);
      if (m && el.closest('[class*="pag" i], nav')) {
        maxPage = Math.max(maxPage, parseInt(m[1], 10));
      }
    }
    if (maxPage > p) {
      cur.searchParams.set("page", String(p + 1));
      return cur.toString();
    }
  } catch (e) { /* no URL API? */ }
  return null;
}

// A paginated careers page must be scanned from the START, not from whatever
// page the user happened to be on (user request 2026-07-21: they hit scan on
// ?page=7 and the first six pages were never seen). Returns the page-1 URL, or
// null if this URL isn't on a later page. Only unambiguous page params —
// a bare "p" is far more often a tracking token.
const PAGE_PARAMS = ["page", "pg", "pageNumber", "page_num", "pageIndex", "pageNo"];

function firstPageUrl(raw) {
  try {
    const u = new URL(raw);
    let changed = false;
    for (const k of PAGE_PARAMS) {
      const v = u.searchParams.get(k);
      if (v != null && /^\d+$/.test(v) && parseInt(v, 10) > 1) {
        u.searchParams.set(k, "1");
        changed = true;
      }
    }
    if (/\/page\/\d+\/?$/i.test(u.pathname) &&
        parseInt(u.pathname.match(/\/page\/(\d+)/i)[1], 10) > 1) {
      u.pathname = u.pathname.replace(/\/page\/\d+(\/?)$/i, "/page/1$1");
      changed = true;
    }
    return changed ? u.toString() : null;
  } catch (e) {
    return null;
  }
}

function waitForLoad(tabId, timeoutMs) {
  return new Promise((resolve) => {
    const done = (ok) => { chrome.tabs.onUpdated.removeListener(onUpd); resolve(ok); };
    const timer = setTimeout(() => done(false), timeoutMs || 20000);
    function onUpd(id, info) {
      if (id === tabId && info.status === "complete") {
        clearTimeout(timer);
        setTimeout(() => done(true), 1500);   // let client rendering settle
      }
    }
    chrome.tabs.onUpdated.addListener(onUpd);
  });
}

// ------------------------------------------------------------ scan session
// One scan at a time (per-user AI scans are deliberate and scoped — CLAUDE.md).
// scan.phase: "capturing" (this worker walks pagination) → "backend" (the
// local backend extracts/reads/scores; we poll it on the panel's behalf).
let scan = null;

async function saveScan() {
  if (scan && scan.scanId) {
    await chrome.storage.session.set({
      scanState: { scanId: scan.scanId, tabId: scan.tabId } });
  }
}

async function clearScan() {
  scan = null;
  await chrome.storage.session.remove("scanState");
}

// The worker can be killed by Chrome between events; a backend-phase scan is
// resumable from session storage (a capture-phase one is not — its loop died
// with the worker, and the panel reports that honestly).
async function restoreScan() {
  if (scan) return;
  const v = await chrome.storage.session.get(["scanState"]);
  if (v.scanState && v.scanState.scanId) {
    scan = { ...v.scanState, phase: "backend", stopRequested: false,
             readingPages: false, readProgress: null, error: "" };
  }
}

async function startScan(tabId) {
  await restoreScan();
  if (scan) throw new Error("A scan is already running.");
  scan = { tabId, phase: "capturing", scanId: null, page: 1, links: 0,
           stopRequested: false, readingPages: false, readProgress: null,
           rewound: false, shortPages: 0, missed: 0, challenged: "",
           plan: null, resolveConfirm: null, pagesScanned: 0,
           stalled: null, stallRetried: 0, error: "" };
  runCapture(tabId).catch((e) => {
    if (scan) { scan.phase = "error"; scan.error = e.message || String(e); }
  });
  return { started: true };
}

// What the walk is about to cost, read off page 1's own numbers. Never
// guessed: with no stated total there is no honest "N jobs across M pages" to
// show, and the scan just runs.
function scanPlan(page) {
  const range = page.pageRange || null;
  const total = page.pageTotal || (range && range.total) || 0;
  const per = (range && range.expected) || (page.cards || []).length || 0;
  return { total, per, pages: total && per ? Math.ceil(total / per) : 0 };
}

// Ask before a long walk (user request 2026-07-22). Small boards are not
// worth a click, so the prompt only appears when the walk is genuinely long.
// Returns false if the user cancelled.
async function confirmWalk(page) {
  const plan = scanPlan(page);
  if (!plan.total || (plan.total < 80 && plan.pages < 4)) return true;
  scan.plan = plan;
  scan.phase = "confirm";
  return await new Promise((resolve) => { scan.resolveConfirm = resolve; });
}

// The panel answered the "N jobs across M pages — proceed?" prompt.
async function confirmScan(go) {
  await restoreScan();
  if (!scan || scan.phase !== "confirm") return { ok: false };
  scan.phase = go ? "capturing" : "cancelled";
  const resolve = scan.resolveConfirm;
  scan.resolveConfirm = null;
  if (resolve) resolve(!!go);
  return { ok: true };
}

async function runCapture(tabId) {
  let tab = await chrome.tabs.get(tabId);
  if (!/^https?:/.test(tab.url || "")) throw new Error("Open a careers page first.");

  // Sites that keep filters in the URL FRAGMENT (HubSpot: #department=…)
  // apply them client-side, so ANY navigation reloads the page unfiltered —
  // paging through it would scan a different job list than the user is
  // looking at (measured 2026-07-21: their filtered 15 became the full 155).
  // In that case capture only what's on screen and say so.
  scan.hashFiltered = /^#.+=/.test(new URL(tab.url).hash || "");

  // Rewind to page 1 first (navigation only — never a click), so a scan
  // started deep in the pager still covers the whole filtered list.
  const rewind = scan.hashFiltered ? null : firstPageUrl(tab.url);
  if (rewind && !scan.stopRequested) {
    scan.rewound = true;
    await chrome.tabs.update(tabId, { url: rewind });
    await waitForLoad(tabId);
    tab = await chrome.tabs.get(tabId);
  }

  // Walk the pagination: capture every page of the user's filtered view,
  // navigating (never clicking) to each next page.
  const agg = { url: tab.url, title: tab.title || "", jsonld: [],
                anchors: [], cards: [], pageTotal: null, textParts: [] };
  const visited = new Set();
  // Cross-page state for the stall check below. Every page-level check we have
  // compares a page against ITSELF, which cannot see the failure that actually
  // costs the most jobs: a site whose pager doesn't respond to URL navigation
  // re-renders page 1 every time, and page 1 verifies perfectly against page
  // 1's own numbers (measured on careers.servicenow.com 2026-07-22 — ?page=1
  // and ?page=2 return the identical 20 job ids).
  const seenCards = new Set();
  let lastFrom = 0;
  for (let n = 1; n <= (scan.hashFiltered ? 1 : MAX_SCAN_PAGES); n++) {
    if (!scan || scan.stopRequested) break;
    scan.page = n;
    // Wait for the job list to STOP GROWING before capturing. A fixed delay
    // races client-side rendering: ServiceNow rendered ~10 of its 20 jobs
    // within 1.5s, so a 24-page walk collected 237 of 462 — half (user
    // report 2026-07-22).
    try {
      await chrome.scripting.executeScript({ target: { tabId },
                                             func: waitForListToSettle });
    } catch (e) { /* capture whatever is there */ }
    // Expand first, so the capture sees every job the page can show.
    try {
      await chrome.scripting.executeScript({ target: { tabId },
                                             func: expandListings });
    } catch (e) { /* page won't allow it — capture what's visible */ }
    // Capture, then CHECK it against the page's own stated range and retry
    // if we came up short. Every scan failure so far has been a silent
    // undercount — a fixed wait, a cap, a grouping bug — and none of them
    // announced themselves. Verifying each page against the site's own
    // numbers catches all of that class, on any site that states a range,
    // instead of needing a new rule per site (user request 2026-07-22).
    let page = {};
    for (let attempt = 0; attempt < 3; attempt++) {
      const [cap] = await chrome.scripting.executeScript({
        target: { tabId }, func: capturePage,
      });
      page = (cap && cap.result) || {};
      const want = page.pageRange && page.pageRange.expected;
      const got = (page.cards || []).length;
      if (!want || got >= want) break;          // no claim to check, or we met it
      scan.shortPages = (scan.shortPages || 0) + 1;
      await new Promise((r) => setTimeout(r, 1500 * (attempt + 1)));
    }
    if (page.challenge) {
      // A human has to clear this. Keep what we already captured and stop —
      // continuing would walk challenge pages and record zero jobs each.
      scan.challenged = page.challenge;
      break;
    }
    // Page 1 is captured; now we know what the whole walk costs and can ask.
    // The gate sits BEFORE aggregation so a cancel keeps nothing and posts
    // nothing — a cancelled scan must not quietly become a one-page scan.
    if (n === 1 && !(await confirmWalk(page))) return;
    if (!scan || scan.stopRequested) break;

    // Did this page actually ADVANCE? Two independent signals, because a site
    // may state a range, or repeat jobs, or both:
    //   - its stated "from" went up ("Displaying 21 to 40" after "1 to 20"),
    //   - it contributed at least one job URL we hadn't already collected.
    // Neither is a guess: both are read off the page. A walk that stalls here
    // is collecting page 1 over and over, and every within-page check passes
    // while it happens.
    const knownBefore = seenCards.size;
    for (const c of page.cards || []) seenCards.add(c.url);
    const gained = seenCards.size - knownBefore;
    const from = (page.pageRange && page.pageRange.from) || 0;
    const movedOn = n === 1 || gained > 0 || (from > 0 && from > lastFrom);
    if (from) lastFrom = from;
    if (!movedOn) {
      // Back off and look again before giving up. A stall is most often the
      // site throttling a walk that's moving fast (careers.servicenow.com
      // served page 1 for every page and threw a Cloudflare check at a cold
      // browser, while paging by hand works) — so the retries WAIT, they
      // don't just re-read. Two tries, 5s then 15s, then stop honestly.
      scan.stallRetried = (scan.stallRetried || 0) + 1;
      if (scan.stallRetried <= 2) {
        await new Promise((r) => setTimeout(r, scan.stallRetried === 1 ? 5000 : 15000));
        n--;                       // re-capture this same page
        continue;
      }
      scan.stalled = { page: n, collected: seenCards.size };
      break;
    }
    scan.stallRetried = 0;
    scan.pagesScanned = n;
    // Remember the worst shortfall so the panel can report it honestly.
    if (page.pageRange && page.pageRange.expected) {
      const got = (page.cards || []).length;
      if (got < page.pageRange.expected) {
        scan.missed = (scan.missed || 0) +
          (page.pageRange.expected - got);
      }
    }
    // 10 per page threw away 19 of a page's 20 JobPosting blobs on any board
    // that renders one per card. Bound the AGGREGATE instead, so a long walk
    // still can't blow the payload up.
    if (agg.jsonld.length < 3000) agg.jsonld.push(...(page.jsonld || []));
    agg.anchors.push(...(page.anchors || []));
    agg.cards.push(...(page.cards || []));
    // The list's own total comes from page 1 — later pages repeat it.
    if (agg.pageTotal == null) agg.pageTotal = page.pageTotal || null;
    agg.textParts.push((page.visibleText || "").slice(0, 20000));
    scan.links = agg.anchors.length;
    // What the panel shows while capturing: JOBS collected so far, deduped
    // across pages. The anchor count above is an internal budget (every nav
    // and filter link on the page counts toward it) and meant nothing to
    // anyone reading the panel.
    scan.jobsFound = seenCards.size;
    if (agg.cards.length >= MAX_SCAN_CARDS || agg.anchors.length > MAX_LINKS) break;

    const [nx] = await chrome.scripting.executeScript({
      target: { tabId }, func: findNextHref,
    });
    const nextUrl = nx && nx.result;
    if (!nextUrl || visited.has(nextUrl) || !scan || scan.stopRequested) break;
    visited.add(nextUrl);
    await chrome.tabs.update(tabId, { url: nextUrl });
    await waitForLoad(tabId);
  }

  if (!scan) return;
  if (scan.stopRequested && agg.anchors.length === 0) {
    await clearScan();
    return;
  }
  const payload = { url: agg.url, title: agg.title, jsonld: agg.jsonld,
                    anchors: agg.anchors.slice(0, MAX_LINKS),
                    // THE cap that lost ServiceNow's board: 24 pages captured
                    // 463 cards correctly and 300 were posted (cap audit
                    // 2026-07-22). Never re-introduce a bound here that is
                    // tighter than the one the walk itself stops on.
                    cards: agg.cards.slice(0, MAX_SCAN_CARDS),
                    pageTotal: agg.pageTotal,
                    hashFiltered: scan.hashFiltered,
                    missedRows: scan.missed || 0,
                    challenged: scan.challenged || "",
                    pagesScanned: scan.pagesScanned || 1,
                    // Set when the site's pager stopped responding to URL
                    // navigation. A MEASURED cause, so the coverage note is
                    // allowed to name it.
                    stalledAtPage: (scan.stalled && scan.stalled.page) || 0,
                    visibleText: agg.textParts.join("\n").slice(0, 100000) };
  const d = await api("/api/extension/scan", {
    method: "POST", body: JSON.stringify(payload),
  });
  scan.scanId = d.scan_id;
  scan.phase = "backend";
  await saveScan();
  if (scan.stopRequested) {
    // Stop arrived while we were still capturing — pass it through.
    try {
      await api(`/api/extension/scan/${scan.scanId}/stop`, { method: "POST" });
    } catch (e) { /* the status poll will surface the state either way */ }
  }
}

// The panel polls this every 2s while a scan runs; reactions that must not
// depend on the panel staying open (rendered-tab fallback, lost-continue
// retry) happen HERE, not in content.js.
async function scanStatus() {
  await restoreScan();
  if (!scan) return { none: true };
  if (scan.phase === "error") {
    const err = scan.error;
    await clearScan();
    return { phase: "error", error: err };
  }
  if (scan.phase === "cancelled") {
    await clearScan();
    return { phase: "cancelled" };
  }
  if (scan.phase === "confirm") {
    return { phase: "confirm", plan: scan.plan };
  }
  if (scan.phase === "capturing") {
    return { phase: "capturing", page: scan.page, links: scan.links,
             jobs: scan.jobsFound || 0,
             totalPages: (scan.plan && scan.plan.pages) || 0,
             rewound: scan.rewound, hashFiltered: scan.hashFiltered,
             missed: scan.missed || 0, challenged: scan.challenged || "",
             stopping: scan.stopRequested };
  }
  if (scan.readingPages) {
    // The background-tab loop drives progress; don't hammer the backend.
    return { phase: "backend", reading: true, readProgress: scan.readProgress };
  }
  try {
    const s = await api(`/api/extension/scan/${scan.scanId}`);
    if (TERMINAL.includes(s.state)) {
      await clearScan();
      return { phase: "done", status: s };
    }
    if (s.state === "need_pages" && (s.pending || []).length && !scan.readingPages) {
      // The backend hands these back in BATCHES now, so the progress numbers
      // have to come from it — a per-batch count would restart at 0 every 25
      // jobs and look like the scan was going backwards.
      readPendingPages(scan.scanId, s.pending,
                       s.pending_total || s.pending.length,
                       s.pending_done || 0);      // async — never awaited here
    } else if (s.state === "need_pages" && !(s.pending || []).length && !scan.readingPages) {
      // All pages delivered but the continue call was lost (backend hiccup —
      // a Supabase blip once left Stop visible forever). Retry; idempotent.
      await api(`/api/extension/scan/${scan.scanId}/continue`, { method: "POST" });
    }
    return { phase: "backend", status: s, stopping: scan.stopRequested };
  } catch (e) {
    await clearScan();
    return { phase: "error", error: e.message || String(e) };
  }
}

async function stopScan() {
  await restoreScan();
  if (!scan) return { ok: true };
  scan.stopRequested = true;
  // Stop while the confirm prompt is up: release the waiting capture loop,
  // or it sits on a promise nothing will ever resolve.
  if (scan.resolveConfirm) {
    const resolve = scan.resolveConfirm;
    scan.resolveConfirm = null;
    scan.phase = "cancelled";
    resolve(false);
    return { ok: true };
  }
  if (scan.scanId) {
    try {
      await api(`/api/extension/scan/${scan.scanId}/stop`, { method: "POST" });
    } catch (e) { /* capture-phase stop — the local flag is enough */ }
  }
  return { ok: true };
}

// Rendered-tab fallback (bot-blocked sites — a real Delivery Hero scan failed
// 76/76 server-side description reads): open each pending job page in ONE
// background tab, capture what the browser rendered, and hand it to the
// backend. One page at a time, stoppable, tab closed after.
async function readPendingPages(scanId, urls, total, done) {
  if (!scan || scan.readingPages) return;
  scan.readingPages = true;
  scan.readProgress = { done: done || 0, total: total || urls.length };
  let worker = null;
  try {
    worker = await chrome.tabs.create({ active: false, url: "about:blank" });
    for (const u of urls) {
      if (!scan || scan.stopRequested) break;
      try {
        await chrome.tabs.update(worker.id, { url: u });
        await waitForLoad(worker.id);
        const [cap] = await chrome.scripting.executeScript({
          target: { tabId: worker.id }, func: capturePage,
        });
        const page = (cap && cap.result) || {};
        await api(`/api/extension/scan/${scanId}/page`, {
          method: "POST",
          body: JSON.stringify({ url: u, jsonld: page.jsonld || [],
                                 visibleText: page.visibleText || "" }),
        });
      } catch (e) { /* one unreadable page never aborts the batch */ }
      if (scan && scan.readProgress) scan.readProgress.done++;
    }
  } finally {
    if (worker) { try { await chrome.tabs.remove(worker.id); } catch (e) { /* gone */ } }
    if (scan && scan.stopRequested) {
      try { await api(`/api/extension/scan/${scanId}/stop`, { method: "POST" }); }
      catch (e) { /* the status poll will surface it */ }
    }
    try { await api(`/api/extension/scan/${scanId}/continue`, { method: "POST" }); }
    catch (e) { /* scanStatus() retries this if it was lost */ }
    if (scan) { scan.readingPages = false; scan.readProgress = null; }
  }
}

// ------------------------------------------------------------- apply session
// applyUrl = the saved job this application belongs to. Survives navigation
// (posting → ATS form is always a navigation) in session storage.
async function getApplyUrl() {
  const v = await chrome.storage.session.get(["applyUrl"]);
  return v.applyUrl || null;
}

// The job context (title/company/description) read from the POSTING has to
// survive the navigation to the ATS form — the content script dies on it and
// the form itself usually carries no description (decision 2026-07-22).
async function getApplyContext() {
  const v = await chrome.storage.session.get(["applyContext"]);
  return v.applyContext || null;
}

async function setApplyContext(ctx) {
  if (ctx) await chrome.storage.session.set({ applyContext: ctx });
  else await chrome.storage.session.remove("applyContext");
  return { ok: true };
}

async function setApplyUrl(url) {
  if (url) {
    await chrome.storage.session.set({ applyUrl: url });
  } else {
    await chrome.storage.session.remove(["applyUrl", "applyContext", "autofillPending"]);
  }
  return { ok: true };
}

// "Apply with autofill" / "Tailor first" navigate the tab from the posting to
// the ATS form, which kills the content script. This flag survives that hop so
// the panel, once it re-attaches on the form, fills the step by itself — the
// user pressed one button and expects one action, not a second "Autofill" click
// (redesign 2026-07-24). Cleared as soon as it fires, and when the session ends.
async function getAutofillPending() {
  const v = await chrome.storage.session.get(["autofillPending"]);
  return !!v.autofillPending;
}

async function setAutofillPending(on) {
  if (on) await chrome.storage.session.set({ autofillPending: true });
  else await chrome.storage.session.remove("autofillPending");
  return { ok: true };
}

async function applyDetect(tabId) {
  // Real ATS forms very often live inside an IFRAME (embedded Greenhouse/
  // Lever/Ashby boards) — the first live autofill attempt found 0 fields
  // because detect only ran in the top frame (user report 2026-07-19).
  // Inject the page agent into EVERY frame and aggregate; field ids get a
  // "frameId:" namespace so fill/attach can route back to the right frame.
  let target = { tabId, allFrames: true };
  try {
    await chrome.scripting.executeScript({
      target, files: ["apply.js"], world: "MAIN",
    });
  } catch (e) {
    // Some frame refused injection (sandboxed ad iframe etc.) and the whole
    // call failed — fall back to the top frame alone rather than dying.
    target = { tabId };
    await chrome.scripting.executeScript({
      target, files: ["apply.js"], world: "MAIN",
    });
  }
  const results = await chrome.scripting.executeScript({
    target, world: "MAIN",
    func: () => (window.__applybro
      ? { fields: window.__applybro.detect(),
          controls: document.querySelectorAll("input, textarea, select").length }
      : null),
  });
  const fields = [];
  let controls = 0;
  let injected = 0;
  for (const r of results || []) {
    if (!r || !r.result) continue;
    injected++;
    controls += r.result.controls || 0;
    for (const f of r.result.fields || []) {
      f.id = `${r.frameId}:${f.id}`;
      fields.push(f);
    }
  }
  if (!injected) {
    // Never report an injection failure as "no empty fields" — that lie is
    // exactly what made this bug look like a detection problem.
    throw new Error("Couldn't read this page's form (script injection blocked).");
  }
  return { fields, controls };
}

// "frameId:localId" → [frameId, localId]; bare ids belong to the top frame.
function splitFid(fid) {
  const m = /^(\d+):([\s\S]*)$/.exec(String(fid));
  return m ? [parseInt(m[1], 10), m[2]] : [0, String(fid)];
}

async function applyFill(tabId, values, unresolved) {
  // apply.js installs the capture-phase submit guard while filling and
  // removes it in a `finally` — the human always submits.
  const byFrame = new Map();
  const bucket = (frameId) => {
    if (!byFrame.has(frameId)) byFrame.set(frameId, { values: {}, unresolved: [] });
    return byFrame.get(frameId);
  };
  for (const [fid, spec] of Object.entries(values || {})) {
    const [frameId, local] = splitFid(fid);
    bucket(frameId).values[local] = spec;
  }
  for (const fid of unresolved || []) {
    const [frameId, local] = splitFid(fid);
    bucket(frameId).unresolved.push(local);
  }
  let filled = 0;
  let highlighted = 0;
  for (const [frameId, b] of byFrame) {
    try {
      const [res] = await chrome.scripting.executeScript({
        target: { tabId, frameIds: [frameId] }, world: "MAIN",
        func: (v, u) => window.__applybro.fill(v, u),
        args: [b.values, b.unresolved],
      });
      filled += (res && res.result && res.result.filled) || 0;
      highlighted += (res && res.result && res.result.highlighted) || 0;
    } catch (e) { /* frame navigated away — its fields stay for the human */ }
  }
  return { filled, highlighted };
}

function b64OfBuffer(buf) {
  // Chunked — String.fromCharCode(...bigArray) blows the arg limit on
  // larger PDFs.
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(s);
}

async function applyAttach(tabId, applyUrl, fieldIds, which) {
  const res = await fetch((await backendUrl()) +
    `/api/extension/apply/resume?url=${encodeURIComponent(applyUrl)}` +
    `&which=${encodeURIComponent(which || "tailored")}`);
  if (!res.ok) return { attached: 0 };
  const data = b64OfBuffer(await res.arrayBuffer());
  let attached = 0;
  for (const fid of fieldIds || []) {
    const [frameId, local] = splitFid(fid);
    try {
      const [a] = await chrome.scripting.executeScript({
        target: { tabId, frameIds: [frameId] }, world: "MAIN",
        func: (id, d, name) => window.__applybro.attach(id, d, name),
        args: [local, data, "resume.pdf"],
      });
      if (a && a.result && a.result.ok) attached++;
    } catch (e) { /* frame gone — user attaches by hand */ }
  }
  return { attached };
}

// ------------------------------------------------------------------ routing
async function handle(msg, sender) {
  const tabId = sender.tab && sender.tab.id;
  switch (msg.type) {
    case "api":         return api(msg.path, msg.opts);
    case "getState": {
      await restoreScan();
      // scanHere: the scan is running in THIS tab — the only tab whose panel
      // should auto-open and poll for it.
      return { scanActive: !!scan,
               scanHere: !!(scan && scan.tabId === tabId),
               applyUrl: await getApplyUrl(),
               applyContext: await getApplyContext(),
               autofillPending: await getAutofillPending() };
    }
    case "startScan":   return startScan(tabId);
    case "confirmScan": return confirmScan(!!msg.go);
    case "scanStatus":  return scanStatus();
    case "stopScan":    return stopScan();
    case "setApplyUrl": return setApplyUrl(msg.url || null);
    case "setApplyContext": return setApplyContext(msg.ctx || null);
    case "setAutofillPending": return setAutofillPending(!!msg.on);
    case "applyDetect": return applyDetect(tabId);
    case "applyFill":   return applyFill(tabId, msg.values, msg.unresolved);
    case "applyAttach": return applyAttach(tabId, msg.url, msg.fieldIds, msg.which);
    default:            throw new Error(`unknown message: ${msg.type}`);
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  handle(msg, sender).then(sendResponse,
    (e) => sendResponse({ error: e.message || String(e) }));
  return true;   // async response
});

// Toolbar icon toggles the in-page panel. This has to be SELF-HEALING, not
// best-effort: the content script can be missing or dead for ordinary reasons
// — a tab that was open before the extension loaded, or (very common in beta)
// an unpacked-extension RELOAD, which orphans the content script already in
// every open tab so it can no longer receive messages. The old handler just
// sent "toggle" and swallowed the failure, so on all those pages clicking the
// icon did nothing (user report 2026-07-23).
//
// So: try to toggle a live panel; if nothing answers, inject the content
// script and open it. Injecting is safe to repeat — content.js tears down any
// stale panel and re-initialises, so a fresh working panel always results.
chrome.action.onClicked.addListener(async (tab) => {
  if (!tab || !tab.id || !/^https?:/.test(tab.url || "")) return;  // no CS on chrome://
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "toggle" });
    return;                                    // a live panel answered
  } catch (e) {
    // No live content script — inject it, then open the panel.
  }
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id },
                                           files: ["content.js"] });
    await chrome.tabs.sendMessage(tab.id, { type: "show" });
  } catch (e) {
    /* a page that forbids injection (some chrome-store / PDF viewers) —
       nothing we can do, and better to fail quietly than throw. */
  }
});
