import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { IconTracking, IconSettings } from "../components/icons.jsx";
import { useAccount } from "../App.jsx";
import { useToast } from "../components/Toast.jsx";

// Admin-only, and its OWN surface at #/admin with its own left nav (user
// request 2026-07-22) — not a tab inside the user dashboard. Two sections:
//   Users    — approve accounts, manage subscriptions
//   Settings — per-user AI provider routing + model per task (this is what
//              used to be called "AI policy"; same thing, plainer name)
// Users never see provider/model choices; the admin sets them here. RLS
// enforces everything server-side, and the backend refuses self-suspend
// and self-unsubscribe.

const TABS = [
  { name: "Users", Icon: IconTracking },
  { name: "Settings", Icon: IconSettings },
];
const TASKS = ["extraction", "fit", "tailoring", "qa"];
const TASK_LABELS = {
  extraction: "Read pages", fit: "Fit scoring",
  tailoring: "Tailoring", qa: "Form answers",
};

function UsersTab({ users, myEmail, onPatch }) {
  return (
    <>
      {users.map((u) => {
        const self = u.email === myEmail;
        return (
          <div key={u.user_id} className="card" style={{ marginBottom: 10 }}>
            <div className="row-between">
              <div>
                <strong>{u.email || u.user_id}</strong>
                {self && <span className="meta"> (you)</span>}
                <span className={`chip ${u.status === "active" ? "chip-success" : "chip-neutral"}`}
                      style={{ marginLeft: 8 }}>
                  {u.status}
                </span>
                {u.subscribed
                  ? <span className="chip chip-success" style={{ marginLeft: 4 }}>subscribed</span>
                  : <span className="chip chip-neutral" style={{ marginLeft: 4 }}>unsubscribed</span>}
              </div>
              <div className="row" style={{ gap: 6 }}>
                {u.status !== "active" && (
                  <button className="btn btn-primary" onClick={() => onPatch(u.user_id, { status: "active" })}>
                    Approve
                  </button>
                )}
                {u.status === "pending" && (
                  <button className="btn btn-ghost" onClick={() => onPatch(u.user_id, { status: "rejected" })}>
                    Reject
                  </button>
                )}
                {u.status === "active" && !self && (
                  <button className="btn btn-ghost" onClick={() => onPatch(u.user_id, { status: "suspended" })}>
                    Suspend
                  </button>
                )}
                {!self && (
                  <button className="btn btn-secondary"
                          onClick={() => onPatch(u.user_id, { subscribed: !u.subscribed })}>
                    {u.subscribed ? "Unsubscribe" : "Subscribe"}
                  </button>
                )}
                {self && !u.subscribed && (
                  <button className="btn btn-secondary"
                          onClick={() => onPatch(u.user_id, { subscribed: true })}>
                    Subscribe
                  </button>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </>
  );
}

function ModelInputs({ provider, models, onChange }) {
  const m = (models || {})[provider] || {};
  return (
    <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 6 }}>
      <span className="meta" style={{ minWidth: 80 }}>
        {provider === "openai" ? "OpenAI" : "Claude"}:
      </span>
      {TASKS.map((t) => (
        <label key={t} className="meta" style={{ display: "flex", gap: 4, alignItems: "center" }}>
          {TASK_LABELS[t]}
          <input
            className="input"
            style={{ width: 150, fontSize: 12 }}
            value={m[t] || ""}
            placeholder="(default)"
            onChange={(e) => onChange(provider, t, e.target.value)}
          />
        </label>
      ))}
    </div>
  );
}

function SettingsTab({ users, onPatch }) {
  const [selected, setSelected] = useState("");
  const [key, setKey] = useState("");
  const [models, setModels] = useState(null);

  const u = users.find((x) => x.user_id === selected);
  useEffect(() => {
    setModels(u ? u.policy.models || {} : null);
    setKey("");
  }, [selected]); // eslint-disable-line react-hooks/exhaustive-deps

  function setModel(provider, task, value) {
    setModels((prev) => ({
      ...prev,
      [provider]: { ...((prev || {})[provider] || {}), [task]: value },
    }));
  }

  return (
    <div className="card">
      <div className="card-title">AI provider &amp; models, per user</div>
      <p className="meta">
        Which providers a user may use, how requests route, and the models per task. Claude CLI
        stays allow-listed — off unless you enable it for a specific user.
      </p>
      <select className="input" style={{ maxWidth: 340 }} value={selected}
              onChange={(e) => setSelected(e.target.value)}>
        <option value="">— pick a user —</option>
        {users.map((x) => (
          <option key={x.user_id} value={x.user_id}>{x.email || x.user_id}</option>
        ))}
      </select>

      {u && (
        <div style={{ marginTop: 14 }}>
          <div className="row" style={{ gap: 16, flexWrap: "wrap", alignItems: "center" }}>
            <label className="row" style={{ gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={!!u.policy.allow_openai}
                     onChange={(e) => onPatch(u.user_id, { policy: { allow_openai: e.target.checked } })} />
              OpenAI allowed
            </label>
            <label className="row" style={{ gap: 6, alignItems: "center" }}>
              <input type="checkbox" checked={!!u.policy.allow_claude_cli}
                     onChange={(e) => onPatch(u.user_id, { policy: { allow_claude_cli: e.target.checked } })} />
              Claude CLI allowed
            </label>
            <label className="row meta" style={{ gap: 6, alignItems: "center" }}>
              Primary
              <select className="input" style={{ width: 130 }} value={u.policy.primary_provider || "openai"}
                      onChange={(e) => onPatch(u.user_id, { policy: { primary_provider: e.target.value } })}>
                <option value="openai">openai</option>
                <option value="claude_cli">claude_cli</option>
              </select>
            </label>
            <label className="row meta" style={{ gap: 6, alignItems: "center" }}>
              Fallback
              <select className="input" style={{ width: 130 }} value={u.policy.fallback_provider || ""}
                      onChange={(e) => onPatch(u.user_id, { policy: { fallback_provider: e.target.value || null } })}>
                <option value="">(none)</option>
                <option value="openai">openai</option>
                <option value="claude_cli">claude_cli</option>
              </select>
            </label>
          </div>

          <div className="row" style={{ gap: 8, alignItems: "center", marginTop: 12 }}>
            <input className="input" type="password" style={{ maxWidth: 300 }}
                   placeholder={u.policy.has_openai_key ? "OpenAI key: saved (paste to replace)" : "OpenAI API key for this user"}
                   value={key} onChange={(e) => setKey(e.target.value)} />
            <button className="btn btn-secondary" disabled={!key.trim()}
                    onClick={() => { onPatch(u.user_id, { policy: { openai_api_key: key.trim() } }); setKey(""); }}>
              Save key
            </button>
          </div>

          <div style={{ marginTop: 10 }}>
            <div className="meta" style={{ marginBottom: 2 }}>
              Model per task (blank = default). Save after editing.
            </div>
            <ModelInputs provider="openai" models={models} onChange={setModel} />
            <ModelInputs provider="claude_cli" models={models} onChange={setModel} />
            <button className="btn btn-secondary" style={{ marginTop: 8 }}
                    onClick={() => onPatch(u.user_id, { policy: { models } })}>
              Save models
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default function Admin() {
  const [tab, setTab] = useState("Users");
  const [users, setUsers] = useState(null);
  const [error, setError] = useState("");
  const account = useAccount();
  const toast = useToast();

  function load() {
    api.adminUsers().then((d) => setUsers(d.users)).catch((e) => setError(e.message));
  }
  useEffect(load, []);

  async function patch(userId, body) {
    setError("");
    try {
      await api.adminPatchUser(userId, body);
      toast("Saved");
      load();
    } catch (e) {
      setError(e.message);
    }
  }

  if (account.logged_in && account.is_admin === false) {
    return (
      <div className="content">
        <h1>Admin</h1>
        <p className="meta">This area is for ApplyBro administrators.</p>
        <Link className="btn btn-secondary" to="/">Back to ApplyBro</Link>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <nav className="sidebar">
        <div className="sidebar-brand">ApplyBro admin</div>
        {TABS.map(({ name, Icon }) => (
          <button key={name}
                  className={"sidebar-link" + (tab === name ? " active" : "")}
                  onClick={() => setTab(name)}>
            <Icon className="sidebar-icon" />
            {name}
          </button>
        ))}
        <div className="sidebar-account">
          <div className="meta sidebar-email" title={account.email}>{account.email}</div>
          <Link className="btn btn-ghost btn-sm" to="/">Back to ApplyBro</Link>
        </div>
      </nav>

      <main className="main">
        <div className="content">
          <h1>{tab}</h1>
          <p className="meta">
            {tab === "Users"
              ? "Approve accounts and manage subscriptions. Every change is recorded in the audit log."
              : "Set each user's AI provider routing and the model used for each task."}
          </p>

          {error && (
            <div className="card" style={{ borderColor: "var(--danger)", background: "var(--danger-soft)" }}>
              {error}
            </div>
          )}
          {users === null && !error && <p className="meta">Loading…</p>}
          {users !== null && users.length === 0 && (
            <div className="empty-state"><h3>No users yet</h3></div>
          )}
          {users !== null && tab === "Users" && (
            <UsersTab users={users} myEmail={account.email} onPatch={patch} />
          )}
          {users !== null && tab === "Settings" && (
            <SettingsTab users={users} onPatch={patch} />
          )}
        </div>
      </main>
    </div>
  );
}
