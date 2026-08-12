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

Usage:
    python teardown.py              # Live mode — deletes for real
    python teardown.py --dry-run    # Log what would be deleted
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from config import CREATED_IDS_DIR

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

# A few entities reject a plain DELETE outright (405 Method Not Allowed —
# not a dependency block, the HTTP verb itself isn't supported) and only
# delete via one or more commands instead, found by capturing the real
# admin UI's network request (same technique as PROPOSAL_SEND/
# PROPOSAL_ACCEPT and COWORKER_INVOICE_CANCEL/REFUND — see CLAUDE.md rule
# 27). Confirmed live for every entry below. coworkercontracts needs two
# commands in sequence — it must be cancelled before it can be deleted.
# Every other entity in ENTITY_DELETE_ORDER uses a plain nexudus_delete.
COMMAND_DELETE = {
    "coworkerinvoices": ["COWORKER_INVOICE_DELETE"],
    "coworkers": ["COWORKER_DELETE"],
    "coworkercontracts": ["CANCEL_CONTRACT", "DELETE_CONTRACT"],
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


def _delete_one(entity, record_id, nexudus_delete, nexudus_run_command):
    """Most entities use a plain DELETE; a few (see COMMAND_DELETE) only
    support deletion via one or more run-commands, executed in order.

    An earlier step failing (e.g. CANCEL_CONTRACT on a contract that's
    already cancelled from a prior partial teardown) shouldn't block a
    later step that might still succeed on its own — only raise if the
    LAST command in the sequence is the one that fails."""
    commands = COMMAND_DELETE.get(entity)
    if commands is None:
        nexudus_delete(entity, record_id)
        return

    last_error = None
    for command in commands:
        try:
            nexudus_run_command(entity, command, [record_id])
            last_error = None
        except Exception as e:  # noqa: BLE001
            last_error = e
    if last_error is not None:
        raise last_error


def run_teardown(nexudus_delete, dry_run, nexudus_run_command=None):
    files = load_tracked_records()
    if not files:
        print("No tracked records found in data/created-ids/ — nothing to tear down.")
        return

    # Pool every record across files, remembering which file+index it came from
    # so we can rewrite each file afterward with only the survivors.
    pooled = []  # (file_path, index_in_file, record)
    for path, records in files.items():
        for i, r in enumerate(records):
            pooled.append((path, i, r))

    by_entity = {}
    for item in pooled:
        entity = item[2].get("entity")
        by_entity.setdefault(entity, []).append(item)

    total_seen = len(pooled)
    total_deleted = 0
    total_skipped_no_support = 0
    total_failed = 0
    deleted_keys = set()  # (path, index) confirmed gone

    ordered_entities = ENTITY_DELETE_ORDER + sorted(set(by_entity) - set(ENTITY_DELETE_ORDER))

    for entity in ordered_entities:
        items = by_entity.get(entity)
        if not items:
            continue

        if entity in NO_DELETE_SUPPORT:
            print(f"--- {entity} ({len(items)}) --- SKIPPED (no delete support in API)")
            total_skipped_no_support += len(items)
            continue

        print(f"--- {entity} ({len(items)}) ---")
        for path, i, record in items:
            record_id = record.get("Id")
            if record_id is None or (isinstance(record_id, str) and record_id.startswith("DRY-")):
                continue

            if dry_run:
                print(f"  WOULD DELETE {entity} {record_id}")
                continue

            try:
                _delete_one(entity, record_id, nexudus_delete, nexudus_run_command)
                deleted_keys.add((path, i))
                total_deleted += 1
            except Exception as e:  # noqa: BLE001 — log and continue tearing down the rest
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
                    total_deleted += 1
                else:
                    print(f"  FAILED to delete {entity} {record_id}: {e}")
                    total_failed += 1

    print()
    print(f"Seen: {total_seen}  Deleted: {total_deleted}  "
          f"Skipped (unsupported): {total_skipped_no_support}  Failed: {total_failed}")

    if dry_run or not deleted_keys:
        return

    # Rewrite each file, keeping only records that weren't confirmed deleted.
    for path, records in files.items():
        survivors = [r for i, r in enumerate(records) if (path, i) not in deleted_keys]
        path.write_text(json.dumps(survivors, indent=2) + "\n")
    print("Updated data/created-ids/*.json to remove deleted records.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        run_teardown(nexudus_delete=None, dry_run=True)
    else:
        import nexudus_client as client
        run_teardown(nexudus_delete=client.nexudus_delete, dry_run=False,
                     nexudus_run_command=client.nexudus_run_command)
