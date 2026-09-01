# Multi-location (multi-business) support — analysis & what it would take

**Status:** not supported. The panel lets you *point* runs at a different Nexudus
business, but the local state that tracks "what got created" is global, so a second
location is never a clean slate.

This doc captures the analysis so it can be picked up later without re-deriving it.

---

## The question

> If your admin login has access to more than one Nexudus location, does the UI let you
> switch to another one and run a fresh seeding session against it?

## Short answer

**No.** Switching the header dropdown changes which business the *next run* creates records
in (`--business-id`). But:

- `data/created-ids/` (the source of truth for "what this tool has created live") is **one
  flat directory**, not per-business.
- Two of the generators check "does this already exist?" by listing records across the
  **whole login**, not one business.
- `teardown` deletes by tracked ID regardless of the dropdown.
- The panel's "seeded" numbers, `verify`, and the last-run report all read that one global
  tracking dir.

So seeding location B after location A: the idempotency checks see A's records, skip most
of the work, and you end up with B partially seeded and the panel showing A's numbers.

The project's design assumes **one target business at a time** (see `CLAUDE.md` rule 8:
`pipeline._select_business` auto-selects only when there's exactly one business, otherwise
requires an explicit `--business-id` — it's a routing parameter, not an isolation boundary).

---

## What works today

- `webui/static/app.js :: renderLocation()` renders a `<select>` in the header **only when
  the login has > 1 business**. On change it sets `state.businessId`, persists it to
  `localStorage`, and re-renders.
- `state.businessId` is passed to every command whose registry entry has
  `accepts_business_id: true` — the guided flow (`wizard`), `daily_update`,
  `refresh_output`, `teardown` — and `registry.build_argv()` turns it into
  `--business-id <id>`.
- The generators, once given `--business-id`, **create** records in the right place:
  `generators/02_people.py:118-119` sets `"Businesses": [biz]` + `"InvoicingBusinessId":
  biz` on each coworker; the reference/structural/financial layers already filter their
  existence checks by `*_Business` (see the list below).

So "create records in location B" works. "Do it as an independent session" does not.

---

## What's broken for a second location

### 1. Local tracking is one flat directory

`config.py`:

```python
CREATED_IDS_DIR = DATA_DIR / "created-ids"     # data/created-ids/<generator>.json
OUTPUT_DIR      = PROJECT_ROOT / "output"      # output/<entity>.csv
```

`generators/base.py:94`:

```python
self._ids_file = CREATED_IDS_DIR / f"{self.entity_name}.json"
```

`report_lib.py:23`:

```python
REPORT_PATH = PROJECT_ROOT / "last-run-report.txt"
```

None of these are keyed by business id. So after seeding A:

- `already_created(key_field, key_value)` (base.py:215) checks the same
  `data/created-ids/*.json` no matter which business the header says. Every generator that
  dedupes purely against local tracking (layers **03, 05, 07 make zero `nexudus_list`
  calls** — they rely entirely on this) will skip everything it made for A when you run B.
- `data/plan-manifest.json` (prebuild's incremental record) is global — but that's fine,
  the *plan* (`data/*.json`: coworkers named X, emails `test-001@seeddata.local`, …) is
  business-agnostic. Only the record of *what got created live* needs partitioning.
- `report_lib.tracked_counts()` / `report_lines()` / `write_entity_csvs()` and therefore
  `webui/report.py::gather_plan()` + `gather_report()` show A's counts while you're
  pointed at B.

### 2. Two generators check existence across the whole login

Most existence checks are already business-scoped:

| generator | entity | filter |
|---|---|---|
| `00_reference.py:119/146/173` | taxrates / financialaccounts / resourcetypes | `*_Business` |
| `01_structural.py:517…1113` | tariffs, products, extraservices, timepasses, resources, floorplans, inventoryassets, discountcodes, crmboards | `*_Business` |
| `02_people.py:183` | visitors | `Visitor_Business` |
| `06_financial.py:337/595` | coworkerinvoices | `CoworkerInvoice_Business` |
| `04_activity.py:561` | coworkerextraservices | by `CoworkerExtraService_Coworker` (transitively business-scoped) |

The two that are **not**:

- **`generators/02_people.py:84`** — `existing = nexudus_list("coworkers", {})` then dedupe
  by email. `Coworker_Email` is an exact-match filter that doesn't do substring, so the
  code lists *all* coworkers and filters client-side by `@seeddata.local`. It never filters
  by business, so B's run finds A's `test-001@seeddata.local` and skips creating it — and
  worse, maps `coworker_ids[idx]` to A's coworker record, so every downstream child in B
  (contracts, bookings, check-ins…) attaches to A's coworkers.
- **`generators/daily_update.py:353`** — `all_coworkers = nexudus_list("coworkers", {})`,
  same pattern, for resolving who to create today's activity for.

`CLAUDE.md` rule 28 already documents that this area bit the project once ("a
multi-business login had coworkers land in the wrong business" before `Businesses` /
`InvoicingBusinessId` were set explicitly).

### 3. `teardown` isn't location-scoped

`teardown.py` pools `data/created-ids/*.json` and deletes by tracked ID. Those IDs are
whatever was last tracked — location A's records — regardless of the header dropdown. It
does already contain business-selection code (`_prompt_business_id`,
`maybe_reset_business_counters`, added for the billing-counter-reset feature — rule 47), so
the plumbing to *ask which business* exists; it just isn't used to scope the delete pool.

### 4. `verify` and `refresh_output`

- `verify` = `bash scripts/verify.sh`, which runs an inline
  `python3 -c "from report_lib import report_lines; print('\n'.join(report_lines()))"` —
  no business argument anywhere.
- `refresh_output.py` already queries invoices with `{"CoworkerInvoice_Business": biz}` and
  resolves a business via `pipeline._select_business`, but writes discovered
  `DiscoveredInvoiceId`s into the flat `financial.json`.

---

## What it would take to fix

### Half 1 — partition the tracking (mechanical, broad, low risk)

Thread a `business_id` through the read/write side of tracking + reporting, and give
`created-ids/` (and the report/CSV outputs) one more directory level.

| file | change |
|---|---|
| `config.py` | `CREATED_IDS_DIR` → `created_ids_dir(business_id)` returning `DATA_DIR / "created-ids" / str(business_id)`. Same idea for the per-business report + CSV output (or keep CSVs merged with a `Business` column — decision below). |
| `generators/base.py` | `_ids_file` resolved from the run's business id instead of the module constant. Cleanest: `BaseGenerator.__init__(dry_run=…, business_id=…)` or set the ids dir lazily on the first `run()` (business id is in `prev_output["business_id"]`). |
| `pipeline.py` | `run_up_to` already resolves the business (`_whoami` → `_select_business`); pass it into each `getattr(module, class_name)(…)` (loop around line 226). Also the per-layer `report_lib.write_entity_csvs(OUTPUT_DIR)` call. |
| `report_lib.py` | every function (`_grouped_records`, `tracked_counts`, `report_lines`, `run_reconciliation_lines`, `write_entity_csvs`, `write_report`, `REPORT_PATH`) takes a `business_id` (or a module-level "current context" set once per run). ~all of the file. |
| `teardown.py` | scope the `data/created-ids/*.json` pool to one business's subdir; reuse the existing business-selection prompt; `run_teardown()` signature + `scripts/teardown.sh`. |
| `refresh_output.py` | write discovered invoices into that business's tracking dir. |
| `prebuild.py` | **none** — the plan and `plan-manifest.json` stay global (business-agnostic). |
| `webui/report.py` | `gather_plan()` / `gather_report()` take the selected business id. |
| `webui/handler.py` | `/api/plan` + `/api/report` read a `?business_id=` (from the panel). |
| `webui/registry.py` + `scripts/verify.sh` | plumb `--business-id` into `verify` so its `report_lines()` call is scoped. |
| `webui/static/app.js` | add `state.businessId` to the `/api/plan` + `/api/report` fetches; re-fetch on location change (already re-renders on change). |
| migration | move the current flat `data/created-ids/*.json` into `data/created-ids/1421021016/` (this branch's committed data is the "Explore 2.0" business) — or a fallback that treats a flat dir as "unknown/legacy business". |
| tests | `test_teardown.py`, `test_webui.py`, and any `report_lib` tests assume a flat `created-ids/`. Moderate churn. |

Roughly **~10 files, ~1 day**. It's not architecturally deep — a business id threads
through as a parameter — but it touches most of the pipeline, teardown, reporting, and the
webui.

### Half 2 — make a second-location seed actually *clean*

Only **two spots** (better than feared):

- `generators/02_people.py:84` — after `existing = nexudus_list("coworkers", {})`, filter to
  this business before building `existing_by_email`:
  ```python
  existing = [r for r in existing
              if biz in (r.get("Businesses") or []) or r.get("InvoicingBusinessId") == biz]
  ```
- `generators/daily_update.py:353` — same filter on `all_coworkers`.

**But** this is the part that needs a **live multi-location admin account to verify**,
because it depends on API behaviour this project's convention says must be confirmed live,
not assumed:

- Does `Coworker.Businesses` reliably contain the seeding business for records this tool
  created? (rule 28 confirms the field exists and round-trips; needs a live check that the
  filter above actually partitions cleanly.)
- Is there a server-side `Coworker_Business` / `Coworker_InvoicingBusiness` filter that
  would be cleaner than listing-all + client-side filtering? (the `Coworker_Email` comment
  at `02_people.py:81` suggests the coworker list endpoint's filters are limited.)
- Any other entity whose *live* dedup could bleed once real multi-location data exists —
  re-audit `grep -rn 'nexudus_list(' generators/` against a two-location account.

Getting this wrong = silent cross-location contamination (B's children attached to A's
coworkers), which is exactly the failure mode rule 28 describes.

---

## Decisions to make before building

1. **CSV output** — per-business `output/<id>/*.csv`, or one merged `output/*.csv` with a
   `Business` column? (Merged keeps the "one folder of CSVs" UX; per-business matches the
   tracking layout.)
2. **`last-run-report.txt`** — per business, or one report that covers whichever business
   the last run targeted?
3. **Panel UX** — when you switch location, should the "seeded" column and the Results
   panel immediately reflect the new business (needs the `?business_id=` plumbing), and
   should the guided-flow "delta" recompute against that business's counts? (Yes, but it's
   work.)
4. **Migration** — auto-move the flat `data/created-ids/*.json` into a `<business-id>/`
   subdir on first run, or require a manual `python -m tools.migrate_tracking <id>`?
5. **Is this actually needed?** If the real use is "one demo account", none of this is
   worth it — the current single-target design is correct. It only matters if someone runs
   two live locations from the same checkout.

---

## Quick repro of the current behaviour

1. Login with a multi-business admin. Header shows the location `<select>`.
2. Pick location A, run the guided flow → records land in A, `data/created-ids/` fills up.
3. Pick location B, run the guided flow again → console shows almost everything
   "already exists" / skipped; B gets few or no records; the Data volumes panel still
   shows A's "seeded" counts.
4. Run `teardown` with the dropdown on B → it deletes **A's** records (by tracked ID).
