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

- GET    {url}            list (filters + PageNumber/PageSize as query params)
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

import os
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
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
    "crmopportunities": "crm", "crmopportunityhistories": "crm",
    "proposals": "billing", "coworkerdatafiles": "spaces", "users": "sys",
    "businesses": "sys", "cancelledbookings": "spaces",
}

DEFAULT_TIMEOUT = 30
MAX_PAGES = 50  # safety cap for auto-pagination


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


def _unwrap(entity, action, resp):
    if not resp.ok:
        raise NexudusApiError(entity, action, resp)
    body = resp.json()
    if isinstance(body, dict) and "WasSuccessful" in body and not body["WasSuccessful"]:
        raise NexudusApiError(entity, action, resp)
    return body.get("Value", body) if isinstance(body, dict) else body


def nexudus_list(entity, filters=None):
    """GET — returns every matching record, auto-paginating up to MAX_PAGES."""
    params = dict(filters or {})
    params.setdefault("PageSize", 100)
    records = []
    page = 1
    while page <= MAX_PAGES:
        params["PageNumber"] = page
        resp = requests.get(_url(entity), headers=_headers(), params=params, timeout=DEFAULT_TIMEOUT)
        if not resp.ok:
            raise NexudusApiError(entity, "list", resp)
        body = resp.json()
        records.extend(body.get("Records", []))
        if not body.get("HasNextPage"):
            break
        page += 1
    return records


def nexudus_get(entity, id):
    resp = requests.get(f"{_url(entity)}/{id}", headers=_headers(), timeout=DEFAULT_TIMEOUT)
    return _unwrap(entity, "get", resp)


def nexudus_create(entity, body):
    resp = requests.post(_url(entity), headers=_headers(), json=body, timeout=DEFAULT_TIMEOUT)
    return _unwrap(entity, "create", resp)


def nexudus_update(entity, id, body):
    payload = {"Id": id, **body}
    resp = requests.put(_url(entity), headers=_headers(), json=payload, timeout=DEFAULT_TIMEOUT)
    return _unwrap(entity, "update", resp)


def nexudus_delete(entity, id):
    resp = requests.delete(f"{_url(entity)}/{id}", headers=_headers(), timeout=DEFAULT_TIMEOUT)
    if not resp.ok:
        raise NexudusApiError(entity, "delete", resp)
    return None


def nexudus_run_command(entity, command_key, ids, parameters=None):
    payload = {"Key": command_key, "Ids": ids, "Parameters": parameters or []}
    resp = requests.post(f"{_url(entity)}/runcommand", headers=_headers(), json=payload,
                          timeout=DEFAULT_TIMEOUT)
    return _unwrap(entity, "run_command", resp)
