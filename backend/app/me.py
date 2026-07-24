"""'Me' page backend: profile.yaml (structured field editor), content.yaml
(raw-text editor with validation), resume rebuild, and target-role keywords.

profile.yaml is edited field-by-field, so it uses ruamel.yaml's round-trip
loader: a naive yaml.safe_load + yaml.safe_dump strips every inline comment
(profile.yaml has several that matter — e.g. why phone_national exists),
and ruamel preserves them across a load-modify-save cycle. content.yaml is
edited as raw text (same pattern the legacy dashboard used, and what the
CLAUDE.md-documented re-skin workflow expects) — the user's own text is
written back verbatim after validating it parses, so no round-trip tool is
needed there at all.
"""
from __future__ import annotations

import subprocess
from typing import Any, Dict

import yaml
from ruamel.yaml import YAML

from ..paths import CONTENT_YAML, PROFILE_EXAMPLE_YAML, PROFILE_YAML

_ryaml = YAML()
_ryaml.preserve_quotes = True
_ryaml.width = 4096  # don't re-wrap long comment lines on save


# With the Supabase store on, the resumes table is the SOURCE OF TRUTH for
# content.yaml and profile.yaml; the local files become synced caches (still
# needed on disk: typst builds from CONTENT_YAML, the autofiller reads
# PROFILE_YAML). Reads sync down first; writes update the file then push up.
def _cloud():
    from .. import store
    return store if (store.enabled() and store.logged_in()) else None


def _read_file(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def sync_down() -> None:
    """Pull the signed-in user's resume + profile into the local cache files
    (called on login and before reads). Cloud empty = nothing to pull —
    a fresh user starts from the local examples and pushes up on first save."""
    st = _cloud()
    if st is None:
        return
    row = st.resumes_get()
    for text, path in ((row.get("content_yaml", ""), CONTENT_YAML),
                       (row.get("profile_yaml", ""), PROFILE_YAML)):
        if text and text != _read_file(path):
            with open(path, "w") as f:
                f.write(text)


def _template_defaults() -> Dict[str, Any]:
    """Every setting the shipped template knows about. A profile.yaml created
    before a setting existed simply lacks that key, and both the editor and
    put_profile key off the file's own keys — so a NEW setting (writing_style,
    say) would be invisible and unsavable until the user hand-edited YAML.
    Merging the template in fixes that for every future setting too."""
    try:
        with open(PROFILE_EXAMPLE_YAML) as f:
            return dict(yaml.safe_load(f) or {})
    except (OSError, ValueError):
        return {}


def get_profile() -> Dict[str, Any]:
    try:
        sync_down()
    except RuntimeError:
        pass   # offline: serve the local cache
    with open(PROFILE_YAML) as f:
        data = dict(_ryaml.load(f))
    for k, v in _template_defaults().items():
        if k not in data:
            data[k] = v if isinstance(v, str) else ""
    return data


def put_profile(updates: Dict[str, str]) -> Dict[str, Any]:
    with open(PROFILE_YAML) as f:
        data = _ryaml.load(f)
    # KNOWN keys only — the file's own, plus any the shipped template defines
    # (so a newly-added setting can be saved the first time). Never an
    # arbitrary key from the client.
    allowed = set(data) | set(_template_defaults())
    for k, v in updates.items():
        if k in allowed:
            data[k] = v
    with open(PROFILE_YAML, "w") as f:
        _ryaml.dump(data, f)
    st = _cloud()
    if st is not None:
        st.resumes_set(profile_yaml=_read_file(PROFILE_YAML))
    return dict(data)


def get_content_raw() -> str:
    try:
        sync_down()
    except RuntimeError:
        pass   # offline: serve the local cache
    with open(CONTENT_YAML) as f:
        return f.read()


def put_content_raw(raw: str) -> None:
    """Validates before writing — a malformed save must never corrupt the
    resume master. Writes the user's text back verbatim (their own
    formatting/comments survive exactly), no re-dump."""
    yaml.safe_load(raw)  # raises yaml.YAMLError if invalid
    with open(CONTENT_YAML, "w") as f:
        f.write(raw)
    st = _cloud()
    if st is not None:
        st.resumes_set(content_yaml=raw)


def rebuild_resume() -> Dict[str, Any]:
    from ..paths import BUILD_SH
    r = subprocess.run(["bash", BUILD_SH], capture_output=True, text=True)
    return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr)[-2000:]}
