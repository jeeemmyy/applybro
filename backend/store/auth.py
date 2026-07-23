"""Supabase Auth — email + password sign-in.

(Passwordless email codes were tried first but burn one email PER SIGN-IN
against Supabase's built-in mailer rate limit; passwords need exactly one
confirmation email per account, ever.) signup() creates the account —
Supabase then emails a confirmation link, and login() works once the email
is confirmed, satisfying the verify-before-use requirement. The password
goes straight to Supabase over HTTPS and is never stored or logged; only
the session tokens persist, in gitignored data/session.json, refreshed
automatically.

Every PostgREST call in supabase_store sends the signed-in user's JWT, which
is what row-level security keys on (auth.uid()) — no session, no data.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

import requests

from ..paths import SESSION_JSON

_TIMEOUT = 20


def _conf() -> dict:
    from .supabase_store import _conf as c
    return c()


def _auth_req(path: str, body: dict) -> dict:
    c = _conf()
    if not (c["url"] and c["key"]):
        raise RuntimeError("Supabase isn't configured (supabase: block in config.yaml).")
    # Same pooled/retrying transport as PostgREST — a token refresh that dies
    # on one flaky DNS lookup would sign the user out mid-scan.
    from .supabase_store import _session
    try:
        r = _session().post(f"{c['url']}/auth/v1/{path}", json=body,
                            headers={"apikey": c["key"]}, timeout=_TIMEOUT)
    except requests.RequestException as e:
        raise RuntimeError(f"Couldn't reach Supabase: {type(e).__name__}")
    if r.status_code >= 400:
        try:
            j = r.json()
            msg = (j.get("error_description") or j.get("msg")
                   or j.get("message") or "")
        except ValueError:
            msg = ""
        raise RuntimeError(msg or f"Sign-in error ({r.status_code}).")
    try:
        return r.json() if r.content else {}
    except ValueError:
        return {}


def signup(email: str, password: str) -> dict:
    """Create an account. Returns {"needs_confirmation": True} when Supabase
    sent a confirmation email (the default — sign-in works after the link is
    clicked), or the saved session if confirmations are disabled."""
    if len(password or "") < 8:
        raise RuntimeError("Use a password of at least 8 characters.")
    j = _auth_req("signup", {"email": email.strip().lower(), "password": password})
    if "access_token" in j:
        _save_session(j)
        return session() or {}
    # An already-registered address comes back as a user object with no
    # session and (for privacy) obfuscated fields — surface it honestly.
    if (j.get("identities") == [] or
            (j.get("user") or {}).get("identities") == []):
        raise RuntimeError("That email already has an account — sign in instead.")
    return {"needs_confirmation": True}


def login(email: str, password: str) -> dict:
    """Password sign-in. Returns the saved session."""
    try:
        j = _auth_req("token?grant_type=password",
                      {"email": email.strip().lower(), "password": password})
    except RuntimeError as e:
        if "not confirmed" in str(e).lower():
            raise RuntimeError("Email not confirmed yet — click the link in "
                               "the confirmation email first, then sign in.")
        raise
    if "access_token" not in j:
        raise RuntimeError("Sign-in failed — check the email and password.")
    _save_session(j)
    return session() or {}


def _save_session(j: dict) -> None:
    user = j.get("user") or {}
    s = {
        "access_token": j["access_token"],
        "refresh_token": j.get("refresh_token", ""),
        # refresh a minute early so requests never race the expiry
        "expires_at": int(time.time()) + int(j.get("expires_in", 3600)) - 60,
        "email": user.get("email", ""),
        "user_id": user.get("id", ""),
    }
    d = os.path.dirname(SESSION_JSON)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(SESSION_JSON, "w") as f:
        json.dump(s, f, indent=2)


def session() -> Optional[dict]:
    try:
        with open(SESSION_JSON) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def logout() -> None:
    try:
        os.remove(SESSION_JSON)
    except OSError:
        pass


def access_token() -> Optional[str]:
    """The current user JWT, refreshed transparently when expired. None means
    'not signed in' (including an expired session whose refresh failed)."""
    s = session()
    if not s or not s.get("access_token"):
        return None
    if time.time() >= s.get("expires_at", 0):
        if not s.get("refresh_token"):
            return None
        try:
            j = _auth_req("token?grant_type=refresh_token",
                          {"refresh_token": s["refresh_token"]})
        except RuntimeError:
            return None
        if "access_token" not in j:
            return None
        j.setdefault("user", {"email": s.get("email", ""), "id": s.get("user_id", "")})
        _save_session(j)
        s = session() or {}
    return s.get("access_token")


def logged_in() -> bool:
    return access_token() is not None


def user_id() -> str:
    return (session() or {}).get("user_id", "")


def user_email() -> str:
    return (session() or {}).get("email", "")
