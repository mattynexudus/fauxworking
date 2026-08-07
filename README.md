# Nexudus Test Data Generator

Generates and seeds realistic test data into a Nexudus instance. Populates all reports and dashboards with ~1,700+ records across 50+ entity types.

Talks directly to the Nexudus REST API — no LLM/agent needed to run it. See [Architecture](#architecture) for why.

## Quick Start

The simplest way to run this is the interactive wizard — it handles login, lets you set how much data to generate, and runs the pipeline:

```bash
pip install -r requirements.txt
python3 wizard.py
```

Or step through it manually:

```bash
pip install -r requirements.txt

# One-time login. Run this yourself, in your own terminal — it prompts for
# your Nexudus email/password locally and never passes them anywhere else.
# Only the resulting access/refresh tokens get saved, to a gitignored .env.
python3 nexudus_auth.py setup

python3 prebuild.py              # One-time: generate data/*.json files
bash scripts/seed_all.sh        # Push everything to Nexudus, layers 0-7
bash scripts/daily.sh           # Create today's fresh records (run daily/on-demand)
bash scripts/verify.sh          # Check record counts against targets
```

Re-running `seed_all.sh` is safe — every generator checks for existing records before creating (by name, email, or a locally tracked ID) and skips anything already there. Every run — via the wizard or `pipeline.py` directly — prints a created/skipped/failed summary per layer, a cross-layer total, and a report of what's actually tracked in the account now (also saved to `data/last-run-report.txt`), so you always know exactly what's in the account, not just what this run tried to do.

### Requirements

- The logged-in Nexudus account must be an **admin with API access enabled**. `nexudus_auth.py setup` checks this immediately and fails with a clear error if not, rather than letting a seeding run fail halfway through.
- Your refresh token is valid for ~30–90 days (Nexudus-side). Re-run `python3 nexudus_auth.py setup` when it finally expires — you'll get a clear "Not authenticated" error telling you to.

### Running one layer at a time

```bash
bash scripts/seed_layer.sh 3    # Runs layers 0-3 (each generator re-verifies
                                 # earlier layers exist before doing its own work)
```

### Tearing down

```bash
python3 teardown.py --dry-run    # Preview what would be deleted
python3 teardown.py              # Actually delete every tracked record
```

Deletes strictly by ID from `data/created-ids/*.json` — never by name pattern, never touches anything this project didn't create. Records it doesn't know how to delete (a handful of Nexudus entities are audit-trail-only) are logged and skipped, not silently ignored.

## Architecture

**Why a direct API client instead of an agent/MCP loop:** this project creates ~1,700+ individual records. Routing each one through an LLM tool call would be slow and burn a lot of tokens on pure mechanical data entry that doesn't need any judgment once the data's already generated. So the actual execution path is:

```text
nexudus_auth.py    → one-time interactive login, silent token refresh after
nexudus_client.py  → thin requests wrapper over https://spaces.nexudus.com/api/...
pipeline.py         → chains generator layers 0-7 together, in-process
generators/*.py     → the actual per-entity creation logic (pure Python, no I/O
                       beyond the client calls above)
```

An agent with the Nexudus MCP connector was used *during development* to explore the schema and figure out field names/gotchas (see `reference/`) — that's a one-time research cost, not part of every run.

**Two-step data flow**, unrelated to the above:

1. `prebuild.py` generates deterministic profiles (names, emails, scenario assignments) into committed `data/*.json` files — run once, or again with `--seed` to regenerate
2. Generators read those files and push to the API, resolving day/month offsets against `config.TODAY` at run time — this is what makes the whole dataset a rolling window instead of fixed dates

**One exception:** `06_financial.py` has no `data/*.json` file. Invoice IDs and amounts don't exist until Nexudus generates them server-side, so it discovers them live via the API instead of reading a pre-planned file.

## Credential handling

- `.env` (gitignored, chmod 600) holds only OAuth tokens — never your password. `nexudus_auth.py setup` is meant to be run by you, in your own terminal, so the password prompt is never observed by an agent.
- If you're running this through an agent for anything else in this repo: it will not read, print, or echo `.env` — every tool call is visible in your session transcript if you want to verify that.
- Want an even stronger boundary than "won't read it"? Point `NEXUDUS_ENV_PATH` (or just move the file) somewhere outside the repo entirely, e.g. `~/.config/nexudus-test-data/.env` — nothing currently assumes `.env` lives in the project root except the default path in `nexudus_auth.py`.

## Rolling Date Window

All dates are **relative to the run date** — the data spans 24 months back from today. No hardcoded dates. Re-running on a different day shifts the entire window.

## Structure

| Path | Purpose |
|-----------|---------|
| `wizard.py` | Interactive setup + run: auth, data volumes, dry-run/live |
| `nexudus_auth.py` | One-time login, token storage/refresh |
| `nexudus_client.py` | REST API wrapper (list/create/update/delete/run_command) |
| `pipeline.py` | Chains generator layers together for a live run; prints the run summary/report |
| `report_lib.py` | Shared "what's tracked vs. target" logic, used by `pipeline.py` and `scripts/verify.sh` |
| `config.py` | Volumes, configurable-volume allowlist, rolling date helpers, test markers |
| `prebuild.py` | Data generation (faker profiles → `data/*.json`), with `--flags` for the configurable volumes |
| `generators/` | One Python file per layer (00–07) + daily update |
| `generators/base.py` | Shared base class: idempotency, ID tracking, dry-run, run-summary counting |
| `teardown.py` | Deletes every tracked record, reverse dependency order |
| `scripts/` | Shell wrappers: seed_all, seed_layer, daily, teardown, verify |
| `reference/` | Entity dependency graph, API module map, enum values, extending-the-model guide |
| `tests/` | Unit tests for the pure-logic pieces (volume rescaling, run-summary counting) — `python -m unittest discover tests` |
| `data/created-ids/` | Runtime: JSON files tracking IDs of records created per generator (gitignored) |
| `data/*.json` | Pre-generated profiles (coworkers, contracts, bookings, etc.) — committed |

## Layers

| Layer | Generator | What |
|-------|-----------|------|
| 0 | `00_reference.py` | Tax rates, financial accounts, resource types |
| 1 | `01_structural.py` | Tariffs, products, resources, desks, inventory, discounts, CRM boards |
| 2 | `02_people.py` | Coworkers + visitors |
| 3 | `03_contracts.py` | Contracts, deposits, freezes, inventory assignments, occupancy |
| 4a | `04_activity.py` | Bookings, check-ins, credits, passes |
| 4b | `05_community.py` | Deliveries, events, help desk, threads, blogs, tasks |
| 5 | `06_financial.py` | Invoice triggering, payments, ledger, void/credit-note |
| 5 | `07_crm_proposals.py` | CRM opportunities, proposals |
| — | `daily_update.py` | Fresh daily records (check-ins, bookings, visitors, deliveries) |

Each generator also runs standalone — `python3 generators/03_contracts.py` re-verifies layers 0-3 exist (idempotently) before doing its own work, via `pipeline.run_up_to(3)`.

## Dry Run

Every generator and script supports `--dry-run` — logs what would be created without making any API calls or needing credentials at all:

```bash
python3 generators/03_contracts.py --dry-run
python3 teardown.py --dry-run
```

## Configuring data volumes

The headline counts that matter for a demo — coworkers, visitors, bookings, check-ins, CRM opportunities, proposals, help desk messages, community threads, coworker tasks/time-passes/products — are configurable per run, via `wizard.py`'s prompts or flags directly on `prebuild.py`:

```bash
python3 prebuild.py --coworkers 10 --bookings 20
```

See `config.CONFIGURABLE_VOLUME_KEYS` for the full list, and `reference/extending-the-model.md` for why the rest of the ~50 entity types (resources, teams, calendar events, ...) aren't included — they're hand-authored content, not just a number to scale.

## Test Markers

Records are meant to look like real data — no `[TEST]` name prefixes. The only marker baked into a record is the coworker email (`test-NNN@seeddata.local`); everything else is identified purely by its tracked ID in `data/created-ids/`, which is what `teardown.py` uses.

## More detail

See `CLAUDE.md` for the full standing rules (idempotency conventions, API gotchas discovered while building this, entity-specific patterns) and `reference/extending-the-model.md` for how to safely add or extend a record type — the data-shape conventions and dependency graph, not the operational rules. `test-data-seeding-strategy.md` is a historical build-status log from the original design, not a live reference.
