"""
Direct Nexudus REST API client — no LLM/agent in the loop.

This is the actual execution path for live runs. Every generator's `run()`
method already takes `nexudus_list`/`nexudus_create`/`nexudus_update`/
`nexudus_delete`/`nexudus_run_command` as plain callables (that's how they
were built against the MCP tools during development) — this module provides
real implementations of those same signatures, backed by `requests` calls
straight to https://spaces.nexudus.com/api/..., authenticated via the token
`nexudus_auth.get_access_token()` manages. A live run is just:

    python generators/00_reference.py     # no --dry-run flag

with no agent, no per-record tool-call overhead, and no token cost beyond
running the script itself.

## API shape (reverse-engineered from https://learn.nexudus.com/rest-api/)

Every entity lives under a fixed module prefix — `https://spaces.nexudus.com
/api/<module>/<entity>` — captured in ENTITY_MODULES below (sourced from the
Nexudus platform's own entity catalog, not guessed).

- GET    {url}            list (filters + page/size as query params)
- GET    {url}/{id}        get one
- POST   {url}             create; body = fields (PascalCase, matches the
                            schemas already used throughout this codebase)
- PUT    {url}              update; body = fields INCLUDING "Id" (the ID is
                            not in the URL for updates — same URL as create)
- DELETE {url}/{id}        delete
- POST   {url}/runcommand  run a command; body = {"Key", "Ids", "Parameters"}

Create/update responses are wrapped: {"Status", "Message", "Value": {...the
record...}, "WasSuccessful", "Errors", ...} — this module unwraps "Value"
and raises NexudusApiError on failure. List responses are the same shape
MCP's nexudus_list already returned: {"Records": [...], "HasNextPage", ...}.
"""

import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from config import WRITE_PACING_SECONDS
from nexudus_auth import get_access_token, base_url

# apiPath -> module. Sourced from the Nexudus entity catalog (not guessed) —
# see reference/api-modules.md for the full list and how it was obtained.
ENTITY_MODULES = {
    "taxrates": "sys", "financialaccounts": "billing", "tariffs": "billing",
    "resourcetypes": "spaces", "teams": "spaces", "products": "billing",
    "extraservices": "billing", "timepasses": "billing", "resources": "spaces",
    "floorplans": "sys", "floorplandesks": "sys", "inventoryassets": "spaces",
    "discountcodes": "billing", "crmboards": "crm", "crmboardcolumns": "crm",
    "businesstimeslots": "sys", "helpdeskdepartments": "support",
    "communitygroups": "community", "calendareventcategories": "content",
    "coworkers": "spaces", "visitors": "spaces", "coworkercontracts": "billing",
    "contractproducts": "billing", "contractschedules": "billing",
    "contractpausedperiods": "billing", "contractdeposits": "billing",
    "coworkerinventoryassets": "spaces", "bookings": "spaces",
    "bookingproducts": "spaces", "bookingvisitors": "spaces", "checkins": "spaces",
    "coworkerextraservices": "billing", "coworkerbookingcredits": "billing",
    "coworkerbookingcreditusehistories": "billing", "coworkertimepasses": "billing",
    "coworkerproducts": "billing", "coworkerdeliveries": "spaces",
    "calendarevents": "content", "eventattendees": "content",
    "eventproducts": "content", "helpdeskmessages": "support",
    "communitythreads": "community", "communitymessages": "community",
    "blogposts": "content", "coworkertasks": "crm", "coworkerledgerentries": "billing",
    "coworkerinvoices": "billing", "coworkerinvoicehistories": "billing",
    "crmopportunities": "crm", "crmopportunityhistories": "crm", "opportunitytypes": "crm",
    "proposals": "billing", "coworkerdatafiles": "spaces", "users": "sys",
    "businesses": "sys", "cancelledbookings": "spaces",
    "tarifftimepasses": "billing", "tariffextraservices": "billing",
    "businesssettings": "sys",
}

DEFAULT_TIMEOUT = 30
MAX_PAGES = 50  # safety cap for auto-pagination
MAX_RATE_LIMIT_RETRIES = 8
_RATE_LIMIT_WAIT_RE = re.compile(r"Wait ([\d.]+) seconds")

# Nexudus's generic catch-all for an unhandled server-side exception — the
# same text shows up for genuinely deterministic rejections (e.g. an "open"
# CheckIn dated in the past, an EventAttendee with no CoworkerId — both
# fixed at the data level, see CLAUDE.md rules 33/34) AND for plain
# transient flakiness. Confirmed live it can be *bursty*, not just
# occasional: an identical CrmOpportunity create body failed 5 times in a
# row, then succeeded 8 times in a row right after, with no code or data
# change in between — a 3-attempt retry (this constant's original value)
# wasn't always enough to ride out a bad streak. A genuinely deterministic
# failure still just wastes a few extra seconds retrying before raising.
MAX_TRANSIENT_ERROR_RETRIES = 6
_TRANSIENT_ERROR_TEXT = "Ooops! There was a problem while running this action"


def _is_transient_server_error(resp):
    if resp.status_code != 500:
        return False
    try:
        body = resp.json()
    except ValueError:
        return False
    return isinstance(body, dict) and _TRANSIENT_ERROR_TEXT in str(body.get("Message", ""))


class NexudusApiError(RuntimeError):
    def __init__(self, entity, action, response):
        self.entity = entity
        self.action = action
        self.response = response
        detail = ""
        try:
            body = response.json()
            detail = body.get("Message") or body.get("Errors") or body.text
        except Exception:  # noqa: BLE001
            detail = response.text[:500]
        super().__init__(f"{action} {entity} failed ({response.status_code}): {detail}")


def _url(entity):
    module = ENTITY_MODULES.get(entity)
    if module is None:
        raise KeyError(
            f"'{entity}' isn't in ENTITY_MODULES — add its module to nexudus_client.py "
            f"(check https://learn.nexudus.com/rest-api/<module>/get-{entity}.md)"
        )
    return f"{base_url()}/api/{module}/{entity}"


def _headers():
    return {"Authorization": f"Bearer {get_access_token()}"}


_WRITE_METHODS = {"POST", "PUT", "DELETE"}


def _request(method, url, **kwargs):
    """requests.request, but retries on:
    - 429, for the server's suggested wait (parsed from its "Wait N
      seconds" message), plus a small buffer.
    - a 500 with Nexudus's generic transient-error text, up to
      MAX_TRANSIENT_ERROR_RETRIES times (see _is_transient_server_error).

    Also paces writes (see config.WRITE_PACING_SECONDS) — once per call,
    not per retry attempt, since retries already have their own backoff."""
    if method in _WRITE_METHODS and WRITE_PACING_SECONDS:
        time.sleep(WRITE_PACING_SECONDS)

    transient_attempts = 0
    for attempt in range(MAX_RATE_LIMIT_RETRIES):
        resp = requests.request(method, url, timeout=DEFAULT_TIMEOUT, **kwargs)
        if resp.status_code == 429:
            wait = 1.0
            match = _RATE_LIMIT_WAIT_RE.search(resp.text)
            if match:
                wait = float(match.group(1))
            time.sleep(wait + 0.25)
            continue
        if transient_attempts < MAX_TRANSIENT_ERROR_RETRIES - 1 and _is_transient_server_error(resp):
            transient_attempts += 1
            time.sleep(1.5 * transient_attempts)  # 1.5s, 3s, 4.5s, ... — rides out a bursty streak
            continue
        return resp
    return resp


def _unwrap(entity, action, resp):
    """Create/update responses are wrapped: {"Status", "WasSuccessful",
    "Value": {...the record...}, ...}. GET responses are the raw record
    directly, unwrapped. Some entities (e.g. CrmOpportunity) have their own
    field literally named "Value" — checking for the "Value" key alone to
    decide whether to unwrap breaks GET for those (silently returns just
    that field's content, confirmed live). "WasSuccessful" only appears on
    the wrapper, never as a real entity field, so it's the reliable signal.
    """
    if not resp.ok:
        raise NexudusApiError(entity, action, resp)
    body = resp.json()
    if not isinstance(body, dict) or "WasSuccessful" not in body:
        return body
    if not body["WasSuccessful"]:
        raise NexudusApiError(entity, action, resp)
    return body.get("Value", body)


def nexudus_list(entity, filters=None):
    """GET — returns every matching record, auto-paginating up to MAX_PAGES.

    Pagination query params are "page" and "size", NOT "PageNumber"/
    "PageSize" — confirmed live that the latter are silently ignored
    (unrecognized params), so every request fell back to the API's
    defaults (page 1, size 25) regardless of what was sent. That made
    every previous version of this function under-fetch (silently
    truncated at 25 records) or, combined with a since-fixed
    HasNextPage-trusting loop, over-fetch by re-reading page 1 forever.
    Confirmed via https://learn.nexudus.com/rest-api/spaces/get-coworkers.md.
    Paginates on TotalPages/CurrentPage, which behave correctly with the
    right param names.
    """
    params = dict(filters or {})
    params.setdefault("size", 100)
    records = []
    page = 1
    while page <= MAX_PAGES:
        params["page"] = page
        resp = _request("GET", _url(entity), headers=_headers(), params=params)
        if not resp.ok:
            raise NexudusApiError(entity, "list", resp)
        body = resp.json()
        records.extend(body.get("Records", []))
        total_pages = body.get("TotalPages", 1)
        if page >= total_pages:
            break
        page += 1
    return records


def nexudus_get(entity, id):
    resp = _request("GET", f"{_url(entity)}/{id}", headers=_headers())
    return _unwrap(entity, "get", resp)


def nexudus_create(entity, body):
    resp = _request("POST", _url(entity), headers=_headers(), json=body)
    return _unwrap(entity, "create", resp)


# Generic metadata fields every entity returns that aren't meant to be sent
# back on a PUT — entity-specific read-only fields (denormalized names like
# CurrencyCode, ResourceTypeNames, etc.) are left in deliberately: PUT seems
# to require the full record rather than a true partial patch (confirmed via
# a 400 "X is a required field" when sending only the changed fields), and
# echoing back read-only fields unchanged has not caused rejections.
_UPDATE_STRIP_FIELDS = {
    "CreatedOn", "UpdatedOn", "UpdatedBy", "UniqueId", "IsNew", "SystemId",
    "ToStringText", "LocalizationDetails", "CustomFields",
}


def nexudus_update(entity, id, body):
    """PUT — merges `body` onto the record's current full state (fetched via
    GET) rather than sending only the changed fields, since this API's PUT
    validates as if the whole record must be present."""
    current = nexudus_get(entity, id)
    payload = {k: v for k, v in current.items() if k not in _UPDATE_STRIP_FIELDS}
    payload.update(body)
    payload["Id"] = id

    resp = _request("PUT", _url(entity), headers=_headers(), json=payload)
    return _unwrap(entity, "update", resp)


def nexudus_delete(entity, id):
    resp = _request("DELETE", f"{_url(entity)}/{id}", headers=_headers())
    if not resp.ok:
        raise NexudusApiError(entity, "delete", resp)
    # DELETE can return HTTP 200 with a business-logic failure in the body
    # (WasSuccessful: false) rather than a non-2xx status — confirmed live,
    # e.g. deleting a BookingVisitor whose sibling record blocks it. Trusting
    # resp.ok alone silently treats that as a success. Check the body the
    # same way _unwrap does for create/update/run_command; a delete with no
    # body (or a non-JSON one) still means success.
    try:
        body = resp.json()
    except ValueError:
        return None
    if isinstance(body, dict) and body.get("WasSuccessful") is False:
        raise NexudusApiError(entity, "delete", resp)
    return None


def nexudus_run_command(entity, command_key, ids, parameters=None):
    payload = {"Key": command_key, "Ids": ids, "Parameters": parameters or []}
    resp = _request("POST", f"{_url(entity)}/runcommand", headers=_headers(), json=payload)
    return _unwrap(entity, "run_command", resp)
