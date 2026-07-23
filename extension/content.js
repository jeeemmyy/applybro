// ApplyBro in-page panel (JobRight-style) — replaces the Chrome side panel
// (refactor 2026-07-19). Injected on every http(s) page but DORMANT: a
// floating bubble appears only when the page looks job-related (or a
// scan/apply session is active); clicking it expands the panel. The toolbar
// icon toggles the panel on any page.
//
// This file is a THIN VIEW. It dies on every navigation, so anything that
// must survive one — backend HTTP, the pagination capture loop, the
// rendered-tab fallback, the apply session — lives in background.js; this
// script re-attaches and resumes from that state on each page load.
//
// Never-submit doctrine applies here too: the panel navigates (location.href)
// and reads the DOM — it never clicks page elements, never dispatches keys,
// never submits (scripts/check_extension_never_submits.py). Panel inputs live
// in a closed shadow root, so they can't associate with any page <form>.
(() => {
  "use strict";
  if (window.__applybroPanelLoaded) return;
  window.__applybroPanelLoaded = true;

  // ------------------------------------------------------------------ UI
  const CSS = `
    :host { all: initial; }
    * { box-sizing: border-box; }
    .wrap {
      /* Neutral surface + one accent. No gradients, no chrome — the panel
         sits on someone else's page and must read as a quiet tool. */
      --bg: #FFFFFF; --surface: #F7F7F8; --line: #E8E8EB;
      --ink: #0B0B0F; --muted: #71717A;
      --accent: #4F46E5; --accent-soft: #EEF0FE;
      --ok: #15803D; --ok-soft: #E8F6EC;
      --warn: #B45309; --warn-soft: #FCF3E4;
      --danger: #DC2626;
      font: 400 13px/1.5 "Inter", ui-sans-serif, system-ui, -apple-system,
            "SF Pro Text", "Segoe UI", sans-serif;
      color: var(--ink);
      -webkit-font-smoothing: antialiased;
      font-feature-settings: "cv11", "ss01";
    }
    #bubble {
      position: fixed; right: 20px; bottom: 24px; z-index: 2147483647;
      width: 52px; height: 52px; border-radius: 16px; border: none;
      background: var(--accent); color: #fff; cursor: pointer;
      font: 600 16px/52px inherit; letter-spacing: .02em; text-align: center;
      box-shadow: 0 8px 24px rgba(79,70,229,.34);
      transition: transform .16s cubic-bezier(.2,.8,.2,1);
    }
    #bubble:hover { transform: translateY(-2px) scale(1.03); }

    /* Full-height drawer (user request 2026-07-21) — it minimizes to the
       bubble rather than closing, so long scans stay one click away. */
    #panel {
      position: fixed; top: 0; right: 0; bottom: 0; z-index: 2147483647;
      width: 392px; max-width: 100vw; display: flex; flex-direction: column;
      background: var(--bg); border-left: 1px solid var(--line);
      box-shadow: -18px 0 52px rgba(9,9,11,.10);
    }
    header {
      display: flex; align-items: center; gap: 10px;
      padding: 16px 18px 14px; border-bottom: 1px solid var(--line);
    }
    .mark {
      width: 26px; height: 26px; border-radius: 8px; flex: none;
      background: var(--accent); color: #fff;
      font: 600 12px/1 inherit;
      /* Flex centering, not line-height: a 26px line-box sits the glyphs a
         hair high (their cap-height is above the line's midpoint), which is
         the "AB isn't centered" report. Flex centres the actual glyph box. */
      display: flex; align-items: center; justify-content: center;
    }
    h1 { flex: 1; margin: 0; font-size: 14px; font-weight: 600;
         letter-spacing: -.01em; }
    .iconbtn {
      width: 28px; height: 28px; flex: none; border: none; border-radius: 8px;
      background: none; color: var(--muted); cursor: pointer;
      font: 400 15px/1 inherit;
    }
    .iconbtn:hover { background: var(--surface); color: var(--ink); }
    #body { flex: 1; overflow-y: auto; padding: 14px 18px 28px; }
    #body::-webkit-scrollbar { width: 10px; }
    #body::-webkit-scrollbar-thumb {
      background: #DEDEE2; border: 3px solid var(--bg); border-radius: 999px;
    }

    .meta { color: var(--muted); font-size: 12px; }
    .card {
      border: 1px solid var(--line); border-radius: 12px; padding: 14px;
      margin-top: 14px;
    }
    .eyebrow {
      font-size: 10.5px; font-weight: 600; letter-spacing: .07em;
      text-transform: uppercase; color: var(--muted);
    }
    .card p { margin: 6px 0 0; color: var(--muted); font-size: 12.5px; }

    button.act {
      display: block; width: 100%; padding: 11px 14px; margin-top: 12px;
      border: 1px solid transparent; border-radius: 10px; cursor: pointer;
      background: var(--accent); color: #fff;
      font: 600 13px/1.2 inherit; letter-spacing: -.005em;
      transition: filter .15s ease, transform .06s ease;
    }
    button.act:hover { filter: brightness(1.07); }
    button.act:active { transform: translateY(1px); }
    button.act.secondary {
      background: var(--bg); color: var(--ink); border-color: var(--line);
    }
    button.act.secondary:hover { background: var(--surface); filter: none; }
    button.act.danger {
      background: var(--bg); color: var(--danger); border-color: #F2D2D2;
    }
    button.act:disabled { opacity: .5; cursor: default; }

    /* Coverage warning: the scan read a different set than the user is
       looking at. Never quiet — a wrong "no relevant jobs" is the worst
       possible failure here. */
    #coverage, .warn {
      margin-top: 12px; padding: 10px 12px; border-radius: 10px;
      background: var(--warn-soft); color: var(--warn);
      font-size: 12px; line-height: 1.5;
    }
    /* The "N jobs across M pages — proceed?" prompt. Same warm treatment as
       the coverage band: it's the panel telling you what it's about to cost,
       not an error. */
    #scanPlan {
      margin: 12px 0 0; padding: 10px 12px; border-radius: 10px;
      background: var(--warn-soft); color: var(--warn);
      font-size: 12px; line-height: 1.5;
    }
    .field { margin-top: 12px; }
    .field label {
      display: block; margin-bottom: 4px;
      font-size: 11px; font-weight: 600; letter-spacing: .04em;
      text-transform: uppercase; color: var(--muted);
    }
    .field input, .field textarea {
      width: 100%; padding: 9px 10px; background: var(--bg); color: var(--ink);
      border: 1px solid var(--line); border-radius: 10px;
      font: 400 12.5px/1.45 inherit; resize: vertical;
    }
    .field input:focus, .field textarea:focus {
      outline: none; border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-soft);
    }
    #status, #applyStatus { margin-top: 12px; white-space: pre-line;
                            font-size: 12.5px; color: var(--muted); }
    #status.err, .err { color: var(--danger); }

    /* Progress lives on the STAGE that's running, not in one global bar —
       a bar that sits still at 55%% for two minutes reads as "stuck"
       (user request 2026-07-21). Stages with a known total get a real
       fraction; the two that are a single AI call get a moving stripe, which
       says "working" without faking a number. */
    .bar { height: 3px; margin-top: 7px; border-radius: 999px;
           background: var(--line); overflow: hidden; }
    .bar i { display: block; height: 100%; width: 0; border-radius: 999px;
             background: var(--accent);
             transition: width .45s cubic-bezier(.2,.8,.2,1); }
    .bar.busy i { width: 38%; animation: slide 1.3s ease-in-out infinite; }
    @keyframes slide {
      0%   { transform: translateX(-110%); }
      100% { transform: translateX(300%); }
    }
    /* Stage checklist — which steps are done, which is running now. */
    #stages { list-style: none; margin: 14px 0 0; padding: 0; }
    #stages li { display: flex; gap: 9px; align-items: flex-start;
                 padding: 7px 0; font-size: 12.5px; color: var(--muted); }
    #stages li + li { border-top: 1px solid var(--line); }
    #stages .label { flex: 1; }
    #stages .value { font-weight: 600; font-variant-numeric: tabular-nums;
                     color: var(--ink); white-space: nowrap; }
    #stages .dot {
      flex: none; width: 16px; height: 16px; margin-top: 1px;
      border: 1.5px solid var(--line); border-radius: 50%;
      font: 600 10px/1 inherit; color: transparent;
      /* Flex-centre the ✓ instead of line-height + text-align: the glyph was
         sitting low-left of the circle (user report 2026-07-23). */
      display: flex; align-items: center; justify-content: center;
    }
    #stages li.done .dot { background: var(--ok); border-color: var(--ok);
                           color: #fff; }
    #stages li.now { color: var(--ink); font-weight: 600; }
    #stages li.now .dot { border-color: var(--accent);
                          animation: pulse 1.5s ease-in-out infinite; }
    @keyframes pulse {
      0%, 100% { box-shadow: 0 0 0 0 var(--accent-soft); }
      50%      { box-shadow: 0 0 0 4px var(--accent-soft); }
    }
    #stages .detail { margin-top: 2px; font-weight: 400; color: var(--muted); }

    #results { margin-top: 20px; }
    #results h2 { margin: 0 0 2px; font-size: 12px; font-weight: 600; }
    .job {
      margin-top: 8px; padding: 12px; border: 1px solid var(--line);
      border-radius: 12px; transition: border-color .15s ease;
    }
    .job:hover { border-color: #D6D6DC; }
    .job .top { display: flex; gap: 10px; align-items: center; }
    .pill { flex: none; min-width: 44px; text-align: center;
            border-radius: 8px; padding: 4px 8px;
            font: 600 12px/1.3 inherit; font-variant-numeric: tabular-nums; }
    .pill.hit  { background: var(--ok-soft);   color: var(--ok); }
    .pill.near { background: var(--warn-soft); color: var(--warn); }
    .pill.miss { background: var(--surface);   color: var(--muted); }
    .job .t { flex: 1; font-size: 12.5px; font-weight: 600;
              letter-spacing: -.005em; }
    .job .t a { color: inherit; text-decoration: none; }
    .job .t a:hover { color: var(--accent); text-decoration: underline; }
    .job .t a::after { content: " ↗"; color: var(--muted); font-weight: 400; }
    /* The AI's reasoning is the point of this list, but a 5-line paragraph
       per job buries the next one — clamp, expand on click. */
    .job .r { margin-top: 7px; font-size: 12px; line-height: 1.55;
              color: var(--muted); overflow: hidden; display: -webkit-box;
              -webkit-box-orient: vertical; -webkit-line-clamp: 3; }
    .job.open .r { -webkit-line-clamp: unset; }
    .more { margin-top: 6px; padding: 0; border: none; background: none;
            color: var(--accent); font: 600 11.5px inherit; cursor: pointer; }
    .more:hover { text-decoration: underline; }

    select {
      width: 100%; margin-top: 8px; padding: 9px 10px; background: var(--bg);
      border: 1px solid var(--line); border-radius: 10px;
      font: 400 12.5px/1.3 inherit; color: var(--ink);
    }
    .row { display: flex; gap: 8px; margin-top: 12px; }
    .row button.act { flex: 1; margin-top: 0; }
    [hidden] { display: none !important; }
  `;

  const HTML = `
    <div class="wrap">
      <button id="bubble" title="ApplyBro" hidden>AB</button>
      <div id="panel" hidden>
        <header>
          <div class="mark">AB</div>
          <h1>ApplyBro</h1>
          <button id="close" class="iconbtn" title="Minimize">–</button>
        </header>
        <div id="body">
        <div class="meta" id="account">Connecting to ApplyBro…</div>

        <div class="card meta" id="notJobHint" hidden>
          Open a job posting, a company careers page, or an application form —
          ApplyBro's actions appear when there's something here to work on.
        </div>

        <!-- Exactly ONE of these three cards is ever visible: the page decides
             which. A careers list gets a single button and nothing else. -->
        <div class="card" id="scanCard">
          <div class="eyebrow">This careers page</div>
          <p id="scanBlurb">
            Every job visible with your current filters gets read and compared
            to your resume. The good ones land on your Jobs tab.
          </p>
          <button id="scan" class="act">Find Relevant Jobs</button>
          <button id="stopScan" class="act danger" hidden>Stop Scan</button>

          <!-- Long walks ask first: the board's OWN stated total and page
               count, so the user can back out of a 24-page scan they didn't
               realise they'd started. -->
          <div id="scanConfirm" hidden>
            <p id="scanPlan"></p>
            <div class="row">
              <button id="scanGo" class="act">Scan the whole board</button>
              <button id="scanNo" class="act secondary">Cancel</button>
            </div>
          </div>
        </div>

        <div class="card" id="applyCard">
          <div class="eyebrow">Apply with autofill</div>
          <p>
            ApplyBro reads the posting, fills what it can and highlights the
            rest — <b>you review and submit</b>. Nothing is ever submitted for
            you, and nothing is recorded until you say you applied.
          </p>
          <button id="applyStart" class="act">Apply to this job</button>
          <button id="switchApply" class="act secondary" hidden>
            Apply to THIS job instead (cancels the one in progress)</button>

          <div id="applyBody" hidden>
            <!-- What ApplyBro read off the posting. EDITABLE on purpose: the
                 page is the only source now (no saved job), so the user gets
                 to correct it before anything is tailored or recorded. -->
            <div id="dupWarn" class="warn" hidden></div>
            <div class="field">
              <label for="ctxTitle">Job title</label>
              <input id="ctxTitle" type="text" placeholder="e.g. Senior Fullstack Engineer">
            </div>
            <div class="field">
              <label for="ctxCompany">Company</label>
              <input id="ctxCompany" type="text" placeholder="e.g. Contentful">
            </div>
            <div class="field">
              <label for="ctxDesc">Job description</label>
              <textarea id="ctxDesc" rows="5"
                        placeholder="Paste the job description if ApplyBro couldn't find it"></textarea>
              <div class="meta" id="ctxDescNote"></div>
            </div>
            <button id="ctxSave" class="act secondary">Save these details</button>

            <button id="tailorFirst" class="act secondary" hidden>Tailor my resume first</button>
            <button id="fillStep" class="act">Autofill this step</button>
            <p class="meta">
              Advanced to the next page of the form? Click "Autofill this step"
              again for it.
            </p>
            <div id="resumeAsk" hidden>
              <p class="meta">This form has a resume upload. Attach your
              current resume as-is, or tailor it for this job first?</p>
              <button id="attachCurrent" class="act secondary">Attach current resume</button>
              <button id="tailorAttach" class="act secondary">Tailor first, then attach</button>
            </div>
            <div class="row">
              <button id="applyDone" class="act">I've applied</button>
              <button id="applyCancel" class="act secondary">Cancel</button>
            </div>
          </div>
          <!-- OUTSIDE applyBody: errors must show before a session starts —
               a hidden 409 ("application already in progress") made the Apply
               button look dead (live repro 2026-07-19). -->
          <div id="applyStatus"></div>
        </div>

        <!-- Shared progress/result area: whichever card is showing writes
             here, so no card carries its own duplicate status block. -->
        <ol id="stages" hidden></ol>
        <div id="coverage" hidden></div>
        <div id="status"></div>

        <!-- Live verdicts: what the AI is judging and how it landed. The bar
             alone told the user nothing about WHY 0 jobs matched. -->
        <div id="results" hidden>
          <h2>Jobs the AI read — review them yourself</h2>
          <div class="meta" id="resultsHint"></div>
          <div id="resultsList"></div>
        </div>
        </div>
      </div>
    </div>
  `;

  const host = document.createElement("div");
  const root = host.attachShadow({ mode: "closed" });
  const style = document.createElement("style");
  style.textContent = CSS;
  root.appendChild(style);
  const tpl = document.createElement("template");
  tpl.innerHTML = HTML;
  root.appendChild(tpl.content);
  (document.documentElement || document.body).appendChild(host);

  const $ = (id) => root.getElementById(id);

  // ------------------------------------------------------------ plumbing
  // All backend HTTP goes through the service worker: a content script's own
  // fetch runs under the PAGE's CORS/CSP and would be blocked on most sites.
  async function bg(msg) {
    const r = await chrome.runtime.sendMessage(msg);
    if (r && r.error) throw new Error(r.error);
    return r;
  }
  const api = (path, opts) => bg({ type: "api", path, opts });

  async function checkAccount() {
    try {
      const s = await api("/api/auth/status");
      if (s.logged_in) {
        $("account").textContent = `Connected as ${s.email}` +
          (s.account_status && s.account_status !== "active"
            ? ` — account ${s.account_status}` : "");
      } else if (s.supabase) {
        $("account").textContent = "Not signed in — sign in on the dashboard first.";
      } else {
        $("account").textContent = "Connected (local mode).";
      }
    } catch {
      $("account").textContent =
        "Can't reach ApplyBro — is the dashboard app running?";
    }
  }

  // Reads THIS page directly — the panel lives in it. Read-only.
  function capturePage() {
    const jsonld = Array.from(
      document.querySelectorAll('script[type="application/ld+json"]')
    ).map((s) => s.textContent).filter(Boolean).slice(0, 30);
    const anchors = Array.from(document.querySelectorAll("a[href]"))
      .slice(0, 2000)
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
      visibleText: (document.body.innerText || "").slice(0, 60000),
    };
  }

  function setCoverage(msg) {
    $("coverage").textContent = msg || "";
    $("coverage").hidden = !msg;
  }

  function setStatus(msg, isErr) {
    $("status").textContent = msg;
    $("status").className = isErr ? "err" : "";
  }

  function showPanel(show) {
    $("panel").hidden = !show;
    refreshContext();   // also decides the bubble (hidden while panel is open)
    if (show) checkAccount();
  }

  // --------------------------------------------------------------- scan
  // The capture loop navigates THIS tab, killing this script — background.js
  // owns it. We just start it, poll its status, and render.
  let pollTimer = null;
  let lastStatus = {};   // last backend status, for ticks that don't carry one

  // While a scan runs the start button is GONE, not just disabled (user
  // request 2026-07-21) — Stop is the only thing you can do.
  function scanUiRunning(running) {
    $("scan").hidden = running;
    $("scanBlurb").textContent = running
      ? "Working through this page — it can take a few minutes. You can keep " +
        "browsing; the scan carries on."
      : "Every job visible with your current filters gets read and compared " +
        "to your resume. The good ones land on your Jobs tab.";
    $("stopScan").hidden = !running;
    $("stopScan").disabled = false;
    if (!running) $("scanConfirm").hidden = true;
  }

  // "This board has N jobs across M pages — proceed?" Numbers come from the
  // SITE's own stated total, never from an estimate, so this only appears
  // when the page actually says how big it is.
  function showConfirm(plan) {
    const p = plan || {};
    // ~8s a page to settle, expand and capture. Said as "about", because it
    // is a real measurement of our own loop and nothing more — what happens
    // after collection depends on how many jobs are your kind of role, which
    // nothing here can know yet.
    const mins = Math.max(1, Math.round((p.pages * 8) / 60));
    $("scan").hidden = true;
    $("stopScan").hidden = true;
    $("stages").hidden = true;
    $("scanConfirm").hidden = false;
    $("scanGo").textContent = `Scan all ${p.total} jobs`;
    // A "Show all" page is one page with hundreds of jobs — it still deserves
    // the prompt (the reading is the cost, not the paging) but not a sentence
    // about walking 1 page.
    const cost = p.pages > 1
      ? `across about ${p.pages} pages. Collecting them takes roughly ` +
        `${mins} minute${mins === 1 ? "" : "s"}, and reading and scoring the ` +
        `ones that look like your kind of role takes longer again.`
      : `on this one page. Reading and scoring the ones that look like your ` +
        `kind of role takes a while.`;
    $("scanPlan").textContent =
      `This board says it has ${p.total} jobs ${cost} Narrow it first with ` +
      `the locations in Settings → Targets, or with this site's own filters, ` +
      `if that's more than you want.`;
  }

  // ONE funnel: the stages the scan goes through, each carrying its own
  // number and its own progress (user requests 2026-07-21). Ticked = done,
  // ringed = running now. No global bar — see the .bar CSS note.
  const num = (v) => (typeof v === "number" ? v : 0);
  let lastStage = 0;

  // `at` = index of the stage running now; everything before it is done.
  // `note` = an extra line for the current stage (time left, "stopping…").
  // Each row: [label, value, detail, fraction-done | null for "no total"].
  function renderFunnel(s, at, note) {
    lastStage = at;
    const found = num(s.found);
    const kept = num(s.to_assess);        // survived READING
    // Mid-triage: a total to divide by, and not yet finished.
    const triaging = at === 1 && num(s.triage_total) > 0 &&
                     num(s.triage_done) < num(s.triage_total);
    // Survived triage. While triage is still RUNNING this number is a partial
    // that keeps growing, so the reading row must not divide by it — "0 of 9"
    // out of a 466-job board reads as a finished decision.
    const triaged = triaging ? 0 : (num(s.kept_after_triage) || kept);
    const bar = num(s.threshold) || 60;
    const best = typeof s.top_score === "number" ? ` (best ${s.top_score}%)` : "";
    const rows = [
      [num(s.pages_scanned) > 1
         ? `Collecting the jobs — ${s.pages_scanned} pages scanned`
         : "Collecting the jobs on this page",
       found || "—",
       // The location filter is the user's own setting, so it says so by
       // name — a number that just shrank with no explanation is the same
       // silent undercount, only self-inflicted.
       s.location_skipped
         ? `${s.location_skipped} outside ${(s.locations || []).join(", ")}` +
           (s.location_unknown
              ? ` · ${s.location_unknown} kept (location not listed)` : "")
         : "",
       // Walking a known number of pages is a real fraction too.
       num(s.capture_total_pages)
         ? num(s.pages_scanned) / num(s.capture_total_pages) : null],
      // Triage is one AI call per 60 titles, so on a big board this used to
      // be a moving stripe for minutes with nothing behind it. It has a real
      // total, so it shows a real fraction.
      ["Picking which jobs are worth reading",
       triaging ? `${num(s.triage_done)} of ${num(s.triage_total)}`
                : num(s.kept_after_triage) || (at > 1 ? 0 : "—"),
       triaging
         ? `${num(s.kept_after_triage)} worth reading so far`
         : (s.skipped_by_title
              ? `${s.skipped_by_title} skipped — not your kind of role` : ""),
       triaging ? num(s.triage_done) / num(s.triage_total) : null],
      ["Reading job descriptions",
       triaged ? `${num(s.read)} of ${triaged}` : num(s.read) || "—",
       // "couldn't be read" and "never opened" are different facts and the
       // funnel says both — folding the second into the first blamed the
       // sites for jobs ApplyBro's own ceiling skipped.
       [s.failed ? `${s.failed} couldn't be read` : "",
        s.unattempted ? `${s.unattempted} never opened` : ""]
         .filter(Boolean).join(" · "),
       triaged
         ? (num(s.read) + num(s.failed) + num(s.unattempted)) / triaged
         : null],
      ["Comparing them to your resume",
       kept ? `${num(s.assessed)} of ${kept}` : "—", "",
       kept ? num(s.assessed) / kept : null],
      ["Added to your Jobs tab", num(s.relevant),
       [s.below_threshold
          ? `${s.below_threshold} scored under your ${bar}% bar${best}` : "",
        s.ai_errors ? `${s.ai_errors} the AI couldn't score` : ""]
         .filter(Boolean).join(" · "),
       null],
    ];
    $("stages").hidden = false;
    $("stages").innerHTML = rows.map(([label, value, detail, frac], i) => {
      const cls = i < at ? "done" : i === at ? "now" : "";
      const sub = [i === at ? note : "", detail].filter(Boolean).join(" · ");
      // Only the running stage shows a bar: a real fraction when there's a
      // total to divide by, a moving stripe when the step is one AI call.
      const prog = i !== at || at >= rows.length - 1 ? ""
        : frac == null
          ? `<div class="bar busy"><i></i></div>`
          : `<div class="bar"><i style="width:${
               Math.max(2, Math.min(100, Math.round(frac * 100)))}%"></i></div>`;
      return `<li class="${cls}"><span class="dot">✓</span>` +
             `<div class="label">${esc(label)}` +
             (sub ? `<div class="detail">${esc(sub)}</div>` : "") + prog +
             `</div><span class="value">${esc(value)}</span></li>`;
    }).join("");
  }

  // Plain English only — no "triage", no "assess" (user request 2026-07-21).
  // Returns [note for the current stage, stage index].
  function scanProgress(s) {
    if (s.cancel && !["stopped", "complete", "partial", "error"].includes(s.state)) {
      return ["stopping — finishing what's already in flight", lastStage];
    }
    if (s.state === "extracting") return ["", 0];
    if (s.state === "triaging") return ["", 1];
    if (s.state === "reading" || s.state === "need_pages") return ["", 2];
    if (s.state === "scoring") {
      const left = Math.ceil(((num(s.to_assess) - num(s.assessed)) * 15) / 60);
      return [left > 0 ? `about ${left} min left` : "", 3];
    }
    return ["", 4];
  }

  // Delegated: the list is re-rendered on every poll, so per-row listeners
  // would leak. "Read more" only appears when the text is actually clamped.
  $("resultsList").addEventListener("click", (e) => {
    const btn = e.target.closest && e.target.closest(".more");
    if (!btn) return;
    const card = btn.parentElement;
    const open = !card.classList.contains("open");
    card.classList.toggle("open", open);
    btn.textContent = open ? "Show less" : "Read more";
    if (open) expanded.add(card.dataset.url); else expanded.delete(card.dataset.url);
  });

  // Which reasons the user opened. The list re-renders on every poll while a
  // scan runs; without this, reading a long reason would collapse under you.
  const expanded = new Set();

  const esc = (v) => String(v == null ? "" : v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  // Every job the AI actually read, with its score and its reasoning — so a
  // "0 relevant" scan shows WHAT was considered and why it fell short.
  function renderResults(s) {
    const list = (s.results || []).slice()
      .sort((a, b) => (b.score == null ? -1 : b.score) - (a.score == null ? -1 : a.score));
    $("results").hidden = list.length === 0;
    if (!list.length) return;
    const bar = s.threshold || 60;
    $("resultsHint").textContent =
      `${list.length} job(s) read in full — ${bar}% or above is added ` +
      `automatically. Open any title to judge it yourself.`;
    $("resultsList").innerHTML = list.map((r) => {
      const cls = r.score == null ? "miss"
        : r.score >= bar ? "hit" : r.score >= bar - 15 ? "near" : "miss";
      const score = r.score == null ? "—" : `${r.score}%`;
      // New tab, so the scan's own tab is never navigated away from.
      const title = /^https?:/i.test(r.url || "")
        ? `<a href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(r.title)}</a>`
        : esc(r.title);
      const open = expanded.has(r.url) ? " open" : "";
      return `<div class="job${open}" data-url="${esc(r.url || "")}"><div class="top">` +
             `<span class="pill ${cls}">${esc(score)}</span>` +
             `<div class="t">${title}</div></div>` +
             (r.reason
               ? `<div class="r">${esc(r.reason)}</div>` +
                 `<button class="more">${open ? "Show less" : "Read more"}</button>`
               : "") +
             `</div>`;
    }).join("");
    // "Read more" only belongs on reasons the clamp actually truncated.
    $("resultsList").querySelectorAll(".job").forEach((card) => {
      const body = card.querySelector(".r");
      const btn = card.querySelector(".more");
      if (btn && body && !card.classList.contains("open") &&
          body.scrollHeight <= body.clientHeight + 1) {
        btn.hidden = true;
      }
    });
  }

  function endPoll() {
    clearInterval(pollTimer);
    pollTimer = null;
    scanUiRunning(false);
  }

  function renderScanTick(r) {
    if (r.none) {
      // Background lost the scan (worker restarted mid-capture) — be honest.
      endPoll();
      setStatus("Scan ended unexpectedly — run it again.", true);
      return;
    }
    scanUiRunning(true);
    if (r.phase === "error") {
      endPoll();
      setStatus(r.error || "Scan failed.", true);
      return;
    }
    if (r.phase === "confirm") {
      showConfirm(r.plan);
      setStatus("");
      return;
    }
    if (r.phase === "cancelled") {
      endPoll();
      // A deliberate cancel is not a failure and nothing was kept.
      setStatus("Scan cancelled — nothing was read.");
      return;
    }
    if (r.phase === "capturing") {
      $("scanConfirm").hidden = true;
      // "links so far" was the raw <a href> count — an internal budget that
      // counts every nav and filter link on the page. The panel shows JOBS.
      renderFunnel(
        { found: r.jobs, pages_scanned: r.page,
          capture_total_pages: r.totalPages },
        0,
        r.totalPages ? `page ${r.page} of about ${r.totalPages}`
                     : `page ${r.page}`);
      setStatus(r.stopping ? "Stopping…"
        : r.rewound ? "Started over from page 1 so the whole list gets scanned." : "");
      return;
    }
    if (r.reading && r.readProgress) {
      // During background-tab reading the backend isn't polled (so it isn't
      // hammered), which means lastStatus.read is frozen — the ONLY live
      // number here is readProgress.done. So drive the reading row's count
      // from it directly, and drop the duplicate "— N of M" from the note.
      const base = { ...(lastStatus || {}),
                     read: r.readProgress.done,
                     to_assess: r.readProgress.total,
                     kept_after_triage: r.readProgress.total };
      renderFunnel(base, 2, "opening each in a background tab in your browser");
      return;
    }
    if (r.phase === "done") {
      const s = r.status;
      lastStatus = s;
      setCoverage(s.coverage);
      renderResults(s);
      endPoll();
      renderFunnel(s, 5, "");   // every stage ticked; the bar is gone by now
      // The funnel already reports the outcome row by row, so a successful
      // scan says nothing extra (user request 2026-07-21). Only things the
      // funnel CAN'T show get a line: stops, failures, and the hint that the
      // fit bar itself may be the reason nothing landed.
      if (s.state === "complete" || s.state === "partial") {
        setStatus(s.error || (!s.relevant && s.below_threshold
          ? `Nothing cleared your ${s.threshold}% bar — lower it in Settings ` +
            `if that seems harsh.` : ""), !!s.error);
      } else if (s.state === "stopped") {
        setStatus(`Scan stopped. ${s.error || ""}`);
      } else {
        setStatus(`Scan failed: ${s.error || "unknown error"}`, true);
      }
      return;
    }
    if (r.status) {
      setCoverage(r.status.coverage);
      renderResults(r.status);
      lastStatus = r.status;
      const [note, idx] = scanProgress(r.status);
      renderFunnel(r.status, idx, note);
      setStatus(r.status.cancel || r.stopping ? "Stopping…" : "");
    }
  }

  function startPolling() {
    clearInterval(pollTimer);
    scanUiRunning(true);
    pollTimer = setInterval(async () => {
      try {
        renderScanTick(await bg({ type: "scanStatus" }));
      } catch (e) {
        endPoll();
        setStatus(e.message, true);
      }
    }, 2000);
  }

  $("scan").addEventListener("click", async () => {
    scanUiRunning(true);
    lastStatus = {};   // instant feedback; the first poll is 2s away
    setCoverage("");
    renderFunnel({}, 0, "");
    setStatus("");
    $("results").hidden = true;
    $("resultsList").innerHTML = "";
    try {
      await bg({ type: "startScan" });
      startPolling();
    } catch (e) {
      setStatus(e.message, true);
      scanUiRunning(false);
    }
  });

  $("scanGo").addEventListener("click", async () => {
    $("scanConfirm").hidden = true;
    renderFunnel({}, 0, "");
    try { await bg({ type: "confirmScan", go: true }); }
    catch (e) { setStatus(e.message, true); }
  });

  $("scanNo").addEventListener("click", async () => {
    $("scanConfirm").hidden = true;
    try { await bg({ type: "confirmScan", go: false }); }
    catch (e) { setStatus(e.message, true); }
  });

  $("stopScan").addEventListener("click", async () => {
    $("stopScan").disabled = true;
    setStatus("Stopping…");
    try { await bg({ type: "stopScan" }); }
    catch (e) { /* the status poll will surface the state */ }
  });

  // -------------------------------------------------------------- apply
  // applyUrl (the saved job this application belongs to) lives in the
  // background's session storage — posting → ATS form is always a
  // navigation, and this script doesn't survive those.
  let applyUrl = null;
  let applyTailored = false;   // has THIS session's job been tailored yet?
  let pendingAttach = [];      // resume file-input fids awaiting the user's choice

  function setApplyStatus(msg, isErr) {
    $("applyStatus").textContent = msg;
    $("applyStatus").style.color = isErr ? "#B3402A" : "";
  }

  // The href of the posting's own "Apply" link, so the panel can NAVIGATE
  // to the application form. Navigation only — nothing is clicked.
  function findApplyHref() {
    const cands = Array.from(document.querySelectorAll("a[href]"))
      .filter((a) => /^apply(\s+now)?$/i.test((a.innerText || "").trim()))
      .map((a) => a.href)
      .filter((h) => /^https?:/.test(h) && !/linkedin\.com|indeed\./i.test(h));
    return cands[0] || null;
  }

  // What this page says about the job. JSON-LD JobPosting first (ATS forms
  // and postings both carry it surprisingly often), then the page's own
  // heading plus its main text. Read-only, and never invented — a field it
  // can't find is left blank for the user to fill in.
  function readJobContext() {
    for (const el of document.querySelectorAll('script[type="application/ld+json"]')) {
      let data;
      try { data = JSON.parse(el.textContent || ""); } catch (e) { continue; }
      const stack = [data];
      while (stack.length) {
        const node = stack.pop();
        if (Array.isArray(node)) { stack.push(...node); continue; }
        if (!node || typeof node !== "object") continue;
        if (node["@type"] === "JobPosting" && node.title) {
          const org = node.hiringOrganization;
          return {
            title: String(node.title).trim(),
            company: String((org && org.name) || org || "").trim(),
            description: stripHtml(String(node.description || "")),
          };
        }
        stack.push(...Object.values(node));
      }
    }
    const h1 = document.querySelector("h1");
    const main = document.querySelector(
      "[class*='job-description' i], [class*='jobDescription' i], " +
      "[id*='job-description' i], article, main");
    return {
      title: (h1 && h1.innerText || "").replace(/\s+/g, " ").trim().slice(0, 160),
      company: "",
      description: ((main && main.innerText) || "").replace(/\s+/g, " ")
        .trim().slice(0, 20000),
    };
  }

  function stripHtml(html) {
    const d = document.createElement("div");
    d.innerHTML = html;
    return (d.textContent || "").replace(/\s+/g, " ").trim().slice(0, 20000);
  }

  function fillContextFields(ctx) {
    $("ctxTitle").value = ctx.title || "";
    $("ctxCompany").value = ctx.company || "";
    $("ctxDesc").value = ctx.description || "";
    const n = (ctx.description || "").length;
    $("ctxDescNote").textContent = n > 200
      ? `${n.toLocaleString()} characters read from the posting — edit if it grabbed the wrong text.`
      : "Couldn't read the description from this page. Paste it here so " +
        "ApplyBro can tailor your resume to it.";
  }

  function startedUI(d) {
    $("applyBody").hidden = false;
    sessionActive = true;
    applyTailored = !!d.tailored;
    refreshContext();
    fillContextFields({
      title: d.title || "", company: d.company || "",
      description: d.description || "",
    });
    // Advisory only — you can always apply anyway (decision 2026-07-22).
    const dup = d.duplicate;
    $("dupWarn").hidden = !dup;
    if (dup) {
      $("dupWarn").textContent =
        `You already applied to ${dup.title || "this job"}` +
        (dup.company ? ` at ${dup.company}` : "") +
        ` on ${dup.date_applied}. That's inside ${d.duplicate_days || 90} days — ` +
        `applying again is up to you.`;
    }
    $("tailorFirst").hidden = !(d.description || "").trim();
  }

  async function startApplyWith(url, ctx) {
    const d = await api("/api/extension/apply/start", {
      method: "POST",
      body: JSON.stringify({ url, ...(ctx || {}) }),
    });
    applyUrl = url;
    await bg({ type: "setApplyUrl", url });
    await bg({ type: "setApplyContext", ctx: {
      title: d.title || "", company: d.company || "",
      description: d.description || "",
    } });
    startedUI(d);
    return d;
  }

  async function beginApply() {
    setApplyStatus("");
    try {
      // Read the JD HERE, on the posting — the form we're about to navigate
      // to usually doesn't carry one (user decision 2026-07-22).
      const ctx = readJobContext();
      const d = await startApplyWith(location.href, ctx);
      if (!d) return;
      const href = findApplyHref();
      if (href) {
        setApplyStatus("Opening the application form…");
        location.href = href;   // navigation only — the page's own Apply link
      } else {
        setApplyStatus("Review the details above, then use \"Autofill this " +
          "step\" once the form is open.");
      }
    } catch (e) {
      setApplyStatus(e.message, true);
    }
  }
  $("applyStart").addEventListener("click", beginApply);

  // The user is the authority on these three fields — whatever they save is
  // what gets tailored against and what lands in Tracking.
  $("ctxSave").addEventListener("click", async () => {
    $("ctxSave").disabled = true;
    try {
      await api("/api/extension/apply/context", {
        method: "POST",
        body: JSON.stringify({
          url: applyUrl,
          title: $("ctxTitle").value.trim(),
          company: $("ctxCompany").value.trim(),
          description: $("ctxDesc").value.trim(),
        }),
      });
      await bg({ type: "setApplyContext", ctx: {
        title: $("ctxTitle").value.trim(),
        company: $("ctxCompany").value.trim(),
        description: $("ctxDesc").value.trim(),
      } });
      $("tailorFirst").hidden = !$("ctxDesc").value.trim();
      setApplyStatus("Saved ✓");
    } catch (e) {
      setApplyStatus(e.message, true);
    } finally {
      $("ctxSave").disabled = false;
    }
  });

  $("switchApply").addEventListener("click", async () => {
    $("switchApply").disabled = true;
    try {
      await api("/api/extension/apply/finish", {
        method: "POST",
        body: JSON.stringify({ url: applyUrl, outcome: "cancelled" }),
      });
      await bg({ type: "setApplyUrl", url: null });
      applyUrl = null;
      sessionActive = false;
      applyTailored = false;
      $("applyBody").hidden = true;
      $("resumeAsk").hidden = true;
      await beginApply();
    } catch (e) {
      setApplyStatus(e.message, true);
    } finally {
      $("switchApply").disabled = false;
      refreshContext();
    }
  });

  $("tailorFirst").addEventListener("click", async () => {
    setApplyStatus("Tailoring your resume for this job — usually 1–2 min…");
    $("tailorFirst").disabled = true;
    try {
      const d = await api("/api/extension/apply/tailor", {
        method: "POST", body: JSON.stringify({ url: applyUrl }),
      });
      setApplyStatus(`Tailored ✓ — fit ${d.fit_before}% → ${d.fit_after}%.`);
      $("tailorFirst").hidden = true;
      applyTailored = true;
      setApplyStatus("Tailored ✓ — now autofill the form.");
    } catch (e) {
      setApplyStatus(e.message, true);
    } finally {
      $("tailorFirst").disabled = false;
    }
  });

  $("fillStep").addEventListener("click", async () => {
    setApplyStatus("Reading this step and deciding what to fill…");
    $("fillStep").disabled = true;
    try {
      // 1. background injects apply.js into every frame and detects fields
      const det = await bg({ type: "applyDetect" });
      const fields = det.fields || [];
      if (fields.length === 0) {
        // Honest zero: "no empty fields" on a visibly empty form was a lie
        // (user report 2026-07-19) — say what was actually seen.
        setApplyStatus(det.controls
          ? `No fillable empty fields found — the ${det.controls} control(s) ` +
            `on this step are already filled, hidden, or of a kind ApplyBro ` +
            `can't fill. Fill this step by hand.`
          : "Couldn't find any form fields on this page. If you can see the " +
            "form, it's built in a way ApplyBro can't read yet — fill it by " +
            "hand this time.", true);
        return;
      }
      // 2. backend resolves values (same logic as the Playwright filler)
      const r = await api("/api/extension/apply/resolve", {
        method: "POST", body: JSON.stringify({ url: applyUrl, fields }),
      });
      // 3. fill (guard on during fill, off after so YOU can submit)
      const res = await bg({ type: "applyFill", values: r.values,
                             unresolved: r.unresolved });
      const left = (r.unresolved || []).length;
      setApplyStatus(
        `Filled ${res.filled} field(s)` +
        (left ? `. ${left} left for you (highlighted).` : ".") +
        `\nReview everything, then submit the form yourself.`);
      // 4. resume slot found: tailored resumes attach right away; otherwise
      //    the CHOICE is the user's — current resume as-is, or tailor first
      //    (user request 2026-07-19). Never attached silently.
      pendingAttach = r.attach_resume_to || [];
      if (pendingAttach.length) {
        if (applyTailored) {
          await doAttach("tailored");
        } else {
          $("resumeAsk").hidden = false;
        }
      }
    } catch (e) {
      setApplyStatus(e.message, true);
    } finally {
      $("fillStep").disabled = false;
    }
  });

  async function doAttach(which) {
    $("resumeAsk").hidden = true;
    try {
      const a = await bg({ type: "applyAttach", url: applyUrl,
                           fieldIds: pendingAttach, which });
      pendingAttach = [];
      setApplyStatus(a.attached
        ? `Attached your ${which === "master" ? "current" : "tailored"} ` +
          `resume ✓\nReview everything, then submit the form yourself.`
        : "Couldn't attach the resume — add it by hand (the upload field " +
          "may not accept scripted files).", !a.attached);
    } catch (e) {
      setApplyStatus(`Resume attach failed: ${e.message} — add it by hand.`, true);
    }
  }

  $("attachCurrent").addEventListener("click", () => doAttach("master"));

  $("tailorAttach").addEventListener("click", async () => {
    $("resumeAsk").hidden = true;
    setApplyStatus("Tailoring your resume for this job — usually 1–2 min…");
    try {
      const d = await api("/api/extension/apply/tailor", {
        method: "POST", body: JSON.stringify({ url: applyUrl }),
      });
      applyTailored = true;
      setApplyStatus(`Tailored ✓ — fit ${d.fit_before}% → ${d.fit_after}%.`);
      $("tailorFirst").hidden = true;
      await doAttach("tailored");
    } catch (e) {
      $("resumeAsk").hidden = false;   // let them pick again (or go current)
      setApplyStatus(`Tailoring failed: ${e.message}`, true);
    }
  });

  async function endApply(outcome) {
    try {
      await api("/api/extension/apply/finish", {
        method: "POST", body: JSON.stringify({ url: applyUrl, outcome }),
      });
    } catch (e) {
      setApplyStatus(e.message, true);
      return;
    }
    await bg({ type: "setApplyUrl", url: null });
    $("applyBody").hidden = true;
    $("resumeAsk").hidden = true;
    setApplyStatus("");
    applyUrl = null;
    sessionActive = false;
    applyTailored = false;
    pendingAttach = [];
    refreshContext();
    setStatus(outcome === "applied"
      ? "Marked applied — it's on your Jobs tab." : "Application cancelled.");
  }
  $("applyDone").addEventListener("click", () => endApply("applied"));
  $("applyCancel").addEventListener("click", () => endApply("cancelled"));

  // ------------------------------------------- contextual cards & bubble
  // Read-only probe of THIS page — nothing clicked.
  function probePage() {
    const inputs = Array.from(document.querySelectorAll("input, textarea, select"))
      .filter((e) => {
        const r = e.getBoundingClientRect();
        return r.width > 2 && r.height > 2;
      }).length;
    const jobLinks = Array.from(document.querySelectorAll("a[href]"))
      .filter((a) => /\/(jobs?|careers?|positions?|vacanc\w*|openings?)\//i.test(a.href))
      .length;
    const hasPosting = Array.from(
      document.querySelectorAll('script[type="application/ld+json"]')
    ).some((s) => (s.textContent || "").includes('"JobPosting"'));
    return { inputs, jobLinks, hasPosting, cards: jobCardCount() };
  }

  // How many jobs does this page LIST? Same structural signal the scan's
  // capture uses: job links share one URL template and repeat; nav links
  // don't. Counting "/jobs/"-style URLs missed SmartRecruiters entirely —
  // careers.smartrecruiters.com/Freshworks/744000… has no such segment, so a
  // careers page full of jobs was classified as a single posting and offered
  // the wrong card (user report 2026-07-22).
  const CARD_JOBISH =
    /(^|\/)(jobs?|careers?|positions?|vacanc\w*|openings?)(\/|$)|greenhouse|lever\.co|myworkdayjobs|smartrecruiters|ashbyhq|recruitee|workable|personio/i;

  function jobCardCount() {
        // Everything after the id is per-job noise: ServiceNow's
        // /jobs/<id>/<slug>/ gave every posting its own template, so no
        // group ever reached 3 and a 462-job board yielded 24 (user report
        // 2026-07-22). Cut at the id so all of a board's jobs group together.
    const tpl = (u) => {
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
    const groups = {};
    for (const a of document.querySelectorAll("a[href]")) {
      if (!/^https?:/.test(a.href)) continue;
      const t = tpl(a.href);
      if (!t || !CARD_JOBISH.test(t)) continue;
      groups[t] = (groups[t] || 0) + 1;
    }
    let best = 0;
    for (const k in groups) {
      if (groups[k] >= 3) best = Math.max(best, groups[k]);
    }
    return best;
  }

  const JOBISH_URL = /job|career|position|vacanc|opening|apply|greenhouse|lever\.co|smartrecruiters|workday|ashby|recruitee|workable|personio/i;
  const ATS_HOST = /greenhouse\.io|lever\.co|myworkdayjobs\.com|smartrecruiters\.com|ashbyhq\.com|recruitee\.com|workable\.com|personio\.(de|com)|bamboohr\.com|icims\.com|jobvite\.com|teamtailor\.com/i;

  // The bubble is unsolicited UI on someone else's page, so it needs a STRONG
  // job signal — the loose card heuristic put it on YouTube and Twitter,
  // where a stray /careers/ footer link was enough (user report 2026-07-19).
  // Only hostnames/paths that ARE job surfaces, a rendered JobPosting, or a
  // page dense with job links qualify.
  function strongJobSignal(p) {
    return p.hasPosting ||
           p.jobLinks >= 5 ||
           ATS_HOST.test(location.hostname) ||
           /job|career|talent|recruit|vacanc/i.test(location.hostname) ||
           /\/(jobs?|careers?|vacanc\w*|positions?|openings?|apply)([/?#]|$)/i
             .test(location.pathname);
  }

  let scanActive = false;
  let sessionActive = false;

  // What KIND of page is this? Exactly one answer, so the panel can show
  // exactly one card (user feedback 2026-07-21: a careers list offering Save,
  // Scan AND a stale apply session at once is why the panel felt buggy).
  //   list    — a careers/search results page: many job links, no posting
  //   posting — one job ad
  //   form    — an application form (fields to fill)
  //   other   — not a job surface at all
  function pageKind(p) {
    // A page that LISTS jobs wins over everything except a form: a posting
    // page shows one job, a list shows many, and the structural card count
    // is what tells them apart on any site.
    const listy = p.cards >= 3 || p.jobLinks > 5;
    const jobish = JOBISH_URL.test(location.hostname + location.pathname);
    if (p.inputs >= 4 && (ATS_HOST.test(location.hostname) || jobish) && !listy) {
      return "form";
    }
    if (p.hasPosting && !listy) return "posting";
    if (listy) return "list";
    if (p.hasPosting) return "posting";
    if (jobish || p.jobLinks > 0) return "posting";
    return "other";
  }

  // One card, chosen by the page — with two overrides: a running scan keeps
  // the scan card wherever its pagination walks, and a bound apply session
  // owns a posting/form page (that's where the form is).
  function refreshContext() {
    const p = probePage();
    const kind = pageKind(p);
    let show = kind;
    if (scanActive) show = "list";
    else if (sessionActive &&
             (kind === "posting" || kind === "form" || location.href === applyUrl)) {
      show = "form";
    }
    $("scanCard").hidden = show !== "list";
    $("applyCard").hidden = !(show === "form" || show === "posting");
    $("notJobHint").hidden = show !== "other";
    // An apply session that isn't showing its card stays SILENT here — the
    // "application in progress" line was noise on a careers page (user
    // request 2026-07-21: "Remove. Never show."). It's cancellable from the
    // apply card on the form itself, and from the dashboard.
    // While a session is bound, plain "Apply to this job" can only 409 —
    // replace it with an explicit switch (user report 2026-07-19: "This job
    // is different"). Same-page check keeps it off the session's own pages.
    $("applyStart").hidden = sessionActive;
    $("switchApply").hidden = !(sessionActive && location.href !== applyUrl);
    // The bubble is unsolicited UI on someone else's page: strong signal only.
    $("bubble").hidden = !(strongJobSignal(p) || scanActive ||
                           (sessionActive && kind !== "other")) ||
                         !$("panel").hidden;
    return kind !== "other";
  }

  $("bubble").addEventListener("click", () => showPanel(true));
  $("close").addEventListener("click", () => showPanel(false));

  // Toolbar icon → toggle, on any page (even non-jobish ones).
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === "toggle") showPanel($("panel").hidden);
  });

  // -------------------------------------------------- attach & resume
  // Fresh page load: dormant bubble on job-ish pages; if a scan or apply
  // session is mid-flight (this page load might BE the scan's pagination
  // step, or the ATS form the apply flow navigated to), reopen the panel
  // and pick the work back up.
  (async () => {
    let state = { scanHere: false, applyUrl: null };
    try { state = await bg({ type: "getState" }); }
    catch (e) { /* backend/worker unreachable — stay dormant */ }
    // Per-TAB: only the tab a scan is walking shows scan UI and polls it —
    // a scan must not light up every other page the user opens meanwhile.
    scanActive = !!state.scanHere;
    applyUrl = state.applyUrl || null;
    if (!applyUrl) {
      // The BACKEND may hold an application lock the extension lost track of
      // (Chrome restart, extension reload). Without this check the panel
      // offers a fresh "Apply to this job" that can only 409 (live repro
      // 2026-07-19: a stale Celonis session made every Apply click look dead).
      try {
        const s = await api("/api/extension/apply/session");
        if (s && s.url) {
          applyUrl = s.url;
          bg({ type: "setApplyUrl", url: applyUrl });
        }
      } catch (e) { /* backend not running — stay dormant */ }
    }
    sessionActive = !!applyUrl;
    const jobish = refreshContext();
    // Auto-open ONLY where the work is: the tab being scanned, or a page the
    // apply session plausibly continues on (the job's own page, or a job-ish
    // page like the ATS form it navigated to). A lingering session opening
    // the panel on Twitter/Gmail was the 2026-07-19 regression.
    if (scanActive || (sessionActive && (jobish || location.href === applyUrl))) {
      showPanel(true);
    }
    if (scanActive) startPolling();
    if (sessionActive) {
      // Re-bind the apply UI to the stored job (start is idempotent — it
      // re-returns the session's fit/tailored state).
      try { startedUI(await api("/api/extension/apply/start", {
        method: "POST", body: JSON.stringify({ url: applyUrl }) })); }
      catch (e) { /* backend gone — the Apply button still works manually */ }
    }
  })();
})();
