# Nexudus REST API — module mapping

Every entity lives under `https://spaces.nexudus.com/api/<module>/<entity>`.
`nexudus_client.py`'s `ENTITY_MODULES` dict is the canonical source this repo
uses at runtime — this file documents where it came from and how to extend it.

## Source

Pulled directly from the Nexudus platform's own entity catalog (the same
schema source the `claude.ai Nexudus` MCP server's `nexudus_list_entities`
tool exposes), not guessed or scraped from doc pages. That catalog gives,
per entity: `apiPath`, `module`, and `operations` (list/get/create/update/
delete/run-command).

## Adding a new entity

If a generator needs an entity not yet in `ENTITY_MODULES`, confirm its
module before adding it — don't guess:

- Fastest: ask an agent with the Nexudus MCP connector to check
  `nexudus_describe_entity` or the entity catalog.
- Or fetch `https://learn.nexudus.com/rest-api/<guessed-module>/get-<entity>.md`
  — if it 404s, the module guess is wrong. Modules seen so far: `sys`,
  `spaces`, `billing`, `crm`, `community`, `content`, `support`, `apps`.

## Gotchas found while building this

- **Not every entity supports every verb.** `businesses` and `coworkerinvoices`
  have no create/delete/commands (list/get/update only) — Nexudus generates
  those server-side. `coworkers` has no delete, but does support commands.
  `coworkerbookingcreditusehistories` has no delete either. Check the
  catalog's `operations` list before assuming a verb works.
- **PUT (update) doesn't take the ID in the URL** — same URL as create, with
  `"Id"` in the request body instead. `nexudus_client.nexudus_update` handles
  this (`{"Id": id, **body}`).
- **Commands go to `{url}/runcommand`**, POST, body
  `{"Key": "...", "Ids": [...], "Parameters": [{"Name", "Value"}, ...]}`.
- **Create/update responses are wrapped**: `{"Status", "Message", "Value":
  {...the record...}, "WasSuccessful", "Errors", ...}`. The client unwraps
  `"Value"` and raises on `WasSuccessful: false`.
