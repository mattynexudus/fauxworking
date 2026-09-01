"""
Auth banner + business picker for the control panel — the only place in the
webui package that talks to Nexudus.

`is_authenticated()` / `login()` wrap `nexudus_auth`; `businesses()` wraps
`pipeline.list_businesses()` and classifies the result as none / single /
multi (the panel blocks a live command on a multi-business login until one is
picked — see jobs.JobManager.start). The business list is cached until
`invalidate()` (called after a fresh login) or an explicit refresh.
"""

from __future__ import annotations

import threading

import nexudus_auth
import pipeline

_lock = threading.Lock()
_biz_cache = None


def is_authenticated() -> bool:
    return nexudus_auth.is_authenticated()


def login(email, password) -> dict:
    """Exchange credentials for tokens (written to .env by nexudus_auth).
    Propagates SystemExit on a bad login or a non-admin account — the handler
    turns that into a 400 with the message. The password is dropped here."""
    try:
        nexudus_auth.authenticate(email, password)
    finally:
        del password
    invalidate()
    return {"authenticated": True, "email": email}


def logout() -> None:
    """Sign out — clears the tokens (nexudus_auth.logout signs the CLI out
    too, same .env) and drops the cached business list."""
    nexudus_auth.logout()
    invalidate()


def invalidate() -> None:
    global _biz_cache
    with _lock:
        _biz_cache = None


def businesses(refresh=False) -> dict:
    """{"authenticated", "mode": none|single|multi, "businesses": [{id,name}], "error"?}."""
    global _biz_cache
    with _lock:
        if _biz_cache is not None and not refresh:
            return _biz_cache

    if not nexudus_auth.is_authenticated():
        result = {"authenticated": False, "mode": "none", "businesses": []}
        with _lock:
            _biz_cache = result
        return result

    try:
        raw = pipeline.list_businesses()
    except SystemExit as e:
        return {"authenticated": True, "mode": "none", "businesses": [], "error": str(e)}
    except Exception as e:  # noqa: BLE001 — a transient API error shouldn't be cached
        return {"authenticated": True, "mode": "none", "businesses": [], "error": str(e)}

    items = [{"id": b["Id"], "name": b.get("Name", "?")} for b in raw]
    mode = "none" if not items else "single" if len(items) == 1 else "multi"
    result = {"authenticated": True, "mode": mode, "businesses": items}
    with _lock:
        _biz_cache = result
    return result
