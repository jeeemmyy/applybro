import { createContext, useContext, useEffect, useState } from "react";
import { HashRouter, Routes, Route, useLocation, useNavigate } from "react-router-dom";
import Sidebar from "./components/Sidebar.jsx";
import Tracking from "./pages/Tracking.jsx";
import Settings from "./pages/Settings.jsx";
import Setup from "./pages/Setup.jsx";
import Login from "./pages/Login.jsx";
import Admin from "./pages/Admin.jsx";
import { api } from "./api.js";

// The signed-in account (email, account_status, subscribed, is_admin) —
// what the sidebar (Admin link), approval banner and Settings read.
const AccountContext = createContext({});
export function useAccount() {
  return useContext(AccountContext);
}

// When the Supabase store is configured, every route sits behind sign-in:
// your applications, tracking and AI policy are yours alone (rows are
// per-user in the database). Pure-local installs skip this gate.
function AuthGate({ children }) {
  const [status, setStatus] = useState(null); // null = checking

  function check() {
    api
      .authStatus()
      .then(setStatus)
      .catch(() => setStatus({ supabase: false, logged_in: false }));
  }
  useEffect(check, []);

  if (status === null) return null;
  if (status.supabase && !status.logged_in) return <Login onDone={check} />;
  return <AccountContext.Provider value={status}>{children}</AccountContext.Provider>;
}

// Account-state banner (SaaS Phase 1): a pending/suspended account can browse
// the dashboard, but every AI feature is refused server-side — say so plainly
// instead of letting users discover it through error toasts.
function AccountBanner() {
  const acc = useAccount();
  if (!acc.supabase || !acc.logged_in) return null;
  let msg = "";
  if (acc.account_status === "pending")
    msg = "Your account is awaiting approval — AI features unlock once it's activated.";
  else if (acc.account_status === "suspended" || acc.account_status === "rejected")
    msg = "Your account isn't active. Contact ApplyBro.";
  else if (acc.account_status === "active" && acc.subscribed === false)
    msg = "Your account isn't subscribed yet — AI features are enabled once your subscription is active.";
  if (!msg) return null;
  return (
    <div className="card" style={{
      margin: "12px 16px 0", borderColor: "var(--danger)",
      background: "var(--danger-soft)",
    }}>
      {msg}
    </div>
  );
}

// Gates the normal dashboard behind the first-run setup wizard: on a fresh
// clone (no config.yaml/credentials.json/token.json/profile.yaml yet) every
// route redirects to /setup instead of hitting a wall of 409s. Once ready,
// /setup stays reachable (e.g. to redo a step) but is no longer forced.
function SetupGate({ children }) {
  const [ready, setReady] = useState(null); // null = still checking
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    api
      .getSetupStatus()
      .then((s) => {
        if (!cancelled) setReady(s.ready);
      })
      .catch(() => {
        // Setup-status itself failing shouldn't hard-block the app; treat
        // as "ready" and let individual routes surface their own errors.
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, [location.pathname]);

  useEffect(() => {
    if (ready === false && location.pathname !== "/setup") {
      navigate("/setup", { replace: true });
    }
  }, [ready, location.pathname, navigate]);

  if (ready !== true && location.pathname !== "/setup") {
    // Render nothing until the redirect decision is made — both while the
    // status check is in flight (ready === null) AND once it has answered
    // "not ready" (ready === false, redirect effect about to fire).
    // Otherwise the normal dashboard mounts for one frame and its pages
    // fire API calls that land as 409 noise in the console.
    return null;
  }
  return children;
}

// The dashboard is no longer where the work happens — the EXTENSION is
// (pivot 2026-07-22). Finding, scoring and applying to jobs all live in the
// in-page panel; what's left here is what the panel can't be: a record of
// what you applied to and what came back, plus setup/settings. Companies,
// Jobs and the Apply room are gone.
function Shell() {
  const location = useLocation();
  const onSetup = location.pathname === "/setup";
  return (
    <SetupGate>
      <div className="app-shell">
        {!onSetup && <Sidebar />}
        <main className="main">
          <AccountBanner />
          <Routes>
            <Route path="/" element={<Tracking />} />
            <Route path="/tracking" element={<Tracking />} />
            <Route path="/settings" element={<Settings />} />
            <Route path="/setup" element={<Setup />} />
          </Routes>
        </main>
      </div>
    </SetupGate>
  );
}

export default function App() {
  return (
    <HashRouter>
      <AuthGate>
        <Routes>
          {/* Admin is its own surface with its own nav (user request
              2026-07-22) — never a tab inside the user dashboard. */}
          <Route path="/admin/*" element={<Admin />} />
          <Route path="*" element={<Shell />} />
        </Routes>
      </AuthGate>
    </HashRouter>
  );
}
