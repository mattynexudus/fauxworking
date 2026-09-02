# Fauxworking

This tool fills a Nexudus account with realistic-looking test data — customers, bookings, invoices, contracts, and more — so you have something real to test against, without having to create it all by hand.

It talks directly to Nexudus over the internet; you don't need any AI tool running to use it.

**The browser control panel is the main way to use this.** It handles login, lets you dial in how much data you want, runs the seed, keeps the export in sync, and tears everything down again — all from one local web page. The command line still does everything the panel does (and a bit more) if you prefer it — see [Command line](#command-line-alternative) further down — but you don't need it.

## A note for Windows users

The `./fauxworking` wrapper is a bash script, which plain PowerShell/Command Prompt can't run directly. You have two options:

- Use **Git Bash** (installed alongside [Git for Windows](https://git-scm.com/downloads/win)) or **WSL** — either lets you run `./fauxworking ...` exactly as written below.
- Or run the underlying Python directly. Everywhere this README says `./fauxworking ui`, you can instead run `python webui/server.py`; everywhere it says `python3`, type `python` (or `py` if that doesn't work). Windows installs Python as `python`, not `python3`.

Everything else — `git`, `pip install` — works the same on both.

## Quick start

Four steps and you're in the control panel. Everything after that happens in the browser.

### 1. Create your own branch

**Work on your own branch, not the shared project.** This keeps your changes separate so you can't accidentally break the shared version everyone else uses. Copy and run:

```bash
git checkout -b my-name-test-run
```

(Replace `my-name-test-run` with anything you like — e.g. `sarah-qa-run`.) You only need to do this once per copy of the project you're working in.

### 2. Make sure Python is installed

Version 3.9 or newer. Check with:

```bash
python3 --version
```

If you see something like `Python 3.11.4`, you're set — skip to step 3.

If instead you get an error like "command not found":

- **Mac**: download and install Python from [python.org/downloads](https://www.python.org/downloads/) (or, if you use Homebrew, `brew install python3`).
- **Windows**: download and install Python from [python.org/downloads](https://www.python.org/downloads/) — make sure to tick **"Add Python to PATH"** during setup.

Close and reopen your terminal afterwards, then run `python3 --version` again to confirm it worked.

### 3. Install the required packages

From the project folder:

```bash
python3 -m pip install -r requirements.txt
```

This installs the small set of tools the generator needs to run. (Using `python3 -m pip` instead of just `pip` avoids a common "command not found" error on some setups.)

### 4. Launch the control panel

```bash
./fauxworking ui
```

Then open **<http://127.0.0.1:8765>**. It's bound to your machine only, on purpose — the panel can trigger teardown and the process holds your Nexudus login tokens, so it's not meant to be reachable from anywhere else.

**Log in from the browser.** The panel opens with a login form if you're not signed in yet — enter your Nexudus email and password there. Your password is never saved or sent anywhere except to Nexudus itself. What *does* get saved (in a file called `.env`, on your own computer only) is a login token — similar to staying logged into a website — so you won't have to log in again for a while. The header shows whether you're signed in, and **Sign out** clears the token.

Your Nexudus account needs to be an **admin account with API access turned on**. If it isn't, the panel will tell you clearly instead of letting things fail partway through later.

## Using the control panel

Everything below happens on that one page — no terminal needed after the four steps above.

- **A "Data volumes" panel** — one persistent control for how much of each entity you want: *seeded* (what's live) and an editable *target* per row. Raising a target **adds** that many on the next run; it never rewrites or removes what's there. Over-large values and a changed seed warn you. You tune it here, not inside the run flow.
- **A guided "Set up demo data" card** — one screen: through-which-layer (a descriptive dropdown), export CSV, "start data fresh", a plain-English summary of the delta, the exact equivalent `python` command, then **Preview (dry run)** and — after a deliberate two-step confirm — **Run for real**. Amounts come from the Data volumes panel. It regenerates the plan only when a target actually grew (or you changed the seed / ticked "fresh"); when nothing changed it skips straight to seeding — so this one card covers both "regenerate + seed" and "just seed the current plan".
- **The individual commands below that**, grouped and ordered — Keep it fresh · Check (read-only) · Danger zone — each card tagged with what it touches (offline / read-only / writes live / deletes). Dry-run/live is one toggle per card, not two cards.
- **A location selector in the header** that every command which takes one is run against, so the target is never ambiguous. Live commands stay disabled until you pick one on a multi-location login.
- **Live output**, streamed as it happens, plus a durable log per run under `logs/` (gitignored) — a browser refresh or reopening the tab doesn't lose it.
- **One run at a time** — a second command while one is active is refused, not queued.
- **Safety by default.** Dry-run is pre-checked wherever it applies; a live write needs a second confirming click; teardown's live delete needs its exact confirmation phrase typed. Clean-mode teardown (ignore tracking, delete everything found live) demands a stronger phrase again (`delete everything`). A real teardown also offers, in the same dialog, to delete `data/*.json` plan files, delete `output/*.csv` exports, and reset this location's billing counters — all off by default.
- **Results after every run** — a "what's in the account now" table, the full `last-run-report.txt`, and `output/*.csv` download links.

**Running it again is safe.** If you run a seed a second time, it checks what's already there and only adds what's missing — it won't create duplicates.

### Keeping the export in sync

One thing keeps changing on its own after a run: Nexudus raises new invoices for active contracts over time, on its own schedule — independent of anything this tool does. If it's been a while since your last run, the `output/coworkerinvoices.csv` file can go stale. The **refresh** command (in the panel's "Keep it fresh" group) pulls in any invoices Nexudus generated on its own since your last run, re-exports every CSV in `output/`, and also produces `output/coworkerinvoicelines.csv` — a line-by-line breakdown of every invoice. It's read-only aside from noting the new invoices it finds — it never creates, changes, or deletes anything in the account.

### Undoing it

The **teardown** command (in the panel's "Danger zone" group) removes everything this tool created. It shows you what it's about to delete and asks you to type a confirmation phrase first. This only ever deletes records it created and tracked itself — never anything else in the account, and never by guessing based on names.

## Command line (alternative)

Everything the panel does is also available from the terminal. The `./fauxworking` wrapper is the front door:

```bash
./fauxworking            # Interactive wizard — guided setup + run
./fauxworking daily      # Add a few fresh records "for today" (handy to run daily)
./fauxworking verify     # Check what's in the account against expected counts
./fauxworking refresh    # Pull in invoices Nexudus raised on its own, re-export output/
./fauxworking teardown   # Remove everything this tool created (asks to confirm first)
./fauxworking ui         # Open the browser control panel (see above)
```

The wizard walks you through the same things the panel does: it confirms you're logged in (or prompts you to log in), asks how much data you want, asks whether to do a practice run or a real one, asks which business/location to use if your login has access to more than one, then generates and shows progress. At the end it prints a summary plus a full account report (also saved to `last-run-report.txt`).

If you haven't logged in through the browser panel yet, you can log in from the terminal instead:

```bash
python3 nexudus_auth.py setup
```

It asks for your Nexudus email and password right there in your terminal (the password isn't shown as you type, and is only ever sent to Nexudus). This writes the same `.env` token the panel uses — they share it, so you only need to log in one way.

If you don't want to use the wrapper at all, you can run the same steps yourself directly:

```bash
python3 prebuild.py              # Generate the test data (one-time, or re-run to change volumes)
bash scripts/seed_all.sh         # Push it all to Nexudus
bash scripts/daily.sh            # Add a few fresh records "for today" (handy to run daily)
bash scripts/verify.sh           # Check what's in the account against expected counts
python3 refresh_output.py        # Pull in invoices Nexudus raised on its own, re-export output/
python3 teardown.py --dry-run    # Shows what would be deleted, without deleting anything
python3 teardown.py              # Actually deletes it
```

You can also adjust how much data gets created, e.g.:

```bash
python3 prebuild.py --coworkers 10 --bookings 20
```

Run `python3 prebuild.py --help` to see every option.

If your login has access to more than one business/location, pass `--business-id <id>` to `seed_all.sh`, `seed_layer.sh`, `daily.sh`/`generators/daily_update.py`, or any `generators/0N_*.py` directly — otherwise it'll list the options and stop rather than guessing.

Every command above also supports `--dry-run` if you want to preview without creating anything.

`scripts/ui.sh --host 0.0.0.0` (or `$FAUXWORKING_UI_PORT`/`$FAUXWORKING_UI_HOST`) exists if you really want the control panel reachable from another machine on your network, but there's no login wall beyond the Nexudus one — only do that on a network you trust.

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
| `webui/` | The browser control panel — server, static assets, command registry |
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
| `refresh_output.py` | Discovers invoices Nexudus raised on its own since the last run, re-exports `output/*.csv`, and exports `output/coworkerinvoicelines.csv` |
| `scripts/` | Shell wrappers: seed_all, seed_layer, daily, teardown, verify, refresh, ui |
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

The headline counts that matter for a demo — coworkers, visitors, bookings, check-ins, CRM opportunities, proposals, help desk messages, community threads, coworker tasks/time-passes/products — are configurable per run, via the control panel's Data volumes panel, `wizard.py`'s prompts, or flags directly on `prebuild.py`. See `config.CONFIGURABLE_VOLUME_KEYS` for the full list, and `reference/extending-the-model.md` for why the rest of the ~50 entity types (resources, teams, calendar events, ...) aren't included — they're hand-authored content, not just a number to scale.

### Test markers

Records are meant to look like real data — no `[TEST]` name prefixes. The only marker baked into a record is the coworker email (`test-NNN@seeddata.local`); everything else is identified purely by its tracked ID in `data/created-ids/`, which is what `teardown.py` uses.

### More detail

See `CLAUDE.md` for the full standing rules (idempotency conventions, API gotchas discovered while building this, entity-specific patterns) and `reference/extending-the-model.md` for how to safely add or extend a record type. `test-data-seeding-strategy.md` is a historical build-status log from the original design, not a live reference.
