"""SSRF-safe outbound fetches (SaaS Phase 6 / MVP-704).

The extension and dashboard hand the backend arbitrary URLs (a job page, a
careers page). Left unguarded, a crafted URL could make the SERVER fetch
internal addresses — the cloud metadata endpoint (169.254.169.254), other
services on localhost, private-network hosts. This module validates a URL
resolves to a PUBLIC address before any request, and re-validates every
redirect hop (a public URL can 302 to an internal one).

Use `safe_get(url)` for user-supplied URLs; `validate_public_url(url)` before
pointing a headless browser at one. Known-ATS API calls (fixed public hosts)
don't need this — the risk is only ever a URL a user/extension supplied.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Optional
from urllib.parse import urlparse, urljoin

import requests


class UnsafeURL(ValueError):
    pass


_MAX_REDIRECTS = 5
_DEFAULT_TIMEOUT = 20


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # Reject loopback, private, link-local (incl. 169.254.169.254 metadata),
    # reserved, multicast, unspecified — everything that isn't a normal
    # public host.
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


def _host_is_public(host: str) -> bool:
    if not host:
        return False
    # A literal IP: check it directly (getaddrinfo would pass it through).
    try:
        ipaddress.ip_address(host)
        return _ip_is_public(host)
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    if not infos:
        return False
    # EVERY resolved address must be public — a host that resolves to one
    # public and one private IP is still an SSRF vector.
    return all(_ip_is_public(info[4][0]) for info in infos)


def validate_public_url(url: str) -> str:
    """Return the URL if it's an http(s) URL whose host resolves only to
    public addresses; raise UnsafeURL otherwise."""
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURL("only http(s) URLs are allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeURL("URL has no host")
    if not _host_is_public(host):
        raise UnsafeURL("URL resolves to a non-public address")
    return url


def safe_get(url: str, *, headers: Optional[dict] = None,
             timeout: int = _DEFAULT_TIMEOUT, **kw) -> Optional[requests.Response]:
    """GET a user-supplied URL, validating it and every redirect hop against
    the SSRF policy. Returns the Response, or None if the URL is unsafe or
    unreachable (callers already treat a missing fetch as 'no data')."""
    kw.pop("allow_redirects", None)   # we follow manually to re-validate hops
    current = url
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            validate_public_url(current)
            r = requests.get(current, headers=headers, timeout=timeout,
                             allow_redirects=False, **kw)
            if r.is_redirect and r.headers.get("location"):
                current = urljoin(current, r.headers["location"])
                continue
            return r
        return None   # too many redirects
    except (UnsafeURL, requests.RequestException):
        return None
