import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useAccount } from "../App.jsx";

function ThresholdCard({ threshold, setThreshold, save, saved }) {
  return (
    <div className="card">
      <div className="card-title">Fit threshold</div>
      <p className="meta">Default minimum fit % for a job to be staged in the review queue.</p>
      <input
        className="input"
        type="number"
        min={0}
        max={100}
        style={{ width: 140 }}
        value={threshold}
        onChange={(e) => setThreshold(e.target.value)}
      />
      <button className="btn btn-primary" onClick={save} style={{ marginLeft: 8 }}>
        {saved ? "Saved ✓" : "Save"}
      </button>
    </div>
  );
}

const FIELD_HELP = {
  salary_expectation: "Leave EMPTY to decide per job during review",
  visa_note: "One-liner about relocation/sponsorship",
  website: "Portfolio site (empty = skipped)",
};

// The old Source tab (Google Sheet company list) is gone: companies live
// locally in data/companies.json, managed on the Companies page.
const TABS = ["General", "Profile", "Resume", "Targets"];

// Exported (not just used below) so the /setup wizard's step 4 can reuse
// the exact same field editor instead of duplicating it — the spec calls
// for "the existing Me/Profile editor component", not a rewrite.
export function ProfileTab() {
  const [profile, setProfile] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getProfile().then(setProfile).catch((e) => setError(e.message));
  }, []);

  async function save() {
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const updated = await api.putProfile(profile);
      setProfile(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  if (error && !profile) {
    return <div className="card" style={{ borderColor: "var(--danger)" }}>{error}</div>;
  }
  if (!profile) return <p className="meta">Loading…</p>;

  return (
    <div className="card">
      <div className="card-title">Application answers (profile.yaml)</div>
      {error && <p className="meta" style={{ color: "var(--danger)" }}>{error}</p>}
      {Object.entries(profile).map(([k, v]) => (
        <div className="field" key={k}>
          <label>{k}</label>
          {FIELD_HELP[k] && <span className="hint">{FIELD_HELP[k]}</span>}
          <input
            className="input"
            value={v ?? ""}
            onChange={(e) => setProfile({ ...profile, [k]: e.target.value })}
          />
        </div>
      ))}
      <button className="btn btn-primary" onClick={save} disabled={saving}>
        {saving ? "Saving…" : saved ? "Saved ✓" : "Save"}
      </button>
    </div>
  );
}

// Upload-a-PDF entry point (SaaS Phase 2): the PDF's text is parsed into the
// structured facts everything else runs on. Parsed result lands in the
// editor below for REVIEW — nothing is saved until the user hits Save.
function ResumeUploadCard({ onParsed, onStatus }) {
  const [busy, setBusy] = useState(false);
  const [meta, setMeta] = useState({});

  useEffect(() => {
    api.resumeBase().then(setMeta).catch(() => {});
  }, []);

  function pick(e) {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!file) return;
    const reader = new FileReader();
    reader.onload = async () => {
      setBusy(true);
      onStatus("");
      try {
        const b64 = String(reader.result).split(",", 2)[1] || "";
        const d = await api.resumeUpload(file.name, b64);
        onParsed(d.content);
        onStatus("Parsed ✓ — review below (fix anything we misread), then Save.");
        api.resumeBase().then(setMeta).catch(() => {});
      } catch (err) {
        onStatus(`Couldn't parse — ${err.message}`);
      } finally {
        setBusy(false);
      }
    };
    reader.readAsDataURL(file);
  }

  return (
    <div className="card">
      <div className="card-title">Upload your resume (PDF)</div>
      <p className="meta">
        ApplyBro reads your PDF and extracts your real experience — that becomes the ground truth
        for job scoring and tailoring. Only facts found in the PDF are kept; nothing is invented.
      </p>
      <div className="row" style={{ gap: 10, alignItems: "center" }}>
        <label className={"btn btn-primary" + (busy ? " disabled" : "")} style={{ cursor: "pointer" }}>
          {busy ? "Reading your resume…" : meta.filename ? "Replace PDF" : "Upload PDF"}
          <input type="file" accept="application/pdf" style={{ display: "none" }}
                 disabled={busy} onChange={pick} />
        </label>
        {busy && <span className="spinner" />}
        {meta.filename && !busy && (
          <span className="meta">
            On file: {meta.filename}
            {meta.uploaded_at ? ` · ${String(meta.uploaded_at).slice(0, 10)}` : ""}
          </span>
        )}
      </div>
      {busy && (
        <p className="meta" style={{ marginTop: 8, marginBottom: 0 }}>
          Extracting text and structuring your experience — usually under two minutes.
        </p>
      )}
    </div>
  );
}

function ResumeTab() {
  const [content, setContent] = useState(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.getContent().then((d) => setContent(d.content)).catch((e) => setStatus(e.message));
  }, []);

  async function saveAndRebuild() {
    setBusy(true);
    setStatus("");
    try {
      await api.putContent(content);
      const r = await api.rebuildContent();
      setStatus(r.ok ? "Saved. PDF rebuilt ✓" : `Saved, but build FAILED: ${r.output.slice(-300)}`);
    } catch (e) {
      setStatus(`NOT saved — ${e.message}`);
    } finally {
      setBusy(false);
    }
  }

  if (content === null) return <p className="meta">Loading…</p>;

  return (
    <>
      <ResumeUploadCard onParsed={setContent} onStatus={setStatus} />
      {status && (
        <div className="card" style={{ background: "var(--navy-soft)" }}>
          {status}
        </div>
      )}
      <div className="card">
        <div className="card-title">Your experience (review & edit)</div>
        <p className="meta">
          What ApplyBro read from your resume — the ground truth for scoring and tailoring. Fix
          anything misread, then Save. Never add skills/dates/titles you don't have — tailored
          versions are checked against this.
        </p>
        <textarea
          className="input"
          style={{ width: "100%", height: 420, fontFamily: "ui-monospace, monospace", fontSize: 12 }}
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
        <div className="row" style={{ marginTop: 10, gap: 8 }}>
          <button className="btn btn-primary" onClick={saveAndRebuild} disabled={busy}>
            {busy ? "Working…" : "Save & rebuild PDF"}
          </button>
          <a href={api.resumePdfUrl()} target="_blank" rel="noreferrer">
            <button type="button" className="btn btn-secondary">
              Open current PDF
            </button>
          </a>
        </div>
      </div>

      <div className="card">
        <div className="card-title">Want a different look? (re-skin)</div>
        <p className="meta">
          The layout is cloned from a reference PDF and lives separately from your content, so you
          can change the design any time without losing a word of your data. It's a Claude task
          (design analysis), not a button:
        </p>
        <ol className="meta">
          <li>
            Put the PDF whose layout you like into the <code>resume/Master Resume/</code> folder (any
            filename).
          </li>
          <li>
            Open Claude Code in this project and say: <i>"Re-skin my resume to the layout of
            &lt;filename&gt; — re-run Layer 0 against it. Do not modify content.yaml."</i>
          </li>
          <li>
            Claude rebuilds <code>template.typ</code> to match the new design pixel-for-pixel (same
            measure-and-compare process as the original), renders YOUR existing content into it, and
            shows you a side-by-side.
          </li>
          <li>
            Approve it, and every future resume — master and all tailored versions — comes out in
            the new design automatically.
          </li>
        </ol>
        <p className="meta">
          Everything about you (experience, bullets, skills) survives untouched: it all lives in
          content.yaml, which a re-skin never edits.
        </p>
      </div>
    </>
  );
}

const LIST_STYLE = {
  width: "100%",
  fontFamily: "ui-monospace, monospace",
  fontSize: 13,
};

function TargetsTab() {
  const [keywords, setKeywords] = useState("");
  const [locations, setLocations] = useState("");
  const [departments, setDepartments] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    api.getTargets().then((d) => {
      setKeywords((d.role_keywords || []).join("\n"));
      setLocations((d.locations || []).join("\n"));
      setDepartments((d.departments || []).join("\n"));
    });
  }, []);

  const lines = (s) => s.split("\n").map((x) => x.trim()).filter(Boolean);

  async function save() {
    setSaving(true);
    setSaved(false);
    try {
      await api.putTargets(keywords.split("\n"), lines(locations), lines(departments));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div className="card">
        <div className="card-title">Roles you target (discovery keywords, one per line)</div>
        <p className="meta">Filters which jobs get FOUND; never adds skills to your resume.</p>
        <textarea
          className="input"
          style={{ ...LIST_STYLE, height: 200 }}
          value={keywords}
          onChange={(e) => setKeywords(e.target.value)}
        />
      </div>

      <div className="card">
        <div className="card-title">Locations you'd work in (one per line)</div>
        <p className="meta">
          A real filter: a scan drops jobs outside these before reading them, which is what
          makes a 460-job board affordable. Matching is generous both ways — "Berlin" keeps
          "Berlin, Germany" and "Germany" keeps it too, and "Remote" keeps "Remote — EMEA".
          A job whose location ApplyBro couldn't read is always <b>kept</b>, never quietly
          dropped. Leave empty to scan everywhere.
        </p>
        <textarea
          className="input"
          style={{ ...LIST_STYLE, height: 120 }}
          placeholder={"Remote\nBerlin\nGermany"}
          value={locations}
          onChange={(e) => setLocations(e.target.value)}
        />
      </div>

      <div className="card">
        <div className="card-title">Teams you're open to (one per line)</div>
        <p className="meta">
          <b>Not a filter — a hint that widens.</b> The same full-stack role sits under
          Engineering at one company, Product at the next and Customer Success at the third,
          so this only makes a scan more likely to KEEP a role in these areas. Nothing is
          ever excluded for being outside the list.
        </p>
        <textarea
          className="input"
          style={{ ...LIST_STYLE, height: 120 }}
          placeholder={"Engineering\nProduct\nPlatform"}
          value={departments}
          onChange={(e) => setDepartments(e.target.value)}
        />
      </div>

      <button className="btn btn-primary" onClick={save} disabled={saving}>
        {saving ? "Saving…" : saved ? "Saved ✓" : "Save"}
      </button>
    </>
  );
}

// Rendered in the backend's FEATURES order — the pipeline order (read pages
// first, then score, tailor, answer questions).
const FEATURE_LABELS = {
  extraction: "Reading career pages / job posts",
  fit: "Job fit scoring",
  tailoring: "Resume tailoring",
  qa: "Application question answers",
};

function GeneralTab() {
  const [threshold, setThreshold] = useState(60);
  const [saved, setSaved] = useState(false);
  const [ai, setAi] = useState(null); // {provider, has_key, key_hint, models, features, claude_models}
  const [keyInput, setKeyInput] = useState("");
  const [modelOptions, setModelOptions] = useState([]);
  const [modelsFallback, setModelsFallback] = useState(false);
  const [aiSaved, setAiSaved] = useState(false);
  const [aiError, setAiError] = useState("");

  function loadModelOptions(provider) {
    setAiError("");
    api.getAiModels(provider)
      .then((d) => {
        setModelOptions(d.models || []);
        setModelsFallback(Boolean(d.fallback));
        if (d.error) setAiError(d.error);
      })
      .catch((e) => setAiError(e.message));
  }

  useEffect(() => {
    api.getUiState().then((d) => setThreshold(d.fit_threshold ?? 60)).catch(() => {});
    api.getAi().then((d) => { setAi(d); loadModelOptions(d.provider); }).catch(() => {});
  }, []);

  async function save() {
    await api.putUiState({ fit_threshold: Number(threshold) });
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  }

  async function saveAi(patch) {
    setAiError("");
    try {
      const d = await api.putAi(patch);
      setAi(d);
      if (patch.provider) loadModelOptions(patch.provider);
      if (patch.openai_api_key) { setKeyInput(""); loadModelOptions(d.provider); }
      setAiSaved(true);
      setTimeout(() => setAiSaved(false), 2000);
    } catch (e) {
      setAiError(e.message);
    }
  }

  const provider = ai?.provider || "openai";
  const models = ai?.models || {};
  const features = ai?.features || ["fit", "tailoring", "qa", "extraction"];
  const account = useAccount();

  // Provider/model choice is ADMIN-only and lives on /admin (user request
  // 2026-07-22) — one place, not two. Nobody configures it here, not even
  // the admin: this page is the USER dashboard.
  return (
    <>
      <div className="card">
        <div className="card-title">AI</div>
        <p className="meta">
          AI in ApplyBro — job-fit scoring, resume tailoring, form answers, reading career
          pages — is managed by ApplyBro for your account. There's nothing to configure here.
        </p>
      </div>
      <ThresholdCard threshold={threshold} setThreshold={setThreshold} save={save} saved={saved} />
      <div className="card">
        <div className="card-title">Chrome profile reset</div>
        <p className="meta">
          If the dedicated Chrome window's saved logins get corrupted, quit ApplyBro entirely first
          (the profile is in use while it's running), then run:
        </p>
        <pre>rm -rf ~/.applybro/chrome-profile</pre>
        <p className="meta">This isn't a button here on purpose — it deletes saved logins/cookies.</p>
      </div>
    </>
  );
}

export default function Settings() {
  const [tab, setTab] = useState("General");

  return (
    <div className="content">
      <h1>Settings</h1>
      <div className="seg" style={{ marginBottom: 20 }}>
        {TABS.map((t) => (
          <button
            key={t}
            className={"seg-btn" + (tab === t ? " active" : "")}
            onClick={() => setTab(t)}
          >
            {t}
          </button>
        ))}
      </div>
      {tab === "General" && <GeneralTab />}
      {tab === "Profile" && <ProfileTab />}
      {tab === "Resume" && <ResumeTab />}
      {tab === "Targets" && <TargetsTab />}
    </div>
  );
}
