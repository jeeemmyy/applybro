"""`python -m backend.cli start` — the one command that runs the dashboard.

Boots the FastAPI backend (backend.app.server:app) and Playwright's Chrome
persistent context in the SAME asyncio event loop, so later phases can hand
the same browser context to the apply engine (application tabs open beside
the dashboard tab, not in a separate throwaway browser).

The Chrome profile lives at ~/.applybro/chrome-profile and persists forever
(ATS/Workday logins survive between runs). Tab 1 is always the dashboard;
closing that window shuts the whole thing down.
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


async def _run(headless: bool = False) -> None:
    if not _port_free(HOST, PORT):
        print(f"Port {PORT} is already in use — ApplyBro may already be running. "
              f"Open http://{HOST}:{PORT} in a browser, or stop the other "
              f"process and try again.")
        sys.exit(1)

    from playwright.async_api import async_playwright

    from .server import app

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
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


def run(headless: bool = False) -> None:
    try:
        asyncio.run(_run(headless=headless))
    except KeyboardInterrupt:
        pass
