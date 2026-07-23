import { useEffect, useState } from "react";
import { api } from "../api.js";

// Tracking, applications-first (pivot 2026-07-22). The dashboard no longer
// tracks JOBS — the extension is where jobs are found, scored and applied to.
// What's left here is the record: one row per application, plus whatever
// Gmail has since said about it. Only rejections are matched for now;
// everything else reads "no reply yet" rather than being guessed at.

function prettyDate(iso) {
  if (!iso) return "";
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

function daysSince(iso) {
  if (!iso) return null;
  const d = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(d.getTime())) return null;
  return Math.floor((Date.now() - d.getTime()) / 86400000);
}

function StatTile({ label, value }) {
  return (
    <div className="stat-tile">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

function Row({ a }) {
  const rejected = a.status === "rejected";
  const age = daysSince(a.date_applied);
  return (
    <div className="card" style={{ marginBottom: 10 }}>
      <div className="row-between">
        <div style={{ minWidth: 0 }}>
          <strong>
            {a.url ? (
              <a href={a.url} target="_blank" rel="noopener noreferrer">
                {a.title || "Untitled role"}
              </a>
            ) : (a.title || "Untitled role")}
          </strong>
          <div className="meta">{a.company || "Unknown company"}</div>
        </div>
        <div style={{ textAlign: "right", flexShrink: 0 }}>
          <span className={`chip ${rejected ? "chip-danger" : "chip-neutral"}`}>
            {rejected ? `Rejected ${prettyDate(a.rejected_on)}` : "No reply yet"}
          </span>
          <div className="meta" style={{ marginTop: 4 }}>
            Applied {prettyDate(a.date_applied)}
            {age !== null && !rejected && ` · ${age} day${age === 1 ? "" : "s"} ago`}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Tracking() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [mailError, setMailError] = useState("");

  function load() {
    api.getApplications().then(setData).catch((e) => setError(e.message));
  }
  useEffect(load, []);

  // Gmail is a SEPARATE, optional signal: if it isn't set up, the list of
  // applications still works. Never let a mail failure hide the record.
  async function checkMail() {
    setRefreshing(true);
    setMailError("");
    try {
      await api.refreshTracking();
      load();
    } catch (e) {
      setMailError(e.message);
    } finally {
      setRefreshing(false);
    }
  }

  if (error) {
    return (
      <div className="content">
        <h1>Tracking</h1>
        <div className="card" style={{ borderColor: "var(--danger)", background: "var(--danger-soft)" }}>
          {error}
        </div>
      </div>
    );
  }
  if (!data) return <div className="content"><h1>Tracking</h1><p className="meta">Loading…</p></div>;

  const { applications, counts } = data;

  return (
    <div className="content">
      <div className="row-between">
        <h1>Tracking</h1>
        <button className="btn btn-secondary" onClick={checkMail} disabled={refreshing}>
          {refreshing ? "Checking Gmail…" : "Check Gmail for replies"}
        </button>
      </div>
      <p className="meta">
        Every job you applied to through the ApplyBro extension. Gmail is read
        only to spot rejections — nothing is ever sent, deleted or replied to.
      </p>

      {mailError && (
        <div className="card" style={{ borderColor: "var(--danger)", background: "var(--danger-soft)" }}>
          Couldn't check Gmail: {mailError}
          <div className="meta">Your applications below are unaffected.</div>
        </div>
      )}

      {applications.length === 0 ? (
        <div className="empty-state">
          <h3>No applications yet</h3>
          <p className="meta">
            Apply to a job with the ApplyBro extension and click “I've applied” —
            it shows up here.
          </p>
        </div>
      ) : (
        <>
          <div className="stat-row" style={{ margin: "16px 0" }}>
            <StatTile label="Applications" value={counts.total} />
            <StatTile label="Awaiting reply" value={counts.waiting} />
            <StatTile label="Rejected" value={counts.rejected} />
          </div>
          {applications.map((a) => (
            <Row key={`${a.url}|${a.date_applied}|${a.title}`} a={a} />
          ))}
        </>
      )}
    </div>
  );
}
