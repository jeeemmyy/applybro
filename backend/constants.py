"""Small cross-layer constants that both the feature packages and backend.app
need, without either layer importing from the other.

CDP_PORT: the dedicated dashboard Chrome window (launch.py, async Playwright)
exposes a remote-debugging port. backend.autofill.forms (sync Playwright,
runs in a background thread) connects to that SAME port via
chromium.connect_over_cdp() so application tabs open beside the dashboard
tab in one window, instead of spawning a second throwaway browser. This has
been verified empirically: a CDP-attached sync Playwright client can open a
new tab in the async context's browser, and that tab survives even after the
sync client's `with sync_playwright()` block exits — exactly the "fill and
leave the tab open for review" behavior the apply flow needs.
"""
CDP_PORT = 9666
