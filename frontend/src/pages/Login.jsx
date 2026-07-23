import { useState } from "react";
import { api } from "../api.js";

// Email + password sign-in, no emails involved (confirmation is disabled in
// the Supabase project — decided 2026-07-14 after rate-limit pain; the
// needs_confirmation branch below survives only as a fallback in case the
// setting is ever re-enabled). The password goes straight to Supabase;
// ApplyBro never stores it — only the session.
export default function Login({ onDone }) {
  const [mode, setMode] = useState("login"); // login | signup
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const ready = email.trim() && password;

  async function submit() {
    if (!ready) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (mode === "signup") {
        const res = await api.authSignup(email.trim(), password);
        if (res.needs_confirmation) {
          setNotice(
            `Almost there — we sent a confirmation link to ${email.trim()}. ` +
            "Click it, then sign in here."
          );
          setMode("login");
        } else {
          onDone();
        }
      } else {
        await api.authLogin(email.trim(), password);
        onDone();
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <div className="card login-card">
        <div className="login-brand">ApplyBro</div>
        <h2 style={{ marginBottom: 4 }}>
          {mode === "signup" ? "Create your account" : "Sign in"}
        </h2>
        <p className="meta">
          {mode === "signup"
            ? "Pick a password (8+ characters) — you're signed in right away."
            : "Your companies, queue, and settings belong to your account."}
        </p>
        {error && <p className="meta" style={{ color: "var(--danger)" }}>{error}</p>}
        {notice && !error && <p className="meta" style={{ color: "var(--navy)" }}>{notice}</p>}

        <div className="field">
          <label>Email</label>
          <input
            className="input"
            type="email"
            placeholder="you@example.com"
            value={email}
            autoFocus
            disabled={busy}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="field">
          <label>Password</label>
          <input
            className="input"
            type="password"
            placeholder={mode === "signup" ? "At least 8 characters" : "Your password"}
            value={password}
            disabled={busy}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
        </div>
        <button
          className="btn btn-primary"
          style={{ width: "100%", justifyContent: "center" }}
          onClick={submit}
          disabled={busy || !ready}
        >
          {busy ? (
            <><span className="spinner" /> {mode === "signup" ? "Creating…" : "Signing in…"}</>
          ) : mode === "signup" ? "Create account" : "Sign in"}
        </button>
        <button
          className="btn btn-ghost btn-sm"
          style={{ width: "100%", justifyContent: "center", marginTop: 8 }}
          disabled={busy}
          onClick={() => { setMode(mode === "signup" ? "login" : "signup"); setError(""); setNotice(""); }}
        >
          {mode === "signup" ? "Have an account? Sign in" : "New here? Create an account"}
        </button>
      </div>
    </div>
  );
}
