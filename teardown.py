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
04_activity.py); CoworkerBookingCreditUseHistory and CoworkerInvoice
support list/get/update only, no delete.

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
    "coworkertimepasses",
    "coworkerbookingcredits",
    "coworkerextraservices",
    "checkins",
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
NO_DELETE_SUPPORT = {"cancelledbookings", "coworkerbookingcreditusehistories", "coworkerinvoices"}


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


def run_teardown(nexudus_delete, dry_run):
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
                nexudus_delete(entity, record_id)
                deleted_keys.add((path, i))
                total_deleted += 1
            except Exception as e:  # noqa: BLE001 — log and continue tearing down the rest
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
        run_teardown(nexudus_delete=client.nexudus_delete, dry_run=False)
