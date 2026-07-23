import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { ProfileTab } from "./Settings.jsx";

const LAYER0_COMMAND =
  "Turn my resume PDF into content.yaml — read CLAUDE.md first, this is the " +
  "Layer 0 content/layout split.";

const STEP_LABELS = [
  "Google Cloud credential",
  "Connect your Google account",
  "Pick your spreadsheet",
  "Your application answers",
  "Resume master",
];

function stepFromStatus(s) {
  if (!s) return 0;
  if (!s.credentials_json) return 0;
  if (!s.token_json) return 1;
  if (!s.spreadsheet_configured) return 2;
  if (!s.profile_yaml) return 3;
  return 4;
}

function StepDots({ step }) {
  return (
    <div className="row" style={{ gap: 6, marginBottom: 20 }}>
      {STEP_LABELS.map((label, i) => (
        <span
          key={label}
          className={"chip " + (i === step ? "" : i < step ? "chip-success" : "chip-neutral")}
          style={{ opacity: i > step ? 0.6 : 1 }}
        >
          {i + 1}. {label}
        </span>
      ))}
    </div>
  );
}

function CredentialsStep({ status, onRecheck, checking }) {
  return (
    <div className="card">
      <div className="card-title">1. Create a free Google Cloud OAuth credential</div>
      <p className="meta">
        ApplyBro reads your target-company spreadsheet and (read-only) your Gmail for outcome
        tracking, using YOUR OWN free Google credential — nothing is sent to any ApplyBro server;
        there isn't one.
      </p>
      <ol className="meta" style={{ paddingLeft: 18 }}>
        <li>
          Open the{" "}
          <a href="https://console.cloud.google.com/apis/dashboard" target="_blank" rel="noreferrer">
            Google Cloud Console
          </a>{" "}
          and create a project (free tier).
        </li>
        <li>
          Enable the <b>Google Sheets API</b> and <b>Gmail API</b> for that project.
        </li>
        <li>
          Under "Credentials", create an <b>OAuth client ID</b> of type <b>Desktop app</b>.
        </li>
        <li>
          Download the JSON file it gives you, rename it <code>credentials.json</code>, and place
          it in the ApplyBro project root (next to <code>config.example.yaml</code>).
        </li>
      </ol>
      {status && !status.credentials_json && (
        <p className="meta" style={{ color: "var(--danger)" }}>
          credentials.json not found yet.
        </p>
      )}
      {status && status.credentials_json && (
        <p className="meta" style={{ color: "var(--champagne)" }}>
          credentials.json found ✓
        </p>
      )}
      <button className="btn btn-primary" onClick={onRecheck} disabled={checking}>
        {checking ? "Checking…" : "I've added it — check again"}
      </button>
    </div>
  );
}

function OAuthStep({ status, onRecheck }) {
  const [starting, setStarting] = useState(false);
  const [authStatus, setAuthStatus] = useState(null);
  const [error, setError] = useState("");
  const pollRef = useRef(null);

  function stopPolling() {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = null;
  }

  async function start() {
    setError("");
    setStarting(true);
    try {
      await api.setupAuthStart();
      pollRef.current = setInterval(async () => {
        try {
          const s = await api.setupAuthStatus();
          setAuthStatus(s);
          if (s.done || s.token_present) {
            stopPolling();
            setStarting(false);
            onRecheck();
          } else if (s.error) {
            stopPolling();
            setStarting(false);
          }
        } catch (e) {
          /* transient — keep polling */
        }
      }, 1200);
    } catch (e) {
      setError(e.message);
      setStarting(false);
    }
  }

  useEffect(() => () => stopPolling(), []);

  return (
    <div className="card">
      <div className="card-title">2. Connect your Google account</div>
      <p className="meta">
        Opens Google's own consent screen in your default browser (not this dashboard's window).
        Scope is read/write on Sheets (to log applications) and read-only Gmail (to classify
        outcomes) — never send, delete, or modify email.
      </p>
      {status?.token_json ? (
        <p className="meta" style={{ color: "var(--champagne)" }}>Connected ✓</p>
      ) : (
        <>
          <button className="btn btn-primary" onClick={start} disabled={starting}>
            {starting ? (
              <>
                <span className="spinner" /> Waiting for consent in your browser…
              </>
            ) : (
              "Connect Google account"
            )}
          </button>
          {authStatus?.error && (
            <p className="meta" style={{ color: "var(--danger)", marginTop: 8 }}>
              {authStatus.error}
            </p>
          )}
          {error && (
            <p className="meta" style={{ color: "var(--danger)", marginTop: 8 }}>{error}</p>
          )}
        </>
      )}
    </div>
  );
}

function SpreadsheetStep({ onDone }) {
  const [spreadsheetId, setSpreadsheetId] = useState("");
  const [tabs, setTabs] = useState(null);
  const [headersByTab, setHeadersByTab] = useState({});
  const [tab, setTab] = useState("");
  const [companyCol, setCompanyCol] = useState("");
  const [urlCol, setUrlCol] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function fetchTabs() {
    setBusy(true);
    setError("");
    try {
      const r = await api.setupSpreadsheet(spreadsheetId.trim());
      setTabs(r.tabs);
      setHeadersByTab(r.headers_by_tab);
      const firstTab = r.tabs[0] || "";
      setTab(firstTab);
      if (firstTab) {
        const g = await api.guessColumns(firstTab);
        setCompanyCol(g.company_name_column || "");
        setUrlCol(g.career_url_column || "");
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function onTabChange(newTab) {
    setTab(newTab);
    try {
      const g = await api.guessColumns(newTab);
      if (g.company_name_column) setCompanyCol(g.company_name_column);
      if (g.career_url_column) setUrlCol(g.career_url_column);
    } catch {
      /* ignore — user can fill columns manually */
    }
  }

  async function finish() {
    setBusy(true);
    setError("");
    try {
      await api.putSource({
        tab,
        company_name_column: companyCol,
        career_url_column: urlCol,
      });
      onDone();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="card-title">3. Pick your target-company spreadsheet</div>
      <p className="meta">
        The Google Sheet that lists companies you're targeting (with their careers-page URL) —
        ApplyBro also uses it to log discovered/applied jobs.
      </p>
      {error && <p className="meta" style={{ color: "var(--danger)" }}>{error}</p>}
      {!tabs ? (
        <>
          <div className="field">
            <label>Spreadsheet ID</label>
            <span className="hint">
              The long id in the sheet's URL: docs.google.com/spreadsheets/d/&lt;THIS_PART&gt;/edit
            </span>
            <input
              className="input"
              value={spreadsheetId}
              onChange={(e) => setSpreadsheetId(e.target.value)}
              placeholder="1AbCdE..."
            />
          </div>
          <button
            className="btn btn-primary"
            onClick={fetchTabs}
            disabled={busy || !spreadsheetId.trim()}
          >
            {busy ? "Checking…" : "Continue"}
          </button>
        </>
      ) : (
        <>
          <div className="field">
            <label>Tab</label>
            <select className="input" value={tab} onChange={(e) => onTabChange(e.target.value)}>
              {tabs.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
          <div className="row">
            <div className="field" style={{ flex: 1 }}>
              <label>Company name column</label>
              <input
                className="input"
                value={companyCol}
                onChange={(e) => setCompanyCol(e.target.value)}
              />
            </div>
            <div className="field" style={{ flex: 1 }}>
              <label>Careers URL column</label>
              <input className="input" value={urlCol} onChange={(e) => setUrlCol(e.target.value)} />
            </div>
          </div>
          <p className="meta">Columns are auto-guessed from "{tab}"'s header row — edit if wrong.</p>
          <button className="btn btn-primary" onClick={finish} disabled={busy}>
            {busy ? "Saving…" : "Save & continue"}
          </button>
        </>
      )}
    </div>
  );
}

function ProfileStep({ onDone }) {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .setupProfileInit()
      .then(() => setReady(true))
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <div className="card" style={{ borderColor: "var(--danger)" }}>{error}</div>;
  if (!ready) return <p className="meta">Setting up profile.yaml…</p>;

  return (
    <>
      <div className="card">
        <div className="card-title">4. Your application answers</div>
        <p className="meta">
          Standard form-fill answers (contact info, work authorization, etc). Only facts that are
          true — the autofiller types these into matching fields on real applications; nothing
          here is invented for you.
        </p>
      </div>
      <ProfileTab />
      <button className="btn btn-primary" onClick={onDone} style={{ marginTop: 12 }}>
        Continue
      </button>
    </>
  );
}

function ResumeStep({ onFinish }) {
  const [copied, setCopied] = useState(false);

  function copy() {
    navigator.clipboard?.writeText(LAYER0_COMMAND).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="card">
      <div className="card-title">5. Turn your resume into content.yaml</div>
      <p className="meta">
        This step is a <b>Claude Code judgment task</b>, not a button — separating your resume's
        content from its layout (so tailoring a job application can never accidentally change how
        the PDF looks) takes reading your actual PDF and reasoning about its structure. There is no
        Claude API key anywhere in ApplyBro: nothing here calls an LLM API; this happens in a
        Claude Code session you run yourself.
      </p>
      <ol className="meta" style={{ paddingLeft: 18 }}>
        <li>
          Put your current resume PDF in the <code>resume/Master Resume/</code> folder.
        </li>
        <li>
          Open Claude Code in this project directory and say:
          <div className="row" style={{ marginTop: 8, gap: 8 }}>
            <code style={{ flex: 1, background: "var(--surface)", padding: "6px 10px", borderRadius: 6 }}>
              {LAYER0_COMMAND}
            </code>
            <button className="btn btn-secondary" onClick={copy}>
              {copied ? "Copied ✓" : "Copy"}
            </button>
          </div>
        </li>
        <li>
          Claude analyzes your resume's fonts/spacing/structure, writes <code>content.yaml</code>{" "}
          (your facts only — dates, titles, employers, bullets) and <code>template.typ</code> (the
          layout), then compiles a PDF you can compare pixel-for-pixel against the original.
        </li>
      </ol>
      <p className="meta">
        You can do this later — the dashboard works without it (scoring/tailoring just won't have
        anything to match against yet).
      </p>
      <button className="btn btn-primary" onClick={onFinish}>
        Take me to the dashboard
      </button>
    </div>
  );
}

export default function Setup() {
  const [status, setStatus] = useState(null);
  const [step, setStep] = useState(0);
  const [checking, setChecking] = useState(false);
  const navigate = useNavigate();
  const initializedRef = useRef(false);

  async function refresh() {
    setChecking(true);
    try {
      const s = await api.getSetupStatus();
      setStatus(s);
      if (!initializedRef.current) {
        setStep(stepFromStatus(s));
        initializedRef.current = true;
      }
      return s;
    } finally {
      setChecking(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function advanceAfter(fn) {
    await fn();
    const s = await refresh();
    setStep(stepFromStatus(s));
  }

  return (
    <div className="content">
      <h1>Set up ApplyBro</h1>
      <p className="meta">
        A one-time walkthrough — your own free Google credential, your own spreadsheet, your own
        data. Nothing here is hosted by ApplyBro; everything stays on this machine.
      </p>
      <StepDots step={step} />

      {step === 0 && (
        <CredentialsStep
          status={status}
          checking={checking}
          onRecheck={() => advanceAfter(async () => {})}
        />
      )}
      {step === 1 && (
        <OAuthStep status={status} onRecheck={() => advanceAfter(async () => {})} />
      )}
      {step === 2 && <SpreadsheetStep onDone={() => advanceAfter(async () => {})} />}
      {step === 3 && <ProfileStep onDone={() => advanceAfter(async () => {})} />}
      {step === 4 && <ResumeStep onFinish={() => navigate("/")} />}

      {step > 0 && (
        <button className="btn btn-ghost" onClick={() => setStep(step - 1)} style={{ marginTop: 8 }}>
          ← back
        </button>
      )}
    </div>
  );
}
