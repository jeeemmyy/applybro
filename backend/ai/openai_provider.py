"""OpenAI provider — the DEFAULT (user's own API key, their own billing).

Plain HTTP via `requests` to the Chat Completions API (no new SDK dep). The
request is STREAMED so the dashboard's Abort button can stop it near-
immediately: the reader loop checks a cancel flag between chunks. A blocking
non-streamed POST couldn't be interrupted — that's the honest reason to
stream here.

The API key is read from config/env by the caller and passed in; it is never
logged and never placed in an error message.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Tuple

import requests

from ._abort import ABORTED, register, unregister

API_URL = "https://api.openai.com/v1/chat/completions"


def _is_reasoning(model: str) -> bool:
    """o-series (o1/o3/o4…) and the gpt-5 family accept ONLY the default
    temperature — sending an explicit one returns a 400. JSON mode is fine on
    them, so this gates the temperature parameter only. The 400-retry in
    run() is the backstop for any model this doesn't recognize."""
    m = (model or "").lower()
    return bool(re.match(r"o\d", m) or m.startswith("gpt-5"))


class _StreamCanceller:
    def __init__(self) -> None:
        self._ev = threading.Event()

    def cancel(self) -> bool:
        self._ev.set()
        return True

    @property
    def cancelled(self) -> bool:
        return self._ev.is_set()


def _friendly_error(status: int, body: str) -> str:
    """A helpful message per status, WITHOUT echoing the request (which holds
    the key in its headers — requests never puts it in the body, but be
    careful regardless)."""
    if status == 401:
        return ("OpenAI rejected the API key (401). Check it in "
                "Settings → General.")
    if status == 429:
        return ("OpenAI rate limit or quota reached (429) — check your "
                "OpenAI account's billing/usage.")
    if status == 404:
        return ("OpenAI says that model doesn't exist (404) — pick a "
                "different model in Settings → General.")
    try:
        msg = json.loads(body).get("error", {}).get("message", "")
    except (ValueError, AttributeError):
        msg = ""
    return f"OpenAI API error {status}" + (f": {msg}" if msg else "")


def _strip_unsupported(payload: dict, body: str) -> bool:
    """A 400 that complains about a param the chosen model doesn't accept:
    drop that param so the call can succeed rather than fail outright. Returns
    True if something was removed (caller retries once)."""
    low = (body or "").lower()
    removed = False
    if "temperature" in low and "temperature" in payload:
        del payload["temperature"]
        removed = True
    if ("response_format" in low or "json" in low) and "response_format" in payload:
        del payload["response_format"]
        removed = True
    return removed


def run(prompt: str, *, model: str, api_key: str, timeout: int,
        temperature: float = None, json_mode: bool = False) -> Tuple[bool, str]:
    c = _StreamCanceller()
    register(c)
    resp = None
    # Privacy (SaaS Phase 6 / MVP-701): stateless — `store: false` keeps the
    # request/response out of OpenAI-side storage (dashboards, retrievable
    # logs). We never use Assistants / Threads / Files, so there's no
    # persistent conversation object to leave behind. This does NOT bypass any
    # abuse-monitoring retention OpenAI applies without a ZDR agreement — see
    # docs/PRIVACY.md; the user-facing wording must match the account.
    payload = {"model": model,
               "messages": [{"role": "user", "content": prompt}],
               "store": False,
               "stream": True}
    # Low temperature = reproducible output (see backend/ai _FEATURE_SAMPLING);
    # omitted for reasoning models that reject it. JSON mode guarantees a
    # parseable object back for the features that need one.
    if temperature is not None and not _is_reasoning(model):
        payload["temperature"] = temperature
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    def _post():
        return requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json=payload, stream=True, timeout=timeout)

    try:
        resp = _post()
        if resp.status_code == 400 and _strip_unsupported(payload, resp.text[:600]):
            resp.close()
            resp = _post()   # retry once without the rejected param(s)
        if resp.status_code != 200:
            return False, _friendly_error(resp.status_code, resp.text[:400])
        chunks = []
        for line in resp.iter_lines(decode_unicode=True):
            if c.cancelled:
                return False, ABORTED
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
                delta = obj["choices"][0]["delta"].get("content")
            except (ValueError, KeyError, IndexError):
                continue
            if delta:
                chunks.append(delta)
        text = "".join(chunks)
        if not text.strip():
            return False, "OpenAI returned an empty response."
        return True, text
    except requests.Timeout:
        return False, f"OpenAI request timed out after {timeout}s"
    except requests.RequestException as e:
        if c.cancelled:
            return False, ABORTED
        return False, f"OpenAI request failed: {type(e).__name__}"
    finally:
        unregister(c)
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass


def list_models(api_key: str, timeout: int = 20) -> Tuple[bool, object]:
    """Fetch the account's available model ids (for the Settings dropdown).
    Returns (True, [ids]) or (False, error). We don't guess ids — the user
    picks from what their key can actually see."""
    try:
        r = requests.get("https://api.openai.com/v1/models",
                         headers={"Authorization": f"Bearer {api_key}"},
                         timeout=timeout)
    except requests.RequestException:
        return False, ("Couldn't reach OpenAI — check your internet "
                       "connection. Showing a standard model list meanwhile.")
    if r.status_code != 200:
        return False, _friendly_error(r.status_code, r.text[:300])
    try:
        data = r.json().get("data", [])
    except ValueError:
        return False, "OpenAI returned an unreadable model list."
    # Chat-capable ids only: gpt-* and o* families. Non-chat endpoints
    # (audio/tts/embeddings/image/moderation/realtime) would 404 on
    # /chat/completions, so they never belong in these dropdowns.
    _EXCLUDE = ("audio", "realtime", "tts", "transcribe", "whisper",
                "embedding", "moderation", "image", "dall-e", "search",
                "instruct", "computer-use")
    ids = {m["id"] for m in data if isinstance(m, dict) and "id" in m
           and (m["id"].startswith("gpt-") or m["id"].startswith("o"))
           and not any(x in m["id"] for x in _EXCLUDE)}

    import re

    def _rank(mid: str):
        # Plain family ids ("gpt-4o") sort before dated snapshots
        # ("gpt-4o-2024-08-06"); alphabetical within each group.
        return (bool(re.search(r"\d{4}-\d{2}-\d{2}", mid)), mid)

    return True, sorted(ids, key=_rank)
