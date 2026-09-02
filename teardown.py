"""
Teardown — delete every record this project has created.

Deletes strictly by tracked ID (data/created-ids/<generator>.json), never by
name pattern — see CLAUDE.md rule 6/7. Each created-ids file is scoped to a
*generator*, not a Nexudus entity type (e.g. structural.json holds tariffs,
products, resources, ... all mixed together, each tagged with an "entity"
field) — so this script pools every tracked record across all generator
files, groups by that "entity" field, and deletes in reverse dependency
order (children before parents) so FK constraints don't block deletion.

Entities with no delete support in the Nexudus API (confirmed via
nexudus_describe_entity while building the generators) are skipped, not
attempted: CancelledBooking snapshots aren't independently deletable and
are removed automatically when their originating Booking is deleted (which
already happened at seed time for the 40 "to cancel" bookings — see
04_activity.py); CoworkerBookingCreditUseHistory supports list/get/update
only, no delete.

CoworkerInvoice has no plain DELETE endpoint either, but — unlike the
above — it does support real deletion via a command, COWORKER_INVOICE_DELETE
(confirmed live, see CLAUDE.md rule 12: a genuine delete, 404 on a
follow-up GET, not a void). This is the one entity in ENTITY_DELETE_ORDER
deleted via nexudus_run_command instead of nexudus_delete (see
_delete_one() below). Only invoices 06_financial.py has explicitly tracked
against a coworker this tool created are ever touched — see
06_financial.py::_list_invoices for why that tracking exists at all (it
didn't, until this was added: COWORKER_BILL_RUN returns nothing usable, so
without this the majority of invoices this tool causes to exist were
invisible to teardown entirely, which meant anything they blocked — e.g. a
CoworkerProduct swept into one of them — could never be cleanly deleted).

Records that fail to delete (or belong to a no-delete entity) are kept in
the tracking file for a future retry; only confirmed deletions are cleared.

A run against a genuinely complete seed doesn't need any of this, but this
account is generated in stages (see pipeline.py's layer tiers) and a prior
run can easily be partial — a foundational failure, an independent-tier
layer failure, a manual interrupt, the ~50/day coworker limit (CLAUDE.md
rule 30), or just a stray malformed record. So each entity's whole batch
(see _delete_entity_batch) is isolated in its own try/except in the main
loop, one level below pipeline.py's own per-layer isolation — a failure
there is logged distinctly from an ordinary per-record failure and this
script moves on to the next entity rather than aborting the entire run.
Records pooled from data/created-ids/*.json that are missing their
"entity" tag (or aren't even a record dict — the same "stray file"
scenario one step further) are skipped with a warning rather than used as
a dict key, which used to be able to crash the whole run outright before a
single record was touched (see CLAUDE.md rule 48). Per-entity outcomes
(deleted/failed/skipped, plus failure reasons) are collected into
entity_outcomes and rendered by teardown_summary_lines() alongside the
existing aggregate Seen/Deleted/Skipped/Failed line, and persisting
whatever was actually deleted always runs — via a try/finally around the
main loop — even if some entity's batch aborted.

If last-run-report.txt shows signs the last generation run didn't fully
complete, a one-line heads-up prints before teardown starts — purely
informational, it can't change what gets deleted (still strictly by
tracked ID, per rule 6/7).

At the end of a live run, write_teardown_report() persists this run's
outcome to last-teardown-report.txt (+ a last-teardown.json sibling),
the mirror of what pipeline.run_up_to writes for a generation run. It's
a deliberately separate file from last-run-report.txt (see
report_lib.TEARDOWN_REPORT_PATH) — the web control panel reads the JSON
to hang a "last teardown" delta column off its entity table and render
its own status strip, and shows the text verbatim in the raw-report
viewer. Skipped for a dry run.

After a live teardown finishes, it also offers to delete data/*.json — the
pre-generated test data plan files prebuild.py writes and the generators
read from (coworkers.json, bookings.json, ...) — and, separately,
output/*.csv, the per-entity CSV exports. Teardown clearing the live
account and data/created-ids/ tracking never touches either on its own —
the plan files are deliberately reusable across reseed cycles (see
README's two-step data flow) and the CSVs are a read-only snapshot rebuilt
on the next seed + export — so keeping both is the default; this only
offers to remove them for someone who wants a genuinely clean project
directory (see maybe_clear_generated_data / maybe_clear_csv_outputs).
Non-interactive callers can pass --clear-generated-data /
--clear-csv-outputs / --reset-counters to take each action without the
prompt (the web control panel does this, having collected the choices in
its teardown dialog up front); --yes skips --mode clean's typed
confirmation the same way.

It also offers to reset that business's
Billing.Current{Booking,CreditNote,Draft,Invoice}Number counters back to 0
(see maybe_reset_business_counters below) — deleting the tracked records
doesn't roll these back, since Nexudus just keeps auto-incrementing them on
every booking/invoice/draft/credit note this tool ever caused. Asked
interactively rather than done automatically because this account mixes
real, pre-existing data with seeded test data at the same business (see
CLAUDE.md rule 46) — resetting to 0 makes the next real invoice/booking
reuse a number a real historical record already has.

Two modes, chosen via --mode (or an interactive prompt if omitted):
  - "tracked" (default): the behavior described above — strictly by
    data/created-ids/*.json.
  - "clean": ignores tracking entirely and lists every entity in
    ENTITY_DELETE_ORDER live, deleting everything found. For when the
    tracking file is stale, missing, or you just want the account wiped
    regardless of what this project remembers creating — see
    _live_discover_all(). Confirmed live against this project's own
    account: a full "clean" run cleared ~2,100 of ~3,000 live records
    (structural/reference layers, contracts, coworkers 100%); the
    remainder falls into two documented, deterministic API limits (see
    PRICE_PLAN_LOCKED_USE_COMMAND and CLAUDE.md rule 32's stuck-guest
    bug), not this script's own failures.

Usage:
    python teardown.py                       # Live mode — deletes for real
    python teardown.py --mode clean           # Live mode, ignoring tracking
    python teardown.py --mode clean --yes     # ...skipping the typed prompt
    python teardown.py --dry-run              # Log what would be deleted
    python teardown.py --dry-run --mode clean # Preview a full live wipe
    python teardown.py --clear-generated-data --clear-csv-outputs --reset-counters
                                               # take the post-teardown
                                               # cleanups without prompting
    python teardown.py --business-id 12345   # pick which business's
                                               # counters to offer resetting,
                                               # for logins with access to
                                               # more than one (see
                                               # pipeline.py::_select_business)
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import CREATED_IDS_DIR, DATA_DIR, OUTPUT_DIR

# Reverse of the creation dependency order (§3) — children before parents.
ENTITY_DELETE_ORDER = [
    # Layer 5
    "coworkerdatafiles",
    "proposals",
    "crmopportunityhistories",
    "crmopportunities",
    "coworkerledgerentries",
    "coworkerinvoicehistories",
    # CoworkerInvoice — deleted here, before anything it might have swept
    # up as a line item (coworkerproducts, coworkertimepasses,
    # coworkerextraservices, bookings via CHARGE_BOOKING). Its own
    # children (ledger entries, invoice histories) are already gone above.
    "coworkerinvoices",
    # Layer 4b
    "coworkertasks",
    "blogposts",
    "communitymessages",
    "communitythreads",
    "helpdeskmessages",
    "eventattendees",
    "eventproducts",
    "calendarevents",
    "coworkerdeliveries",
    # Layer 4a
    "coworkerproducts",
    # CheckIn before CoworkerTimePass — a CheckIn can reference a
    # CoworkerTimePass via CoworkerTimePassGuid (see
    # 04_activity.py::_grant_day_pass), so the pass needs to outlive its
    # check-ins, not the other way around.
    "checkins",
    "coworkertimepasses",
    "coworkerbookingcredits",
    "coworkerextraservices",
    "bookingvisitors",
    "bookings",
    # Layer 3
    "coworkerinventoryassets",
    "contractdeposits",
    "contractpausedperiods",
    "contractproducts",
    "coworkercontracts",
    # Layer 2
    "visitors",
    "coworkers",
    # Layer 1
    "discountcodes",
    "crmboardcolumns",
    "crmboards",
    "inventoryassets",
    "floorplandesks",
    "floorplans",
    "resources",
    # TariffTimePass/TariffExtraService are join tables (Tariff<->TimePass,
    # Tariff<->ExtraService) — confirmed live they block deletion of both
    # sides ("You must delete all price plan time passes...",
    # "...resource credits...") if left until later. Neither was in this
    # list at all before, so they only ever got processed last
    # (alphabetically, after everything explicit) — too late to help.
    "tarifftimepasses",
    "tariffextraservices",
    "timepasses",
    "extraservices",
    "products",
    "tariffs",
    "teams",
    "helpdeskdepartments",
    "communitygroups",
    "calendareventcategories",
    # Layer 0
    "resourcetypes",
    "financialaccounts",
    "taxrates",
]

# No delete operation in the Nexudus API for these — see module docstring.
NO_DELETE_SUPPORT = {"cancelledbookings", "coworkerbookingcreditusehistories"}

# A few entities need a command instead of (or before) a plain DELETE.
# Each value is a list of steps run in order; a step is either "DELETE"
# (a plain nexudus_delete) or a (command_key, parameters) tuple run via
# nexudus_run_command.
#
# coworkerinvoices/coworkers/coworkercontracts reject a plain DELETE
# outright (405 Method Not Allowed — not a dependency block, the HTTP
# verb itself isn't supported), found by capturing the real admin UI's
# network request (same technique as PROPOSAL_SEND/PROPOSAL_ACCEPT and
# COWORKER_INVOICE_CANCEL/REFUND — see CLAUDE.md rule 27).
# coworkercontracts needs two commands in sequence (cancel, then delete).
#
# bookings is different: a plain DELETE is rejected with "You must delete
# all booking visitors using this record before you can delete it" — and
# that's not a rare edge case here despite how rule 32 originally reads.
# With only 56 tracked visitors reused as guests across 126+ bookings,
# most visitors end up on more than one booking, so the "shared guest
# leaves a BookingVisitor link stuck" pattern rule 32 already documented
# hits a large fraction of bookings, not a couple of outliers — confirmed
# live via a real teardown run showing dozens of consecutive
# "must delete all booking visitors" failures. Rule 32 also already
# confirmed CANCEL_BOOKING cascades-removes a booking's guests correctly
# on its own, so bookings now cancels first (tolerating "already
# cancelled" from the ~25 seeded pre-cancelled ones) and only then
# deletes. Any bookingvisitors left stuck by the standalone
# bookingvisitors step above will already be gone by the time this runs
# (cascaded away) and just 404 harmlessly on a later teardown pass (see
# rule 40) — no separate fix needed there.
COMMAND_DELETE = {
    "coworkerinvoices": [("COWORKER_INVOICE_DELETE", None)],
    "coworkers": [("COWORKER_DELETE", None)],
    "coworkercontracts": [("CANCEL_CONTRACT", None), ("DELETE_CONTRACT", None)],
    "bookings": [
        ("CANCEL_BOOKING", [
            {"Name": "Cancellation Reason", "Value": 7},  # Other — see CANCELLATION_REASON_MAP, 04_activity.py
            {"Name": "Cancel without applying cancellation fee rules", "Value": True},
        ]),
        "DELETE",
    ],
}

# coworkerinvoicehistories' list endpoint 500s unconditionally — confirmed
# live, even filtered per-coworker ("Ooops! There was a problem..."). No
# working way to enumerate it standalone; it's a child of coworkerinvoices
# and COWORKER_INVOICE_DELETE (a genuine delete, not void — rule 12) takes
# its history rows with it. Only relevant to --mode clean's live discovery
# — tracked mode never needs to list this entity, it already has real Ids.
NO_LIST_SUPPORT = {"coworkerinvoicehistories"}

# coworkerextraservices' list endpoint rejects an unfiltered call outright
# (400: requires Id / an updatedon range / BookingUniqueId / Coworker) —
# confirmed live. Listed per-coworker instead. Same scope as NO_LIST_SUPPORT
# above: only matters for --mode clean.
PER_COWORKER_LIST_FILTER = {"coworkerextraservices": "CoworkerExtraService_Coworker"}


def load_tracked_records():
    """Return {file_path: [record, ...]} for every data/created-ids/*.json file."""
    files = {}
    if not CREATED_IDS_DIR.exists():
        return files
    for path in sorted(CREATED_IDS_DIR.glob("*.json")):
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            records = []
        if records:
            files[path] = records
    return files


def _live_discover_all(nexudus_list):
    """--mode clean's replacement for load_tracked_records() + the
    by_entity-grouping block in run_teardown(): instead of trusting
    data/created-ids/*.json, lists every entity in ENTITY_DELETE_ORDER
    live and builds (by_entity, pooled) directly from what's actually on
    the account right now. Confirmed live against this project's account
    (2026-08-25): every entity here returned real records except the two
    handled specially (NO_LIST_SUPPORT skipped entirely,
    PER_COWORKER_LIST_FILTER used for coworkerextraservices).

    Records get a synthetic "entity" tag the same way a tracked record
    already has one, so the rest of the pipeline (grouping, per-record
    delete loop, summary) doesn't need to know which mode produced them.
    There's no backing file, so each pooled item's "path" is None —
    run_teardown's survivor-persistence step is skipped for this mode
    (see its own docstring)."""
    by_entity = {}
    pooled = []
    coworker_ids = None
    for entity in ENTITY_DELETE_ORDER:
        if entity in NO_LIST_SUPPORT:
            continue
        filter_key = PER_COWORKER_LIST_FILTER.get(entity)
        if filter_key is None:
            records = nexudus_list(entity, {})
        else:
            if coworker_ids is None:
                coworker_ids = [c["Id"] for c in nexudus_list("coworkers", {})]
            records, seen_ids = [], set()
            for cid in coworker_ids:
                for rec in nexudus_list(entity, {filter_key: cid}):
                    if rec.get("Id") not in seen_ids:
                        seen_ids.add(rec.get("Id"))
                        records.append(rec)
        for rec in records:
            record = {"entity": entity, **rec}
            item = (None, len(pooled), record)
            pooled.append(item)
            by_entity.setdefault(entity, []).append(item)
    return by_entity, pooled


_ALREADY_CHARGED_TEXT = "already been charged to this customer"

# A CoworkerTimePass/CoworkerExtraService granted automatically by a
# contract's price plan (rather than sold as a standalone item) rejects
# DELETE outright — confirmed live, found via the entity's own /commands
# endpoint since discovery (rule 27) didn't surface it either way. The
# API's own error text offers "mark it as used" as the only alternative,
# not a stepping stone back to deletion — there is no known way to
# actually delete these. USE_TIME_PASS/USE_EXTRA_SERVICE are run as the
# best-available terminal action and tracked as their own outcome
# (marked_used in _entity_bucket), never reported as "deleted".
_PRICE_PLAN_LOCKED_TEXT = "is from a price plan and it cannot be deleted"
PRICE_PLAN_LOCKED_USE_COMMAND = {
    "coworkertimepasses": ("USE_TIME_PASS", None),
    "coworkerextraservices": ("USE_EXTRA_SERVICE", [
        {"Name": "How many minutes/uses would like to use of this extra service?", "Value": 1},
    ]),
}

# A CoworkerExtraService created via CHARGE_BOOKING (rule 12) rejects a
# direct DELETE with this message — confirmed live. UNCHARGE_BOOKING
# ("Revert Charges") is the entity's own documented inverse of
# CHARGE_BOOKING, found via bookings' /commands endpoint, and is what the
# error text itself is pointing at.
_REVERT_CHARGE_TEXT = "Revert the charges of that booking instead"


def _run_steps(entity, record_id, steps, nexudus_delete, nexudus_run_command):
    """Run a COMMAND_DELETE step list in order. An earlier step failing
    (e.g. cancelling something already cancelled by a prior partial
    teardown) shouldn't block a later step that might still succeed on
    its own — only raise if the LAST step fails."""
    last_error = None
    for step in steps:
        try:
            if step == "DELETE":
                nexudus_delete(entity, record_id)
            else:
                command, parameters = step
                nexudus_run_command(entity, command, [record_id], parameters=parameters)
            last_error = None
        except Exception as e:  # noqa: BLE001
            last_error = e
    if last_error is not None:
        raise last_error


def _cleanup_related(parent_id, nexudus_delete, nexudus_run_command, nexudus_list, targets):
    """For each (entity, filter_key, command) in targets, look up live
    records referencing parent_id and delete them (via command if given,
    else a plain delete). Returns True if anything was found — whether
    or not each individual delete actually succeeded — so the caller
    knows whether a retry is even worth attempting."""
    found_any = False
    for entity, filter_key, command in targets:
        for record in nexudus_list(entity, {filter_key: parent_id}):
            found_any = True
            try:
                if command:
                    nexudus_run_command(entity, command, [record["Id"]])
                else:
                    nexudus_delete(entity, record["Id"])
            except Exception:  # noqa: BLE001 — best-effort; the retry after surfaces any real problem
                pass
    return found_any


# Entities whose per-record idempotency is purely local-tracking-based
# (already_created() against data/created-ids/*.json) can silently drift
# from live reality across repeated reseed cycles without an intervening
# successful teardown — confirmed live: an untracked, paid CoworkerInvoice
# and 32 untracked CoworkerProducts were both found live for the exact
# same coworker, both from the same ~16:45 reseed batch, both silently
# blocking COWORKER_DELETE with no indication which was the cause. Unlike
# bookings (a specific "already charged" message to key off), COWORKER_
# DELETE's failure is always the same generic 500 with no distinguishing
# text — so this checks known accumulation points proactively instead of
# reacting to a message. See rule 44.
_COWORKER_CLEANUP_TARGETS = [
    ("coworkerinvoices", "CoworkerInvoice_Coworker", "COWORKER_INVOICE_DELETE"),
    ("coworkerproducts", "CoworkerProduct_Coworker", None),
]


def _delete_one(entity, record_id, nexudus_delete, nexudus_run_command, nexudus_list=None):
    """Most entities use a plain DELETE; a few (see COMMAND_DELETE) need
    one or more steps first — a run-command, a plain DELETE, or both in
    sequence (e.g. cancel via command, then delete)."""
    steps = COMMAND_DELETE.get(entity)
    if steps is None:
        nexudus_delete(entity, record_id)
        return

    try:
        _run_steps(entity, record_id, steps, nexudus_delete, nexudus_run_command)
        return
    except Exception as e:  # noqa: BLE001
        if entity == "bookings" and _ALREADY_CHARGED_TEXT in str(e):
            # A booking that's already been charged (CHARGE_BOOKING
            # created a linked CoworkerExtraService) rejects DELETE even
            # after cancelling — confirmed live. UNCHARGE_BOOKING ("Revert
            # Charges") is the documented inverse of CHARGE_BOOKING and is
            # what the API itself points to when you try to delete the
            # charge directly instead (see _REVERT_CHARGE_TEXT) — more
            # reliable than the previous approach of hunting down and
            # directly deleting the linked CoworkerExtraService, which can
            # hit that same rejection.
            if nexudus_run_command is None:
                raise
            nexudus_run_command("bookings", "UNCHARGE_BOOKING", [record_id])
            _run_steps(entity, record_id, steps, nexudus_delete, nexudus_run_command)
            return

        if entity == "coworkers" and nexudus_list is not None:
            if _cleanup_related(record_id, nexudus_delete, nexudus_run_command,
                                 nexudus_list, _COWORKER_CLEANUP_TARGETS):
                _run_steps(entity, record_id, steps, nexudus_delete, nexudus_run_command)
                return

        raise


def _discover_untracked_coworker_invoices(files, by_entity, pooled, nexudus_list):
    """Pre-flight sync, run once before the main delete loop: financial.json
    only ever gets populated when 06_financial.py's own discovery step runs
    (rule 38b), but Nexudus's automated recurring-contract billing keeps
    generating fresh invoices for every active seeded contract in between
    generation runs, and nothing re-syncs the tracking file after the fact.
    Confirmed live: 101 untracked invoices for this project's own seeded
    coworkers found in a single comparison — same accumulation pattern as
    rule 44, but for invoices that were never tracked at all, so
    _COWORKER_CLEANUP_TARGETS' reactive-only cleanup (which only fires once
    COWORKER_DELETE itself has already failed) was the only thing standing
    between them and a permanently-stuck teardown. Matches
    06_financial.py::_list_invoices' own "only ever touch our own tracked
    records" filter — never by bare live-scope alone, only coworkers this
    project already has some tracked record for."""
    known_coworker_ids = {
        r.get("CoworkerId") for _, _, r in pooled
        if isinstance(r, dict) and r.get("CoworkerId") is not None
    }
    if not known_coworker_ids:
        return

    tracked_ids = {r.get("Id") for _, _, r in by_entity.get("coworkerinvoices", [])}
    live_invoices = nexudus_list("coworkerinvoices", {})
    financial_path = CREATED_IDS_DIR / "financial.json"
    added = 0
    for inv in live_invoices:
        if inv.get("CoworkerId") not in known_coworker_ids or inv.get("Id") in tracked_ids:
            continue
        record = {"entity": "coworkerinvoices", **inv, "DiscoveredInvoiceId": str(inv["Id"])}
        records = files.setdefault(financial_path, [])
        records.append(record)
        item = (financial_path, len(records) - 1, record)
        pooled.append(item)
        by_entity.setdefault("coworkerinvoices", []).append(item)
        added += 1
    if added:
        print(f"Pre-flight: found {added} untracked coworkerinvoices for known coworkers "
              f"— added to this run.\n")


def _entity_bucket(entity_outcomes, entity):
    return entity_outcomes.setdefault(entity, {
        "seen": 0, "deleted": 0, "skipped_no_support": 0, "skipped_no_id": 0,
        "marked_used": 0, "failed": 0, "failure_reasons": Counter(), "entity_aborted": None,
    })


def _delete_entity_batch(entity, items, dry_run, nexudus_delete, nexudus_run_command,
                          nexudus_list, bucket, deleted_keys):
    """One entity's whole batch: the coworkerinvoices credit-note-first
    sort, the NO_DELETE_SUPPORT skip, and the per-record delete loop.
    bucket and deleted_keys are mutated in place rather than returned, so
    whatever this function accomplishes before a failure (currently:
    nothing here is known to raise past the per-record try below, but see
    run_teardown's per-entity wrap) is preserved even if it doesn't return
    normally."""
    if entity == "coworkerinvoices":
        # A credit note (COWORKER_INVOICE_CANCEL's output, see rule 12) is
        # itself a separate CoworkerInvoice linked back to the one it
        # credited via OriginalInvoiceGuid/OriginalInvoiceId — a genuine
        # child-before-parent dependency within this one entity type,
        # confirmed by the user. Sort credit notes (tracked with an
        # OriginalInvoiceId) to the front of this batch so they're deleted
        # before the invoice they reference.
        items = sorted(items, key=lambda item: item[2].get("OriginalInvoiceId") is None)

    if entity in NO_DELETE_SUPPORT:
        print(f"--- {entity} ({len(items)}) --- SKIPPED (no delete support in API)")
        bucket["skipped_no_support"] += len(items)
        return

    print(f"--- {entity} ({len(items)}) ---")
    for path, i, record in items:
        record_id = record.get("Id")
        if record_id is None or (isinstance(record_id, str) and record_id.startswith("DRY-")):
            bucket["skipped_no_id"] += 1
            continue

        if dry_run:
            print(f"  WOULD DELETE {entity} {record_id}")
            continue

        try:
            _delete_one(entity, record_id, nexudus_delete, nexudus_run_command, nexudus_list)
            deleted_keys.add((path, i))
            bucket["deleted"] += 1
        except Exception as e:  # noqa: BLE001 — log and continue with this entity's remaining records
            # A 404 means the record is already gone — some other path
            # (a cascade delete, a manual cleanup, an earlier partial
            # teardown) already got it. That's the end state teardown
            # wants anyway, so treat it as deleted rather than a
            # failure — otherwise a stale tracked ID sits in the file
            # forever, re-reported as "failed" on every future run.
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 404:
                print(f"  {entity} {record_id} already gone (404) — treating as deleted")
                deleted_keys.add((path, i))
                bucket["deleted"] += 1
            elif entity in PRICE_PLAN_LOCKED_USE_COMMAND and _PRICE_PLAN_LOCKED_TEXT in str(e):
                # No delete path exists for this record at all (see
                # PRICE_PLAN_LOCKED_USE_COMMAND) — mark it used instead,
                # the best available terminal action, and stop tracking
                # it (nothing left to retry on a future run).
                command, parameters = PRICE_PLAN_LOCKED_USE_COMMAND[entity]
                try:
                    nexudus_run_command(entity, command, [record_id], parameters=parameters)
                    print(f"  {entity} {record_id}: price-plan-locked — marked used instead of deleted")
                    deleted_keys.add((path, i))
                    bucket["marked_used"] += 1
                except Exception as e2:  # noqa: BLE001
                    print(f"  FAILED to mark {entity} {record_id} as used: {e2}")
                    bucket["failed"] += 1
                    bucket["failure_reasons"]["price_plan_locked_mark_used_failed"] += 1
            elif entity == "coworkerextraservices" and _REVERT_CHARGE_TEXT in str(e) \
                    and record.get("BookingId") is not None:
                # Same underlying rejection as _ALREADY_CHARGED_TEXT on
                # bookings (see _delete_one), just hit from this side —
                # revert the booking's charge via UNCHARGE_BOOKING, then
                # retry this delete once.
                try:
                    nexudus_run_command("bookings", "UNCHARGE_BOOKING", [record["BookingId"]])
                    nexudus_delete(entity, record_id)
                    deleted_keys.add((path, i))
                    bucket["deleted"] += 1
                    print(f"  {entity} {record_id}: reverted booking {record['BookingId']}'s "
                          f"charge, then deleted")
                except Exception as e2:  # noqa: BLE001
                    print(f"  FAILED to delete {entity} {record_id} even after UNCHARGE_BOOKING: {e2}")
                    bucket["failed"] += 1
                    bucket["failure_reasons"]["revert_charge_failed"] += 1
            else:
                print(f"  FAILED to delete {entity} {record_id}: {e}")
                bucket["failed"] += 1
                bucket["failure_reasons"][f"http_{status}" if status else type(e).__name__] += 1


_INCOMPLETE_RUN_MARKERS = (
    "Layers that failed entirely this run",
    "<-- short",
)


def _last_generation_run_incomplete():
    """True if last-run-report.txt (report_lib.py::write_report's output)
    shows signs the most recent generation run didn't fully complete —
    checked via report_lib.py's own literal marker strings rather than
    re-deriving its data, since teardown runs as a separate process with
    no access to pipeline.py's in-memory LAST_RUN_* globals. Purely
    informational: teardown only ever deletes by tracked ID (see module
    docstring), so this can't change what it does, only what it prints
    before starting.

    Deliberately does NOT check report_lines()'s "<-- below target" flag —
    that's the cumulative, lifetime view (report_lib.py::report_lines) and
    can be true for reasons that have nothing to do with the last run's
    completeness (a volume config bumped up since, a multi-day seeding
    plan not finished by design, ...). Only run_reconciliation_lines()'s
    "<-- short" (this run specifically fell short of its own target) and
    an outright layer failure are genuinely run-specific signals."""
    from report_lib import REPORT_PATH
    if not REPORT_PATH.exists():
        return False
    try:
        text = REPORT_PATH.read_text(encoding="utf-8")
    except OSError:
        return False
    return any(marker in text for marker in _INCOMPLETE_RUN_MARKERS)


def teardown_summary_lines(entity_outcomes):
    """Per-entity Seen/Deleted/NoSupport/Failed breakdown, plus failure
    reasons and entity-level abort notices — mirrors report_lib.py's
    run_reconciliation_lines()'s shape and its "only show a detail line
    for entities that actually have something to report" convention."""
    if not entity_outcomes:
        return []

    lines = [f"{'Entity':<32} {'Seen':>6} {'Deleted':>8} {'MarkedUsed':>10} "
              f"{'NoSupport':>10} {'Failed':>8}", "-" * 78]
    aborted = []
    for entity in sorted(entity_outcomes):
        b = entity_outcomes[entity]
        lines.append(f"{entity:<32} {b['seen']:>6} {b['deleted']:>8} {b.get('marked_used', 0):>10} "
                      f"{b['skipped_no_support']:>10} {b['failed']:>8}")
        if b["failure_reasons"]:
            reasons = ", ".join(f"{r}={n}" for r, n in b["failure_reasons"].most_common())
            lines.append(f"    reasons: {reasons}")
        if b["entity_aborted"]:
            lines.append(f"    BATCH ABORTED: {b['entity_aborted']}")
            aborted.append(entity)

    if aborted:
        lines.append("")
        lines.append(f"Entities whose batch aborted partway through ({len(aborted)}): "
                      + ", ".join(aborted))
    return lines


def write_teardown_report(summary, mode):
    """Persist this teardown run to last-teardown-report.txt (+ a .json
    sibling for the web control panel), mirroring
    report_lib.write_report / write_run_json for generation runs.

    A separate file from last-run-report.txt by design — see
    report_lib.TEARDOWN_REPORT_PATH. The text half is the same aggregate
    line and per-entity breakdown printed to the console
    (teardown_summary_lines); the JSON half is what webui/report.py joins
    onto the entity table (a "last teardown" delta column) and renders as
    its own status strip, the mirror of write_run_json's role for a seed.

    Never called for a dry run — nothing was deleted, and it must not
    masquerade as the last real teardown in the panel."""
    from report_lib import TEARDOWN_REPORT_PATH

    generated_at = datetime.now(timezone.utc).isoformat()
    entity_outcomes = summary.get("entity_outcomes") or {}

    agg = (f"Seen: {summary['seen']}  Deleted: {summary['deleted']}  "
           f"Marked used: {summary['marked_used']}  "
           f"Skipped (unsupported): {summary['skipped_no_support']}  "
           f"Failed: {summary['failed']}")
    if summary.get("malformed"):
        agg += f"  Malformed (no entity tag): {summary['malformed']}"

    lines = [f"Teardown report generated {generated_at}", f"Mode: {mode}", "", agg, ""]
    lines += teardown_summary_lines(entity_outcomes) or ["(no entities processed this run)"]

    path = Path(TEARDOWN_REPORT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    payload = {
        "generated_at": generated_at,
        "mode": mode,
        "totals": {
            "seen": summary["seen"], "deleted": summary["deleted"],
            "marked_used": summary["marked_used"],
            "skipped_no_support": summary["skipped_no_support"],
            "failed": summary["failed"], "malformed": summary.get("malformed", 0),
        },
        "entities": {
            entity: {
                "seen": b["seen"], "deleted": b["deleted"],
                "marked_used": b.get("marked_used", 0),
                "skipped_no_support": b["skipped_no_support"],
                "failed": b["failed"],
                # a Counter isn't JSON-serialisable as-is (same as write_run_json)
                "failure_reasons": dict(b["failure_reasons"]),
                "aborted": b["entity_aborted"],
            }
            for entity, b in entity_outcomes.items()
        },
    }
    path.with_suffix(".json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"\n(teardown report saved to {path})")


def run_teardown(nexudus_delete, dry_run, nexudus_run_command=None, nexudus_list=None, mode="tracked"):
    empty_summary = {"seen": 0, "deleted": 0, "marked_used": 0, "skipped_no_support": 0,
                      "failed": 0, "malformed": 0, "entity_outcomes": {}}

    files = {}
    malformed_count = 0

    if mode == "clean":
        # Ignore data/created-ids/*.json entirely — list every entity live
        # and delete whatever's there, regardless of what this project
        # remembers creating. See _live_discover_all()'s docstring.
        if nexudus_list is None:
            raise ValueError("mode='clean' requires nexudus_list to discover live records")
        by_entity, pooled = _live_discover_all(nexudus_list)
        if not pooled:
            print("No live records found on this account — nothing to tear down.")
            if not dry_run:
                write_teardown_report(empty_summary, mode)
            return empty_summary
    else:
        files = load_tracked_records()
        if not files:
            print("No tracked records found in data/created-ids/ — nothing to tear down.")
            if not dry_run:
                write_teardown_report(empty_summary, mode)
            return empty_summary

        if _last_generation_run_incomplete():
            print("Heads up: the last generation run (see last-run-report.txt) shows signs it "
                  "didn't fully complete — some records you expect may be missing. This doesn't "
                  "change what teardown does (it only ever deletes by tracked ID), just flagging "
                  "it before starting.\n")

        # Pool every record across files, remembering which file+index it came from
        # so we can rewrite each file afterward with only the survivors.
        pooled = []  # (file_path, index_in_file, record)
        for path, records in files.items():
            for i, r in enumerate(records):
                pooled.append((path, i, r))

        # A record missing its "entity" tag (or, one step further, a stray
        # file whose JSON isn't even a list of record dicts) isn't a real
        # entity — the same "stray file in data/created-ids/" scenario
        # report_lib.py::_grouped_records() already guards against on the
        # reporting side, with its own malformed_count. Skipping these here
        # matters more than just symmetry: an unguarded None key used to flow
        # straight into `sorted(set(by_entity) - set(ENTITY_DELETE_ORDER))`
        # below, which raises TypeError the instant another, genuinely-
        # unlisted entity name was *also* present that run — crashing the
        # entire teardown before a single record was touched.
        by_entity = {}
        for item in pooled:
            record = item[2]
            entity = record.get("entity") if isinstance(record, dict) else None
            if entity is None:
                malformed_count += 1
                continue
            by_entity.setdefault(entity, []).append(item)

        if malformed_count:
            print(f"WARNING: {malformed_count} tracked record(s) are missing an 'entity' tag — "
                  f"skipped (left in tracking, not deleted). Check data/created-ids/ for a stray "
                  f"file that doesn't belong there.\n")

        if not dry_run and nexudus_list is not None:
            _discover_untracked_coworker_invoices(files, by_entity, pooled, nexudus_list)

    total_seen = len(pooled)
    deleted_keys = set()  # (path, index) confirmed gone
    entity_outcomes = {}

    ordered_entities = ENTITY_DELETE_ORDER + sorted(set(by_entity) - set(ENTITY_DELETE_ORDER))

    try:
        for entity in ordered_entities:
            items = by_entity.get(entity)
            if not items:
                continue

            bucket = _entity_bucket(entity_outcomes, entity)
            bucket["seen"] = len(items)

            try:
                _delete_entity_batch(entity, items, dry_run, nexudus_delete, nexudus_run_command,
                                      nexudus_list, bucket, deleted_keys)
            except Exception as e:  # noqa: BLE001 — this entity's whole batch aborted partway
                # through (something outside _delete_entity_batch's own
                # per-record try/except) — log distinctly from a
                # per-record failure and move to the next entity,
                # mirroring pipeline.py's per-layer isolation one level
                # down, at entity-type granularity instead of layer.
                attempted = bucket["deleted"] + bucket["failed"]
                if attempted:
                    print(f"\n!!! {entity} batch aborted after {attempted} of {bucket['seen']} "
                          f"records were already processed (preserved) — the rest stay tracked "
                          f"for a future retry: {e}")
                else:
                    print(f"\n!!! {entity} batch aborted before any of its {bucket['seen']} "
                          f"records were attempted — all stay tracked for a future retry: {e}")
                bucket["entity_aborted"] = str(e)
    finally:
        # However far the loop above got — even if something in its own
        # setup broke rather than inside one entity's processing — nothing
        # already deleted this run should ever go unpersisted, and the run
        # should always report what it accomplished rather than a bare
        # stack trace with no summary at all.
        total_deleted = sum(b["deleted"] for b in entity_outcomes.values())
        total_marked_used = sum(b.get("marked_used", 0) for b in entity_outcomes.values())
        total_skipped_no_support = sum(b["skipped_no_support"] for b in entity_outcomes.values())
        total_failed = sum(b["failed"] for b in entity_outcomes.values())

        print()
        print(f"Seen: {total_seen}  Deleted: {total_deleted}  Marked used: {total_marked_used}  "
              f"Skipped (unsupported): {total_skipped_no_support}  Failed: {total_failed}"
              + (f"  Malformed (no entity tag): {malformed_count}" if malformed_count else ""))

        summary_lines = teardown_summary_lines(entity_outcomes)
        if summary_lines:
            print()
            print("\n".join(summary_lines))

        # files is empty in mode="clean" (no tracking file backs a live-
        # discovered record — see _live_discover_all), so there's nothing
        # to persist; the account itself is the only state that changed.
        if not dry_run and deleted_keys and files:
            # Rewrite each file, keeping only records that weren't confirmed deleted.
            for path, records in files.items():
                survivors = [r for i, r in enumerate(records) if (path, i) not in deleted_keys]
                path.write_text(json.dumps(survivors, indent=2) + "\n", encoding="utf-8")
            print("\nUpdated data/created-ids/*.json to remove deleted records.")

    summary = {
        "seen": total_seen, "deleted": total_deleted, "marked_used": total_marked_used,
        "skipped_no_support": total_skipped_no_support, "failed": total_failed,
        "malformed": malformed_count, "entity_outcomes": entity_outcomes,
    }
    # Mirror pipeline.run_up_to writing last-run-report.txt at the end of a
    # generation run — a dry run has nothing real to report on, same as there.
    if not dry_run:
        write_teardown_report(summary, mode)
    return summary


def maybe_clear_generated_data(assume_yes=False):
    """Ask whether to also delete data/*.json — the pre-generated test data
    plan files (coworkers.json, bookings.json, ...) that prebuild.py writes
    and the generators read from. Teardown clearing the live account and
    data/created-ids/ tracking (above) never touches these — they're
    reusable across reseed cycles by design, so keeping them is the default;
    this only offers to remove them too for someone who wants the project
    directory itself back to a clean slate, not just the live account.

    The glob also picks up data/plan-manifest.json (prebuild's incremental
    seed/count record) — correct: once the plan files are gone, the manifest
    must go too so the next prebuild starts fresh rather than thinking the
    (now deleted) records are still there.

    assume_yes skips the prompt and deletes — for a non-interactive caller
    (the web control panel) that already collected the choice up front."""
    files = sorted(DATA_DIR.glob("*.json"))
    if not files:
        return

    print(f"\n--- Generated data files in {DATA_DIR} ---")
    print(f"  {len(files)} files (coworkers.json, bookings.json, ...)")

    if assume_yes:
        answer = "y"
    else:
        answer = input(
            "\nAlso delete these local generated data files? They're safe to keep — "
            "prebuild.py overwrites them next time you generate new data anyway (y/N): "
        ).strip().lower()
    if answer != "y":
        print("Keeping generated data files.")
        return

    for path in files:
        path.unlink()
    print(f"Deleted {len(files)} generated data files from {DATA_DIR}.")


def maybe_clear_csv_outputs(assume_yes=False):
    """Ask whether to also delete output/*.csv — the per-entity CSV exports
    (plus the copied run report) that report_lib.write_entity_csvs /
    refresh_output.py write after a live run. Teardown clearing the live
    account and local tracking never touches these; they're a read-only
    snapshot, rebuilt wholesale on the next seed + CSV export. Offered here
    only for someone who wants the project directory itself back to a clean
    slate.

    assume_yes skips the prompt and deletes — same rationale as
    maybe_clear_generated_data."""
    if not OUTPUT_DIR.exists():
        return
    files = sorted(OUTPUT_DIR.glob("*.csv"))
    if not files:
        return

    print(f"\n--- Exported CSVs in {OUTPUT_DIR} ---")
    print(f"  {len(files)} files")

    if assume_yes:
        answer = "y"
    else:
        answer = input(
            "\nAlso delete these exported CSV files? They're rebuilt on the next "
            "seed + CSV export (y/N): "
        ).strip().lower()
    if answer != "y":
        print("Keeping exported CSV files.")
        return

    for path in files:
        path.unlink()
    print(f"Deleted {len(files)} exported CSV files from {OUTPUT_DIR}.")


# The four auto-incrementing counters Nexudus bumps on every booking,
# invoice, draft, and credit note — never rolled back by deleting the
# records themselves, since they're settings on the business, not fields on
# those records.
COUNTER_SETTINGS_TO_RESET = [
    "Billing.CurrentBookingNumber",
    "Billing.CurrentCreditNoteNumber",
    "Billing.CurrentDraftNumber",
    "Billing.CurrentInvoiceNumber",
]


def _fetch_counter_settings(business_id, nexudus_list):
    """businesssettings' list endpoint ignores every filter param tried —
    BusinessId/Name directly and the Entity_Field convention used
    elsewhere in this codebase both no-op, confirmed live (every page
    comes back unfiltered regardless) — so this pulls the whole table and
    filters client-side instead."""
    all_settings = nexudus_list("businesssettings", {"size": 200})
    return [
        s for s in all_settings
        if s.get("BusinessId") == business_id and s.get("Name") in COUNTER_SETTINGS_TO_RESET
    ]


def maybe_reset_business_counters(business_id, business_name, nexudus_list, nexudus_update,
                                  assume_yes=False):
    """Ask whether to reset business_id's billing counters to 0, now that
    teardown has cleared every tracked record. Purely optional — see the
    module docstring for why this doesn't just happen automatically.

    assume_yes skips the prompt and resets — for a non-interactive caller
    (the web control panel) that already collected the choice up front."""
    settings = _fetch_counter_settings(business_id, nexudus_list)
    if not settings:
        print(f"\nNo billing counter settings found for {business_name} (id={business_id}).")
        return

    print(f"\n--- Billing counters for {business_name} (id={business_id}) ---")
    for s in settings:
        print(f"  {s['Name']}: {s['Value']}")

    if assume_yes:
        answer = "y"
    else:
        answer = input(
            "\nReset these to 0? This location may also have real invoices/bookings "
            "issued outside this tool — resetting reuses their numbers (y/N): "
        ).strip().lower()
    if answer != "y":
        print("Leaving counters as-is.")
        return

    for s in settings:
        nexudus_update("businesssettings", s["Id"], {"Value": "0"})
        print(f"  {s['Name']}: {s['Value']} -> 0")
    print("Counters reset.")


def _prompt_business_id(businesses):
    """Numbered-list prompt for picking a business — same pattern as
    wizard.py's collect_business_id. _select_business() deliberately
    fails loudly instead of guessing when --business-id is missing and
    there's more than one business (see rule 8) — appropriate for a
    script driven entirely by flags, but teardown is meant to be
    runnable standalone too, so ask interactively here instead of just
    letting that error end the run before the counter-reset offer."""
    print(f"\nThis login has access to {len(businesses)} businesses — "
          "which one's billing counters should be checked?\n")
    for i, b in enumerate(businesses, start=1):
        print(f"  {i}. {b.get('Name', '?')}")
    while True:
        raw = input(f"\nEnter a number (1-{len(businesses)}): ").strip()
        try:
            choice = int(raw)
        except ValueError:
            print("Please enter a number from the list above.")
            continue
        if not (1 <= choice <= len(businesses)):
            print(f"Please enter a number between 1 and {len(businesses)}.")
            continue
        return businesses[choice - 1]


def _prompt_mode():
    """Interactive prompt for --mode when it's omitted — same numbered-list
    pattern as _prompt_business_id. Defaults to "tracked" (the safer,
    narrower option) on EOFError, matching this script's other optional
    prompts' non-interactive fallback."""
    print("\nHow should teardown decide what to delete?\n")
    print("  1. Tracked only — strictly data/created-ids/*.json (default, safer)")
    print("  2. Clean account — ignore tracking, delete every live record found")
    while True:
        raw = input("\nEnter a number (1-2) [1]: ").strip()
        if raw == "":
            return "tracked"
        if raw == "1":
            return "tracked"
        if raw == "2":
            return "clean"
        print("Please enter 1 or 2.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mode", choices=["tracked", "clean"], default=None,
                         help="'tracked' deletes strictly by data/created-ids/*.json (default); "
                              "'clean' ignores tracking and deletes every live record found. "
                              "Prompted interactively if omitted.")
    parser.add_argument("--business-id", type=int, default=None,
                         help="Which business/location's billing counters to offer "
                              "resetting afterward, if this login has access to more than one")
    parser.add_argument("--yes", action="store_true",
                         help="Skip the interactive 'delete everything' confirmation for "
                              "--mode clean (the caller has already confirmed — e.g. the "
                              "web control panel, which gates it behind a typed phrase of "
                              "its own)")
    parser.add_argument("--clear-generated-data", action="store_true",
                         help="After teardown, delete data/*.json (prebuild's plan files) "
                              "without prompting")
    parser.add_argument("--clear-csv-outputs", action="store_true",
                         help="After teardown, delete output/*.csv (the exported per-entity "
                              "CSVs) without prompting")
    parser.add_argument("--reset-counters", action="store_true",
                         help="After teardown, reset the business's Billing.Current* "
                              "counters to 0 without prompting (uses --business-id)")
    args = parser.parse_args()

    if args.mode is not None:
        mode = args.mode
    else:
        try:
            mode = _prompt_mode()
        except EOFError:
            mode = "tracked"
            print("\nNo input available — defaulting to tracked-only mode.")

    if args.dry_run:
        if mode == "clean":
            # Even a dry run needs a real nexudus_list to discover what's
            # live — there's no tracked-file fallback in this mode (see
            # _live_discover_all). nexudus_delete/nexudus_run_command stay
            # unused either way; dry_run=True never calls them.
            import nexudus_client as client
            run_teardown(nexudus_delete=None, dry_run=True, mode=mode, nexudus_list=client.nexudus_list)
        else:
            run_teardown(nexudus_delete=None, dry_run=True, mode=mode)
    else:
        import nexudus_client as client
        from pipeline import _select_business, list_businesses

        if mode == "clean" and not args.yes:
            try:
                confirm = input(
                    "\n'clean' mode deletes every live record on this account, ignoring local "
                    "tracking entirely — including anything this project didn't create. Type "
                    "'delete everything' to proceed: "
                ).strip()
            except EOFError:
                confirm = ""
            if confirm != "delete everything":
                print("Aborted — no changes made.")
                sys.exit(0)

        try:
            run_teardown(nexudus_delete=client.nexudus_delete, dry_run=False,
                         nexudus_run_command=client.nexudus_run_command,
                         nexudus_list=client.nexudus_list, mode=mode)
        except Exception as e:  # noqa: BLE001 — every anticipated failure mode is already
            # handled inside run_teardown itself (per-record, per-entity);
            # this is the last-resort net for something genuinely
            # unforeseen, so the optional offers below still get a chance
            # to run instead of the whole process dying here.
            print(f"\n\nTeardown hit an unexpected error and stopped early: {e}\n"
                  f"Some tracked records may not have been deleted — re-run teardown.py "
                  f"to pick up where it left off.")

        try:
            maybe_clear_generated_data(assume_yes=args.clear_generated_data)
        except EOFError:
            # No stdin to read from (piped/non-interactive run) — skip this
            # optional offer cleanly, same as the counter-reset one below.
            print("\n\nNo input available — skipping the generated-data cleanup offer.")

        try:
            maybe_clear_csv_outputs(assume_yes=args.clear_csv_outputs)
        except EOFError:
            print("\n\nNo input available — skipping the exported-CSV cleanup offer.")

        try:
            businesses = list_businesses()
            if args.business_id is not None or len(businesses) <= 1:
                business = _select_business(businesses, args.business_id)
            else:
                business = _prompt_business_id(businesses)
            maybe_reset_business_counters(
                business["Id"], business.get("Name", "?"),
                client.nexudus_list, client.nexudus_update,
                assume_yes=args.reset_counters,
            )
        except EOFError:
            # No stdin to read from (piped/non-interactive run) — the
            # teardown itself already finished above; the counter-reset
            # offer is optional, so skip it cleanly instead of a traceback.
            print("\n\nNo input available — skipping the billing counter reset offer.")
