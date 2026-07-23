"""`python -m backend.cli start` — runs the local ApplyBro backend.

The extension IS the product now (CLAUDE.md), so by DEFAULT this boots only
the FastAPI backend and prints its URL — it does not open a browser and does
not navigate anything. The extension talks to this backend from the user's
own Chrome; auto-opening a second Chrome window with a localhost tab was
unwanted (user report 2026-07-23).

`--with-browser` restores the legacy behaviour: a dedicated Chrome window
(Playwright persistent context) with the dashboard in tab 1, for the
Playwright-based autofill path that drives that window over CDP. The profile
lives at ~/.applybro/chrome-profile and persists (ATS/Workday logins survive
between runs). Closing that window shuts ApplyBro down.
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys

import uvicorn

from ..constants import CDP_PORT

HOST = "127.0.0.1"
PORT = 8765
# Product-branded user-data home, INTENTIONALLY independent of the `backend`
# Python package name — this holds the persistent Chrome profile (saved
# ATS/Workday logins). Renaming it would silently orphan those logins, so it
# stays "~/.applybro/" regardless of what the code package is called.
PROFILE_DIR = os.path.expanduser("~/.applybro/chrome-profile")


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((host, port)) != 0


async def _serve() -> tuple:
    """Boot the FastAPI backend. Returns (server, server_task)."""
    from .server import app

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    return server, server_task


async def _run_server_only() -> None:
    """Default: just the backend. The extension connects to it from the user's
    own browser — nothing is auto-opened."""
    server, server_task = await _serve()
    print(f"ApplyBro backend running at http://{HOST}:{PORT}")
    print("The extension connects here automatically. To open the dashboard "
          f"(Tracking + Settings) yourself, visit http://{HOST}:{PORT} .")
    print("Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()          # until Ctrl+C
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        server.should_exit = True
        await server_task


async def _run(headless: bool = False) -> None:
    from playwright.async_api import async_playwright

    from .server import app

    server, server_task = await _serve()
    print(f"ApplyBro backend running at http://{HOST}:{PORT}")

    os.makedirs(PROFILE_DIR, exist_ok=True)
    pw = await async_playwright().start()
    ctx = await pw.chromium.launch_persistent_context(
        PROFILE_DIR, channel="chrome", headless=headless,
        viewport=None,
        args=["--window-size=1440,900", "--window-position=40,40",
              f"--remote-debugging-port={CDP_PORT}"],
    )
    app.state.browser_context = ctx

    closed = asyncio.Event()
    ctx.on("close", lambda: closed.set())

    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto(f"http://{HOST}:{PORT}")
    print("Dashboard window open. Close it (or Ctrl+C here) to stop ApplyBro.")

    try:
        await closed.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        try:
            if not closed.is_set():
                await ctx.close()
        except Exception:
            pass
        await pw.stop()
        server.should_exit = True
        await server_task


def run(headless: bool = False, with_browser: bool = False) -> None:
    if not _port_free(HOST, PORT):
        print(f"Port {PORT} is already in use — ApplyBro may already be running. "
              f"Open http://{HOST}:{PORT} in a browser, or stop the other "
              f"process and try again.")
        sys.exit(1)
    try:
        asyncio.run(_run(headless=headless) if with_browser
                    else _run_server_only())
    except KeyboardInterrupt:
        pass
