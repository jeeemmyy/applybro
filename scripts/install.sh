#!/usr/bin/env bash
# ApplyBro installer (Distribution v1: git clone + this script).
#
# What it does, in order:
#   1. Checks python3 >= 3.9 is on PATH.
#   2. pip installs requirements.txt.
#   3. Verifies Google Chrome is installed (Playwright drives it via
#      channel="chrome" — it does NOT download its own browser; this repo
#      NEVER runs `playwright install`, since cdn.playwright.dev is
#      unreliable/blocked on some machines).
#   4. Verifies `typst` is on PATH (build.sh needs it to compile the resume).
#   5. Prints exact next steps.
#
# Idempotent: safe to re-run any time (e.g. after `git pull`). Every failure
# prints a plain actionable message — never a Python traceback — and exits
# non-zero so a CI/script caller can detect it, but does not `set -e` blindly
# abort mid-check (each check reports itself, then the script tallies).
set -u

BOLD="$(tput bold 2>/dev/null || true)"
RESET="$(tput sgr0 2>/dev/null || true)"
RED="$(tput setaf 1 2>/dev/null || true)"
GREEN="$(tput setaf 2 2>/dev/null || true)"
YELLOW="$(tput setaf 3 2>/dev/null || true)"

ok()   { echo "${GREEN}✓${RESET} $1"; }
warn() { echo "${YELLOW}!${RESET} $1"; }
fail() { echo "${RED}✗${RESET} $1"; }

FAILED=0

echo "${BOLD}ApplyBro install check${RESET}"
echo

# --------------------------------------------------------------- 1. python3
PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    fail "python3 not found on PATH."
    echo "  Install Python 3.9+ from https://www.python.org/downloads/ (or your"
    echo "  OS package manager), make sure 'python3' is on PATH, then re-run this script."
    FAILED=1
else
    PY_VERSION=$("$PYTHON_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
    PY_OK=$("$PYTHON_BIN" -c 'import sys; print(1 if sys.version_info >= (3, 9) else 0)' 2>/dev/null)
    if [ "$PY_OK" = "1" ]; then
        ok "python3 $PY_VERSION found (>= 3.9 required)."
    else
        fail "python3 found but it's version $PY_VERSION — ApplyBro needs 3.9 or newer."
        echo "  Install a newer Python 3 and make sure it's the one 'python3' resolves to."
        FAILED=1
    fi
fi

# ------------------------------------------------------------- 2. pip install
if [ -n "$PYTHON_BIN" ] && [ "$PY_OK" = "1" ]; then
    echo
    echo "Installing Python dependencies from requirements.txt..."
    if "$PYTHON_BIN" -m pip install -r requirements.txt; then
        ok "Python dependencies installed."
    else
        fail "pip install -r requirements.txt failed — see the error above."
        echo "  Common fixes: create/activate a virtualenv first, or run with '--user'."
        FAILED=1
    fi
else
    warn "Skipping pip install — fix the python3 problem above first."
fi

# ---------------------------------------------------- 3. Google Chrome present
echo
CHROME_FOUND=0
case "$(uname -s)" in
    Darwin)
        if [ -d "/Applications/Google Chrome.app" ] || \
           [ -d "$HOME/Applications/Google Chrome.app" ]; then
            CHROME_FOUND=1
        fi
        ;;
    Linux)
        for bin in google-chrome google-chrome-stable chromium chromium-browser; do
            if command -v "$bin" >/dev/null 2>&1; then
                CHROME_FOUND=1
                break
            fi
        done
        ;;
    *)
        # Unrecognized platform (e.g. Windows/Git Bash) — don't fail hard,
        # just can't auto-detect; the runtime check on `start` will still
        # catch it with a clear Playwright error.
        CHROME_FOUND=2
        ;;
esac

if [ "$CHROME_FOUND" = "1" ]; then
    ok "Google Chrome found."
elif [ "$CHROME_FOUND" = "2" ]; then
    warn "Couldn't auto-detect Google Chrome on this OS — make sure it's installed."
else
    fail "Google Chrome not found."
    echo "  ApplyBro drives your existing Chrome install (Playwright channel=\"chrome\")"
    echo "  instead of downloading its own — install Chrome from"
    echo "  https://www.google.com/chrome/ and re-run this script."
    echo "  (This project deliberately never runs 'playwright install' — the"
    echo "  browser download CDN is unreliable on some networks.)"
    FAILED=1
fi

# ------------------------------------------------------------------ 4. typst
echo
if command -v typst >/dev/null 2>&1; then
    ok "typst found ($(typst --version 2>/dev/null | head -1))."
else
    fail "typst not found on PATH — build.sh (resume compilation) needs it."
    echo "  Install it: https://github.com/typst/typst#installation"
    echo "  (macOS: brew install typst)"
    FAILED=1
fi

# --------------------------------------------------------------------- done
echo
if [ "$FAILED" != "0" ]; then
    fail "${BOLD}Install check failed — fix the items marked above, then re-run scripts/install.sh.${RESET}"
    exit 1
fi

ok "${BOLD}All checks passed.${RESET}"
echo
echo "Next steps:"
echo "  1. Run:  ${BOLD}python3 -m backend.cli start${RESET}"
echo "  2. ApplyBro opens its own Chrome window with the dashboard."
echo "     If config.yaml/credentials.json/profile.yaml aren't set up yet,"
echo "     it lands on a first-run setup wizard that walks you through:"
echo "       - a Google Cloud OAuth credential (free tier)"
echo "       - connecting your Google account (Sheets + read-only Gmail)"
echo "       - picking your target-company spreadsheet"
echo "       - your application-answers profile"
echo "       - turning your resume PDF into content.yaml (a Claude Code step)"
echo "  See README.md for the full walkthrough."
