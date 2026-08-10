# Nexudus Test Data Generator

This tool fills a Nexudus account with realistic-looking test data — customers, bookings, invoices, contracts, and more — so you have something real to test against, without having to create it all by hand.

It talks directly to Nexudus over the internet; you don't need any AI tool running to use it.

## Before you start

**Work on your own branch, not the shared project.** This keeps your changes separate so you can't accidentally break the shared version everyone else uses. If you're not familiar with this, just copy and run:

```bash
git checkout -b my-name-test-run
```

(Replace `my-name-test-run` with anything you like — e.g. `sarah-qa-run`.) You only need to do this once per copy of the project you're working in.

## Setting up (one-time)

1. **Make sure Python is installed** (version 3.9 or newer). In your terminal, check with:

   ```bash
   python3 --version
   ```

   If you see something like `Python 3.11.4`, you're set — skip to step 2.

   If instead you get an error like "command not found":
   - **Mac**: download and install Python from [python.org/downloads](https://www.python.org/downloads/) (or, if you use Homebrew, `brew install python3`).
   - **Windows**: download and install Python from [python.org/downloads](https://www.python.org/downloads/) — make sure to tick **"Add Python to PATH"** during setup.

   Close and reopen your terminal afterwards, then run `python3 --version` again to confirm it worked.

2. **Install the required packages.** In your terminal, from the project folder:

   ```bash
   python3 -m pip install -r requirements.txt
   ```

   This installs the small set of tools the generator needs to run. (Using `python3 -m pip` instead of just `pip` avoids a common "command not found" error on some setups.)

3. **Log in to Nexudus.**

   ```bash
   python3 nexudus_auth.py setup
   ```

   It'll ask for your Nexudus email and password, right there in your terminal. Your password is never saved or sent anywhere except to Nexudus itself, and isn't shown on screen as you type it. What *does* get saved (in a file called `.env`, on your own computer only) is a login token — similar to staying logged into a website — so you won't have to log in again for a while.

   Your Nexudus account needs to be an **admin account with API access turned on**. If it isn't, this step will tell you clearly instead of letting things fail partway through later.

## Running it

The easiest way is the interactive wizard — it walks you through everything:

```bash
python3 wizard.py
```

It will:

1. Confirm you're logged in (or prompt you to log in, if you skipped the step above).
2. Ask how much data you want — e.g. how many customers, bookings, etc. You can just press Enter to accept the suggested amount for each one.
3. Ask whether to do a **practice run** (nothing actually created, just shows you what *would* happen) or a **real run** (creates real records in Nexudus). Real runs ask you to type "yes" to confirm before doing anything, since this affects a live account.
4. If your login has access to more than one business/location, ask which one this run should use — this only comes up if there's actually a choice to make.
5. Generate the data and show you progress as it goes.

At the end, it prints a summary — how many records were created, how many already existed and were skipped, and how many failed — plus a full account report (also saved to `last-run-report.txt` in the project folder) so you always have a record of exactly what's in the account.

**Running it again is safe.** If you run it a second time, it checks what's already there and only adds what's missing — it won't create duplicates.

## Undoing it

If you want to remove everything this tool created:

```bash
python3 teardown.py --dry-run    # Shows what would be deleted, without deleting anything
python3 teardown.py              # Actually deletes it
```

This only ever deletes records it created and tracked itself — never anything else in the account, and never by guessing based on names.

## Getting more control (optional)

If you don't want to use the wizard, you can run the same steps yourself:

```bash
python3 prebuild.py              # Generate the test data (one-time, or re-run to change volumes)
bash scripts/seed_all.sh         # Push it all to Nexudus
bash scripts/daily.sh            # Add a few fresh records "for today" (handy to run daily)
bash scripts/verify.sh           # Check what's in the account against expected counts
```

You can also adjust how much data gets created, e.g.:

```bash
python3 prebuild.py --coworkers 10 --bookings 20
```

Run `python3 prebuild.py --help` to see every option.

If your login has access to more than one business/location, pass `--business-id <id>` to `seed_all.sh`, `seed_layer.sh`, or any `generators/0N_*.py` directly — otherwise it'll ask you to pick one rather than guessing.

Every command above also supports `--dry-run` if you want to preview without creating anything.

---

## For developers / AI agents

The sections below are more technical — useful if you're extending this project rather than just running it.

### Why a direct API client instead of an agent/LLM loop

This project creates ~1,700+ individual records. Routing each one through an LLM tool call would be slow and burn a lot of tokens on mechanical data entry that doesn't need judgment once the data's already generated. So the actual execution path is:

```text
nexudus_auth.py    → one-time interactive login, silent token refresh after
nexudus_client.py  → thin requests wrapper over https://spaces.nexudus.com/api/...
pipeline.py         → chains generator layers 0-7 together, in-process
generators/*.py     → the actual per-entity creation logic (pure Python, no I/O
                       beyond the client calls above)
```

An agent with the Nexudus MCP connector was used *during development* to explore the schema and figure out field names/gotchas (see `reference/`) — a one-time research cost, not part of every run.

**Two-step data flow**, unrelated to the above:

1. `prebuild.py` generates deterministic profiles (names, emails, scenario assignments) into `data/*.json` files (gitignored — each user generates their own) — run once, or again with `--seed`/volume flags to regenerate.
2. Generators read those files and push to the API, resolving day/month offsets against `config.TODAY` at run time — this is what makes the whole dataset a rolling window instead of fixed dates.

**One exception:** `06_financial.py` has no `data/*.json` file. Invoice IDs and amounts don't exist until Nexudus generates them server-side, so it discovers them live via the API instead of reading a pre-planned file.

### Credential handling

- `.env` (gitignored, chmod 600) holds only OAuth tokens — never your password. `nexudus_auth.py setup` is meant to be run by you, in your own terminal, so the password prompt is never observed by an agent.
- If you're running this through an agent for anything else in this repo: it will not read, print, or echo `.env` — every tool call is visible in your session transcript if you want to verify that.
- Want an even stronger boundary than "won't read it"? Point `NEXUDUS_ENV_PATH` (or just move the file) somewhere outside the repo entirely, e.g. `~/.config/nexudus-test-data/.env` — nothing currently assumes `.env` lives in the project root except the default path in `nexudus_auth.py`.

### Rolling date window

All dates are **relative to the run date** — the data spans 24 months back from today. No hardcoded dates. Re-running on a different day shifts the entire window.

### Project structure

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
| `data/*.json` | Pre-generated profiles (coworkers, contracts, bookings, etc.) — gitignored, generate your own via `prebuild.py` |

### Layers

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

### Configuring data volumes

The headline counts that matter for a demo — coworkers, visitors, bookings, check-ins, CRM opportunities, proposals, help desk messages, community threads, coworker tasks/time-passes/products — are configurable per run, via `wizard.py`'s prompts or flags directly on `prebuild.py`. See `config.CONFIGURABLE_VOLUME_KEYS` for the full list, and `reference/extending-the-model.md` for why the rest of the ~50 entity types (resources, teams, calendar events, ...) aren't included — they're hand-authored content, not just a number to scale.

### Test markers

Records are meant to look like real data — no `[TEST]` name prefixes. The only marker baked into a record is the coworker email (`test-NNN@seeddata.local`); everything else is identified purely by its tracked ID in `data/created-ids/`, which is what `teardown.py` uses.

### More detail

See `CLAUDE.md` for the full standing rules (idempotency conventions, API gotchas discovered while building this, entity-specific patterns) and `reference/extending-the-model.md` for how to safely add or extend a record type. `test-data-seeding-strategy.md` is a historical build-status log from the original design, not a live reference.
