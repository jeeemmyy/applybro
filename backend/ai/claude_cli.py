"""Claude Code CLI provider (`claude -p` — the user's own subscription login,
no API key). Moved here from tailoring/reword.py, preserving the abort
machinery the dashboard's Abort / Stop-batch buttons depend on.

Always runs with cwd = a temp dir OUTSIDE the project: the CLI auto-loads any
CLAUDE.md above its cwd, and this project's CLAUDE.md told a live run to "ask
me when unsure" — which came back as prose instead of the requested output
(seen in production). Every prompt ApplyBro sends is self-contained.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Optional, Tuple

from ._abort import ABORTED, register, unregister

# Where the Claude Code CLI tends to live when not on the server's PATH
# (macOS GUI-launched processes get a minimal PATH).
_CANDIDATES = [
    os.path.expanduser("~/.claude/local/claude"),
    os.path.expanduser("~/.local/bin/claude"),
    "/opt/homebrew/bin/claude",
    "/usr/local/bin/claude",
]

INSTALL_HINT = (
    "Claude Code CLI not found. Install it once with "
    "`npm install -g @anthropic-ai/claude-code` (then it uses your existing "
    "Claude login — no API key), or set APPLYBRO_CLAUDE_BIN to its path. "
    "Or switch to the OpenAI provider in Settings → General."
)

# Transient failures show up as a fast exit 1 with empty stderr when many
# calls fire back-to-back; a short retry clears them (seen 2026-07-12).
_RETRIES = 2
_BACKOFF = 4


def find_claude() -> Optional[str]:
    env = os.environ.get("APPLYBRO_CLAUDE_BIN")
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env
    hit = shutil.which("claude")
    if hit:
        return hit
    for c in _CANDIDATES:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _kill_tree(p: subprocess.Popen) -> None:
    """Kill the CLI and any children it spawned. The child is started in its
    own process group (start_new_session) precisely so this can killpg —
    killing only the parent leaves children holding the stdout pipe open and
    communicate() waiting on it forever."""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        p.kill()


class _ProcCanceller:
    def __init__(self, p: subprocess.Popen) -> None:
        self.p = p
        self.cancelled = False

    def cancel(self) -> bool:
        self.cancelled = True
        _kill_tree(self.p)
        return True


def run(prompt: str, *, model: str, timeout: int) -> Tuple[bool, str]:
    claude_bin = find_claude()
    if claude_bin is None:
        return False, INSTALL_HINT
    cmd = [claude_bin, "-p", "--output-format", "text", "--model", model]
    for attempt in range(_RETRIES + 1):
        try:
            p = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
                cwd=tempfile.gettempdir(), start_new_session=True)
        except OSError as e:
            return False, f"couldn't run Claude CLI: {e}"
        c = _ProcCanceller(p)
        register(c)
        try:
            out, errout = p.communicate(prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(p)
            p.communicate()
            return False, f"Claude took longer than {timeout}s"
        finally:
            unregister(c)
        if c.cancelled:
            return False, ABORTED
        if p.returncode == 0 and (out or "").strip():
            return True, out
        err = (errout or out or "").strip()
        if "not logged in" in err.lower():
            return False, ("Claude CLI isn't logged in — open Terminal, run "
                           f"`{claude_bin}`, type /login and finish the "
                           "sign-in, then try again.")
        if attempt < _RETRIES:
            time.sleep(_BACKOFF)
            continue
        return False, f"Claude CLI failed: {err[:300]}"
    return False, "Claude CLI failed"
