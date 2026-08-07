# Extending the data model

A guide for adding or extending record types in this generator, written for
whoever (human or AI agent) is picking this up without the context of
having built it. Covers the *static* data-model conventions — how records
reference each other, how they're shaped, how to safely add more. For the
*operational* rules around calling the live API safely (idempotency,
UTC dates, command gotchas, invoice/proposal/booking mechanics, etc.), see
`CLAUDE.md` at the project root — this doc doesn't repeat any of that.

## Where the rest of the reference material lives

| Question | Look at |
|---|---|
| What depends on what, and how many of each? | `reference/entity-dependencies.md` |
| What does enum value X mean on entity Y? | `reference/field-enums.md` |
| Which Nexudus API module is entity X under? | `reference/api-modules.md` |
| Is action X safe / what's the real mechanism? | `CLAUDE.md` (27 standing rules) |

`entity-dependencies.md` and `field-enums.md` were spot-checked against the
current code while writing this doc and are largely accurate, but
`entity-dependencies.md` predates some of this session's fixes and is stale
in at least two places: it still describes cancelling a booking via delete
(the real mechanism is the `CANCEL_BOOKING` command — CLAUDE.md rule 11) and
invoice void/credit-note via a `CoworkerInvoiceHistory` narration record (the
real mechanism is the `VOID_INVOICE`/`COWORKER_INVOICE_CANCEL` commands —
CLAUDE.md rule 12). Trust CLAUDE.md over it for anything command-related.

`test-data-seeding-strategy.md` at the project root is a historical
build-status log from when this repo was first built, not a live reference —
don't treat anything in it as current without cross-checking the code.

## Data-shape conventions

These aren't documented anywhere else — they're conventions this codebase
follows consistently, not enforced by the framework:

- **Sequential `index` + `*Index` cross-references.** Every record `prebuild.py`
  generates gets a 1-based `"index"` field. Records that reference another
  record reference it by that index, in a field named `<Entity>Index` (e.g.
  a booking's `CoworkerIndex` points at a coworker's `"index"`), not by any
  real ID — real Nexudus IDs don't exist until a live create call returns
  one. The layer generators resolve `*Index` → real ID via a dict built up
  as they create things (e.g. `self.coworker_ids[idx] = result["Id"]`).
- **Day offsets, not absolute dates.** All dates in `data/*.json` are
  `*DayOffset` integers (relative to `config.TODAY`), converted to an
  absolute UTC string only at create time via `config.to_utc_str()`. This is
  what makes the whole dataset "rolling" — rerun `prebuild.py` on a later
  date and every date shifts forward with it. Never write an absolute date
  into a `generate_*` function.
- **`track_key` + `already_created(key_field, key_value)`.** The idempotency
  pattern used throughout every `_create_*` method: build a `track_key`
  (usually `str(defn["index"])`, occasionally a composite like
  `f"{a_index}:{b_index}"` for a many-to-many link), check
  `self.already_created("SomeIndexFieldName", track_key)` before creating,
  and call `self.track_id({...})` with that same field name after — see
  `generators/base.py`. Pick a `key_field` name that's unique to that record
  type across the whole tracked-ids file (they all share one file per
  generator, not per entity).
- **The `"entity"` tag.** Every dict passed to `track_id()` needs an
  `"entity"` key set to the Nexudus apiPath (e.g. `"coworkers"`,
  `"bookings"`) — `teardown.py` and `report_lib.py` both group tracked
  records by this field, not by which generator created them.
- **The `__main__` dry-run mock block.** Every `generators/0N_*.py` has an
  `if gen.dry_run:` branch in `__main__` that builds fake `prev_output`-shaped
  data and no-op `nexudus_*` callables, so `python generators/0N_x.py
  --dry-run` works standalone without hitting the live API or needing
  earlier layers to have actually run. Keep this in sync with whatever
  `prev_output` keys a new `_create_*` method actually needs.

## Adding a new record type

1. Confirm the entity's Nexudus module and available operations — see
   `reference/api-modules.md`, or ask an agent with Nexudus MCP access to
   check `nexudus_describe_entity`. Don't guess.
2. Decide which layer it belongs in via `reference/entity-dependencies.md`
   — after whatever it depends on, before whatever depends on it.
3. If it needs faker-driven identity/content data (names, dates, random
   selections), add a `generate_X(rng, ...)` function to `prebuild.py`,
   following the `index` + `*Index` convention above. If it's purely
   derived from what already exists live (e.g. financial entities whose
   IDs only exist server-side), it doesn't need a `data/*.json` file at all
   — see `generators/06_financial.py`'s docstring for why it has none.
4. Add a `_create_X(...)` method to the right `generators/0N_*.py`, following
   the `track_key`/`already_created`/`track_id` idempotency pattern. Wire it
   into that generator's `run()` and into its `__main__` dry-run mock.
5. If you want the new entity's volume to be user-configurable, add it to
   `config.CONFIGURABLE_VOLUME_KEYS` and give `generate_X` a `count=`/`total=`
   parameter defaulting to `VOLUMES["your_key"]` — see `prebuild.py`'s
   `rescale_plan()` if the volume needs to split across sub-categories
   (statuses, stages, scenarios, ...) rather than being one flat number.
   Skip this if the content is hand-authored (specific names/descriptions)
   rather than just "more of the same" — see the exclusion reasoning in
   `config.py` next to `CONFIGURABLE_VOLUME_KEYS`.
6. Add its target count to `config.VOLUMES` (even if not in
   `CONFIGURABLE_VOLUME_KEYS`) so `scripts/verify.sh` / `report_lib.py` can
   report on it, and add its apiPath → VOLUMES-key mapping to
   `report_lib.TARGET_KEY_BY_ENTITY`.
7. Add it to `teardown.py`'s delete order, in the correct reverse-dependency
   position (children before parents).

## Extending an existing record type

Smaller version of the same idea — adding a field, a new named entry to a
content list (like `PRODUCTS` or `NAMED_EVENTS`), or a new scenario bucket:

- If it's a genuinely new field on an existing entity, check it's writable
  at create time (not `updateOnly`) via `reference/field-enums.md` or
  `nexudus_describe_entity` before wiring it in — several fields in this
  codebase (e.g. `CoworkerTimePass.Used`, `Coworker.UserId`) turned out to
  require a follow-up `nexudus_update` instead.
- If it's a new entry in a hand-authored content list (a product, an event,
  a blog post, ...), add matching content everywhere that list is paired
  with one — e.g. a new calendar event name in `prebuild.py`'s
  `EXTRA_EVENT_NAMES` needs a matching entry in `EVENT_DESCRIPTIONS`, or
  `generate_calendar_events` will crash on the missing key.
- If it's a new bucket in a plan-list (like `LIFECYCLE_SCENARIOS` or
  `CRM_STAGE_PLAN`), it'll automatically participate in `rescale_plan()`
  wherever that list is already wired to a configurable volume — no extra
  work needed there, just add the tuple.
