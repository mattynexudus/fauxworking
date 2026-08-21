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

After a live teardown finishes, it also offers to delete data/*.json — the
pre-generated test data plan files prebuild.py writes and the generators
read from (coworkers.json, bookings.json, ...). Teardown clearing the live
account and data/created-ids/ tracking never touches these on its own —
they're deliberately reusable across reseed cycles (see README's two-step
data flow) — so keeping them is the default; this only offers to remove
them too for someone who wants a genuinely clean project directory (see
maybe_clear_generated_data below).

It also offers to reset that business's
Billing.Current{Booking,CreditNote,Draft,Invoice}Number counters back to 0
(see maybe_reset_business_counters below) — deleting the tracked records
doesn't roll these back, since Nexudus just keeps auto-incrementing them on
every booking/invoice/draft/credit note this tool ever caused. Asked
interactively rather than done automatically because this account mixes
real, pre-existing data with seeded test data at the same business (see
CLAUDE.md rule 46) — resetting to 0 makes the next real invoice/booking
reuse a number a real historical record already has.

Usage:
    python teardown.py              # Live mode — deletes for real
    python teardown.py --dry-run    # Log what would be deleted
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import CREATED_IDS_DIR, DATA_DIR

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


def load_tracked_records():
    """Return {file_path: [record, ...]} for every data/created-ids/*.json file."""
    files = {}
    if not CREATED_IDS_DIR.exists():
        return files
    for path in sorted(CREATED_IDS_DIR.glob("*.json")):
        try:
            records = json.loads(path.read_text())
        except json.JSONDecodeError:
            records = []
        if records:
            files[path] = records
    return files


_ALREADY_CHARGED_TEXT = "already been charged to this customer"


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
        if nexudus_list is None:
            raise
        if entity == "bookings" and _ALREADY_CHARGED_TEXT in str(e):
            # A booking that's already been charged (CHARGE_BOOKING
            # created a linked CoworkerExtraService) rejects DELETE even
            # after cancelling — confirmed live. Some pre-existing
            # charges tracked under a fake, non-deletable Id before the
            # rule 41 fix are still sitting on their bookings under a
            # real Id that was never tracked, so the standalone
            # coworkerextraservices step earlier in ENTITY_DELETE_ORDER
            # never actually touched them.
            targets = [("coworkerextraservices", "CoworkerExtraService_BookingId", None)]
        elif entity == "coworkers":
            targets = _COWORKER_CLEANUP_TARGETS
        else:
            raise

        if not _cleanup_related(record_id, nexudus_delete, nexudus_run_command, nexudus_list, targets):
            raise
        _run_steps(entity, record_id, steps, nexudus_delete, nexudus_run_command)


def _entity_bucket(entity_outcomes, entity):
    return entity_outcomes.setdefault(entity, {
        "seen": 0, "deleted": 0, "skipped_no_support": 0, "skipped_no_id": 0,
        "failed": 0, "failure_reasons": Counter(), "entity_aborted": None,
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
        text = REPORT_PATH.read_text()
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

    lines = [f"{'Entity':<32} {'Seen':>6} {'Deleted':>8} {'NoSupport':>10} {'Failed':>8}", "-" * 68]
    aborted = []
    for entity in sorted(entity_outcomes):
        b = entity_outcomes[entity]
        lines.append(f"{entity:<32} {b['seen']:>6} {b['deleted']:>8} "
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


def run_teardown(nexudus_delete, dry_run, nexudus_run_command=None, nexudus_list=None):
    files = load_tracked_records()
    empty_summary = {"seen": 0, "deleted": 0, "skipped_no_support": 0, "failed": 0,
                      "malformed": 0, "entity_outcomes": {}}
    if not files:
        print("No tracked records found in data/created-ids/ — nothing to tear down.")
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
    malformed_count = 0
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
        total_skipped_no_support = sum(b["skipped_no_support"] for b in entity_outcomes.values())
        total_failed = sum(b["failed"] for b in entity_outcomes.values())

        print()
        print(f"Seen: {total_seen}  Deleted: {total_deleted}  "
              f"Skipped (unsupported): {total_skipped_no_support}  Failed: {total_failed}"
              + (f"  Malformed (no entity tag): {malformed_count}" if malformed_count else ""))

        summary_lines = teardown_summary_lines(entity_outcomes)
        if summary_lines:
            print()
            print("\n".join(summary_lines))

        if not dry_run and deleted_keys:
            # Rewrite each file, keeping only records that weren't confirmed deleted.
            for path, records in files.items():
                survivors = [r for i, r in enumerate(records) if (path, i) not in deleted_keys]
                path.write_text(json.dumps(survivors, indent=2) + "\n")
            print("\nUpdated data/created-ids/*.json to remove deleted records.")

    return {
        "seen": total_seen, "deleted": total_deleted,
        "skipped_no_support": total_skipped_no_support, "failed": total_failed,
        "malformed": malformed_count, "entity_outcomes": entity_outcomes,
    }


def maybe_clear_generated_data():
    """Ask whether to also delete data/*.json — the pre-generated test data
    plan files (coworkers.json, bookings.json, ...) that prebuild.py writes
    and the generators read from. Teardown clearing the live account and
    data/created-ids/ tracking (above) never touches these — they're
    reusable across reseed cycles by design, so keeping them is the default;
    this only offers to remove them too for someone who wants the project
    directory itself back to a clean slate, not just the live account."""
    files = sorted(DATA_DIR.glob("*.json"))
    if not files:
        return

    print(f"\n--- Generated data files in {DATA_DIR} ---")
    print(f"  {len(files)} files (coworkers.json, bookings.json, ...)")

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


def maybe_reset_business_counters(business_id, business_name, nexudus_list, nexudus_update):
    """Ask whether to reset business_id's billing counters to 0, now that
    teardown has cleared every tracked record. Purely optional — see the
    module docstring for why this doesn't just happen automatically."""
    settings = _fetch_counter_settings(business_id, nexudus_list)
    if not settings:
        print(f"\nNo billing counter settings found for {business_name} (id={business_id}).")
        return

    print(f"\n--- Billing counters for {business_name} (id={business_id}) ---")
    for s in settings:
        print(f"  {s['Name']}: {s['Value']}")

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--business-id", type=int, default=None,
                         help="Which business/location's billing counters to offer "
                              "resetting afterward, if this login has access to more than one")
    args = parser.parse_args()

    if args.dry_run:
        run_teardown(nexudus_delete=None, dry_run=True)
    else:
        import nexudus_client as client
        from pipeline import _select_business, list_businesses

        try:
            run_teardown(nexudus_delete=client.nexudus_delete, dry_run=False,
                         nexudus_run_command=client.nexudus_run_command,
                         nexudus_list=client.nexudus_list)
        except Exception as e:  # noqa: BLE001 — every anticipated failure mode is already
            # handled inside run_teardown itself (per-record, per-entity);
            # this is the last-resort net for something genuinely
            # unforeseen, so the optional offers below still get a chance
            # to run instead of the whole process dying here.
            print(f"\n\nTeardown hit an unexpected error and stopped early: {e}\n"
                  f"Some tracked records may not have been deleted — re-run teardown.py "
                  f"to pick up where it left off.")

        try:
            maybe_clear_generated_data()
        except EOFError:
            # No stdin to read from (piped/non-interactive run) — skip this
            # optional offer cleanly, same as the counter-reset one below.
            print("\n\nNo input available — skipping the generated-data cleanup offer.")

        try:
            businesses = list_businesses()
            if args.business_id is not None or len(businesses) <= 1:
                business = _select_business(businesses, args.business_id)
            else:
                business = _prompt_business_id(businesses)
            maybe_reset_business_counters(
                business["Id"], business.get("Name", "?"),
                client.nexudus_list, client.nexudus_update,
            )
        except EOFError:
            # No stdin to read from (piped/non-interactive run) — the
            # teardown itself already finished above; the counter-reset
            # offer is optional, so skip it cleanly instead of a traceback.
            print("\n\nNo input available — skipping the billing counter reset offer.")
