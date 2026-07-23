// ApplyBro page agent — injected into the application page (MAIN world).
//
// SAFETY (CLAUDE.md, mirrors backend/autofill/forms.py): nothing here submits
// an application. There is no .submit()/.requestSubmit() call, no element
// .click(), and no Enter keypress anywhere. While ApplyBro fills, a
// capture-phase guard blocks the submit event, blocks Enter in inputs, and
// neuters the form.submit / requestSubmit methods (which fire no event); the
// guard is removed the moment filling ends so the HUMAN can submit. Checked
// mechanically by scripts/check_extension_never_submits.py.
//
// The backend decides every value; this script only reads the form and types
// what it's told. Anything the backend can't resolve is highlighted, never
// guessed.
(() => {
  const ATTR = "data-applybro-fid";
  const SKIP_TYPES = new Set(["hidden", "submit", "button", "image", "reset"]);

  // ATS UIs increasingly render the real controls inside OPEN shadow roots —
  // SmartRecruiters' spl-* web components hid the entire Easy Apply form from
  // a plain document.querySelectorAll (user report 2026-07-19: "says none of
  // them are empty even though the fields are all empty"). Every DOM lookup
  // in this agent goes through these deep helpers.
  function allRoots() {
    const roots = [document];
    for (let i = 0; i < roots.length; i++) {
      for (const el of roots[i].querySelectorAll("*")) {
        if (el.shadowRoot) roots.push(el.shadowRoot);
      }
    }
    return roots;
  }

  function deepQueryAll(sel) {
    const out = [];
    for (const r of allRoots()) out.push(...r.querySelectorAll(sel));
    return out;
  }

  function deepQuery(sel) {
    for (const r of allRoots()) {
      const hit = r.querySelector(sel);
      if (hit) return hit;
    }
    return null;
  }

  function visible(el) {
    if (!el || el.disabled) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.visibility !== "hidden" && s.display !== "none";
  }

  function labelText(el) {
    // Label lookups are scoped to the element's OWN root: a shadow input's
    // <label for=…> lives beside it in the same shadow root, not in document.
    const root = el.getRootNode();
    const id = el.getAttribute("id");
    if (id && root.querySelector) {
      const lab = root.querySelector(`label[for="${CSS.escape(id)}"]`);
      if (lab && lab.innerText.trim()) return lab.innerText.trim();
    }
    const ll = el.getAttribute("aria-labelledby");
    if (ll) {
      const byId = (i) => (root.getElementById && root.getElementById(i)) || document.getElementById(i);
      const t = ll.split(/\s+/).map((i) => (byId(i) || {}).textContent || "").join(" ").trim();
      if (t) return t;
    }
    if (el.getAttribute("aria-label")) return el.getAttribute("aria-label").trim();
    const wrap = el.closest("label");
    if (wrap && wrap.innerText.trim()) return wrap.innerText.trim();
    if (el.placeholder) return el.placeholder.trim();
    // fieldset/legend for radio/checkbox groups
    const fs = el.closest("fieldset");
    if (fs) {
      const lg = fs.querySelector("legend");
      if (lg && lg.innerText.trim()) return lg.innerText.trim();
    }
    return (el.name || "").trim();
  }

  function nativeSet(el, value) {
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    // Real typing fires a COMPOSED input event (it crosses shadow
    // boundaries — frameworks may delegate-listen outside the component's
    // root); change is not composed natively, so it stays plain.
    el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // ---- detection: visible, empty, fillable controls on the current step ----
  function detect() {
    // Stale tags from a previous step would collide with this run's ids
    // (fill() grabs the FIRST element with a matching fid).
    deepQueryAll(`[${ATTR}]`).forEach((el) => el.removeAttribute(ATTR));
    let i = 0;
    const out = [];
    const seenRadioGroups = new Set();
    const controls = deepQueryAll("input, textarea, select");
    for (const el of controls) {
      const tag = el.tagName.toLowerCase();
      const type = (el.getAttribute("type") || "").toLowerCase();
      if (tag === "input" && SKIP_TYPES.has(type)) continue;
      if (!visible(el)) continue;

      let kind;
      if (tag === "select") kind = "select";
      else if (type === "file") kind = "file";
      else if (type === "checkbox") kind = "checkbox";
      else if (type === "radio") kind = "radio";
      else if (tag === "textarea") kind = "textarea";
      else kind = "text";

      // skip already-filled controls (never overwrite the user)
      if (kind === "checkbox" || kind === "radio") {
        // handled at group level below
      } else if (kind === "file") {
        if (el.files && el.files.length) continue;
      } else if (kind === "select") {
        // A select ALWAYS has a value (its first option), so the generic
        // value check read every untouched "Please select…" dropdown as
        // filled and hid it. Index 0 selected = untouched; only skip
        // selects genuinely set to something further down.
        if (el.selectedIndex > 0 && (el.value || "").trim()) continue;
      } else if (el.value && el.value.trim()) continue;

      const label = labelText(el);
      const item = { label, kind };

      if (kind === "radio") {
        const name = el.name || label;
        if (seenRadioGroups.has(name)) continue;
        seenRadioGroups.add(name);
        // The GROUP's question lives in the fieldset legend; labelText(el)
        // returns the first option's own label ("Yes") when each radio is
        // wrapped in a <label> — a wrong question the backend can't resolve.
        const fs = el.closest("fieldset");
        const lg = fs && fs.querySelector("legend");
        if (lg && lg.innerText.trim()) item.label = lg.innerText.trim();
        const group = name && el.getRootNode().querySelectorAll
          ? el.getRootNode().querySelectorAll(`input[type="radio"][name="${CSS.escape(name)}"]`)
          : [el];
        if (Array.from(group).some((r) => r.checked)) continue; // already answered
        item.options = Array.from(group).map((r) => labelText(r)).filter(Boolean);
        // tag the group; fill picks the matching radio
        item.id = String(i++);
        group.forEach((r) => r.setAttribute(ATTR, item.id));
      } else {
        item.id = String(i++);
        el.setAttribute(ATTR, item.id);
        if (kind === "select") {
          item.options = Array.from(el.options).map((o) => o.text.trim()).filter(Boolean);
        }
        if (kind === "text" || kind === "textarea") {
          const ml = el.getAttribute("maxlength");
          if (ml && /^\d+$/.test(ml)) item.maxlength = parseInt(ml, 10);
        }
      }
      out.push(item);
    }
    return out;
  }

  // --------------------------------- submit guard (see file header) --------
  function guardOn() {
    if (window.__applybroGuard) return;
    const g = { blocked: 0 };
    g.onSubmit = (e) => { g.blocked++; e.preventDefault(); e.stopImmediatePropagation(); };
    g.onEnter = (e) => {
      if (e.key === "Enter" && e.target && e.target.tagName !== "TEXTAREA") {
        g.blocked++; e.preventDefault(); e.stopImmediatePropagation();
      }
    };
    // The submit event is NOT composed — from a form inside a shadow root it
    // never reaches document, so the guard hooks EVERY root while filling.
    g.roots = allRoots();
    for (const r of g.roots) {
      r.addEventListener("submit", g.onSubmit, true);
      r.addEventListener("keydown", g.onEnter, true);
    }
    // Methods fire no submit event, so neuter them for the duration.
    g.origSubmit = HTMLFormElement.prototype.submit;
    g.origRequest = HTMLFormElement.prototype.requestSubmit;
    HTMLFormElement.prototype.submit = function () { g.blocked++; };
    HTMLFormElement.prototype.requestSubmit = function () { g.blocked++; };
    window.__applybroGuard = g;
  }

  function guardOff() {
    const g = window.__applybroGuard;
    if (!g) return 0;
    for (const r of g.roots || [document]) {
      r.removeEventListener("submit", g.onSubmit, true);
      r.removeEventListener("keydown", g.onEnter, true);
    }
    HTMLFormElement.prototype.submit = g.origSubmit;
    HTMLFormElement.prototype.requestSubmit = g.origRequest;
    delete window.__applybroGuard;
    return g.blocked;
  }

  // ---- fill: apply backend-resolved values; highlight what it couldn't ----
  function fill(values, unresolved) {
    guardOn();
    let filled = 0;
    let highlighted = 0;
    try {
      for (const [fid, spec] of Object.entries(values || {})) {
        const el = deepQuery(`[${ATTR}="${fid}"]`);
        if (!el) continue;
        try {
          if ("value" in spec && (el.tagName === "TEXTAREA" || el.tagName === "INPUT")) {
            nativeSet(el, String(spec.value));
            filled++;
          } else if ("option" in spec && el.tagName === "SELECT") {
            const opt = Array.from(el.options).find(
              (o) => o.text.trim().toLowerCase() === String(spec.option).toLowerCase());
            if (opt) {
              el.value = opt.value;
              el.dispatchEvent(new Event("change", { bubbles: true }));
              filled++;
            }
          } else if ("option" in spec) {
            // radio group tagged with this fid: check the matching label
            const group = deepQueryAll(`input[type="radio"][${ATTR}="${fid}"]`);
            let done = false;
            for (const r of group) {
              const rroot = r.getRootNode();
              const lab = (r.closest("label") ? r.closest("label").innerText
                : ((rroot.querySelector && rroot.querySelector(`label[for="${CSS.escape(r.id)}"]`)) || {}).innerText || "").trim();
              if (lab.toLowerCase() === String(spec.option).toLowerCase()) {
                r.checked = true;
                r.dispatchEvent(new Event("change", { bubbles: true }));
                done = true;
                break;
              }
            }
            if (done) filled++;
          } else if ("checked" in spec && el.type === "checkbox") {
            if (el.checked !== !!spec.checked) {
              el.checked = !!spec.checked;
              el.dispatchEvent(new Event("change", { bubbles: true }));
            }
            filled++;
          }
        } catch (e) { /* one field failing never aborts the pass */ }
      }
      for (const fid of unresolved || []) {
        const el = deepQuery(`[${ATTR}="${fid}"]`);
        if (el) {
          el.style.outline = "2px solid #B08D4A";
          el.title = "ApplyBro left this for you — please fill it in.";
          highlighted++;
        }
      }
    } finally {
      const blocked = guardOff(); // hand control back so YOU can submit
      return { filled, highlighted, blocked };
    }
  }

  // ---- resume attach: set a file input's files from bytes (no click) ----
  function attach(fid, b64, filename) {
    const el = deepQuery(`[${ATTR}="${fid}"]`)
      || deepQuery('input[type="file"]');
    if (!el) return { ok: false };
    try {
      const bin = atob(b64);
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      const file = new File([arr], filename, { type: "application/pdf" });
      const dt = new DataTransfer();
      dt.items.add(file);
      el.files = dt.files;
      el.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      // Did the page acknowledge it? (some uploaders never echo — reported so
      // the human double-checks, never claimed as done when unconfirmed.)
      let echoed = !!(el.files && el.files.length);
      return { ok: true, echoed };
    } catch (e) {
      return { ok: false };
    }
  }

  window.__applybro = { detect, fill, attach };
})();
