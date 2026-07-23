import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { api } from "../api.js";
import { IconTracking, IconSettings } from "./icons.jsx";

// Finding/scoring/applying all live in the extension now — the dashboard
// keeps only what it's better at (pivot 2026-07-22).
const ITEMS = [
  { to: "/", label: "Tracking", Icon: IconTracking, end: true },
  { to: "/settings", label: "Settings", Icon: IconSettings },
];

export default function Sidebar() {
  const [account, setAccount] = useState(null); // {supabase, logged_in, email}

  useEffect(() => {
    api.authStatus().then(setAccount).catch(() => {});
  }, []);

  async function signOut() {
    try {
      await api.authLogout();
    } finally {
      window.location.reload(); // AuthGate re-checks and shows the login page
    }
  }

  return (
    <nav className="sidebar">
      <div className="sidebar-brand">ApplyBro</div>
      {ITEMS.map(({ to, label, Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) => "sidebar-link" + (isActive ? " active" : "")}
        >
          <Icon className="sidebar-icon" />
          {label}
        </NavLink>
      ))}
      {/* No Admin link here (user request 2026-07-22) — the admin surface is
          reachable at #/admin, not advertised inside the user dashboard. */}
      {account?.logged_in && (
        <div className="sidebar-account">
          <div className="meta sidebar-email" title={account.email}>{account.email}</div>
          <button className="btn btn-ghost btn-sm" onClick={signOut}>
            Sign out
          </button>
        </div>
      )}
    </nav>
  );
}
