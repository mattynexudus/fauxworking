"""
Shared "what's actually in the account" reporting logic — reused by
scripts/verify.sh (a standalone check-anytime command) and pipeline.py
(printed automatically at the end of a live run). Kept as one implementation
so the two never drift apart.

For the entities in config.CONFIGURABLE_VOLUME_KEYS, the "target" reported
is the actual count in the corresponding data/*.json file — i.e. whatever
this account was actually configured to generate via prebuild.py/wizard.py —
not config.VOLUMES' fixed default. Everything else still compares against
VOLUMES, since those entities have no per-run override.
"""

import json
from pathlib import Path

from config import CREATED_IDS_DIR, DATA_DIR, VOLUMES, CONFIGURABLE_VOLUME_KEYS

# Nexudus apiPath -> config.VOLUMES key, for the entities with a direct 1:1 mapping.
TARGET_KEY_BY_ENTITY = {
    "taxrates": "tax_rates", "financialaccounts": "financial_accounts",
    "resourcetypes": "resource_types", "teams": "teams", "tariffs": "tariffs",
    "products": "products", "extraservices": "extra_services", "timepasses": "time_passes",
    "resources": "resources", "floorplans": "floor_plans", "floorplandesks": "floor_plan_desks",
    "inventoryassets": "inventory_assets", "discountcodes": "discount_codes",
    "crmboards": "crm_boards", "crmboardcolumns": "crm_board_columns",
    "businesstimeslots": "business_time_slots", "helpdeskdepartments": "help_desk_departments",
    "communitygroups": "community_groups", "calendareventcategories": "calendar_event_categories",
    "coworkers": "coworkers", "visitors": "visitors",
    "coworkercontracts": "coworker_contracts", "contractproducts": "contract_products",
    "contractpausedperiods": "contract_paused_periods", "contractdeposits": "contract_deposits",
    "coworkerinventoryassets": "coworker_inventory_assets",
    "bookings": "bookings_total", "checkins": "check_ins",
    "coworkerextraservices": "coworker_extra_services",
    "coworkerbookingcredits": "coworker_booking_credits",
    "coworkertimepasses": "coworker_time_passes", "coworkerproducts": "coworker_products",
    "coworkerdeliveries": "coworker_deliveries", "calendarevents": "calendar_events",
    "eventattendees": "event_attendees", "helpdeskmessages": "help_desk_messages",
    "communitythreads": "community_threads", "communitymessages": "community_messages",
    "blogposts": "blog_posts", "coworkertasks": "coworker_tasks",
    "crmopportunities": "crm_opportunities", "crmopportunityhistories": "crm_opportunity_histories",
    "proposals": "proposals", "coworkerdatafiles": "coworker_data_files",
}

# config.VOLUMES key -> the prebuild.py output file it actually controls,
# for the CONFIGURABLE_VOLUME_KEYS subset only.
DATA_FILE_BY_VOLUME_KEY = {
    "coworkers": "coworkers.json",
    "visitors": "visitors.json",
    "bookings_total": "bookings.json",
    "check_ins": "checkins.json",
    "crm_opportunities": "crm_opportunities.json",
    "proposals": "proposals.json",
    "help_desk_messages": "helpdesk_messages.json",
    "community_threads": "community_threads.json",
    "coworker_tasks": "coworker_tasks.json",
    "coworker_time_passes": "time_passes.json",
    "coworker_products": "coworker_products.json",
}


def tracked_counts():
    """{entity: count}, tallied from every data/created-ids/*.json record."""
    counts = {}
    if CREATED_IDS_DIR.exists():
        for path in sorted(CREATED_IDS_DIR.glob("*.json")):
            try:
                records = json.loads(path.read_text())
            except json.JSONDecodeError:
                records = []
            for r in records:
                entity = r.get("entity", "?")
                counts[entity] = counts.get(entity, 0) + 1
    return counts


def target_for(entity):
    """Expected count for an entity, or None if it has no known target."""
    target_key = TARGET_KEY_BY_ENTITY.get(entity)
    if target_key is None:
        return None
    if target_key in CONFIGURABLE_VOLUME_KEYS:
        data_file = DATA_FILE_BY_VOLUME_KEY.get(target_key)
        path = DATA_DIR / data_file if data_file else None
        if path and path.exists():
            try:
                return len(json.loads(path.read_text()))
            except json.JSONDecodeError:
                pass
    return VOLUMES.get(target_key)


def report_lines():
    """The full 'what's in the account' table, as a list of printable lines."""
    counts = tracked_counts()
    if not counts:
        return ["No tracked records found — nothing has been seeded live yet."]

    lines = [f"{'Entity':<32} {'Created':>8} {'Target':>8}", "-" * 50]
    for entity in sorted(counts):
        created = counts[entity]
        target = target_for(entity)
        flag = "" if target is None or created >= target else "  <-- below target"
        lines.append(f"{entity:<32} {created:>8} {str(target) if target is not None else '-':>8}{flag}")
    lines.append("")
    lines.append(f"Total tracked records: {sum(counts.values())}")
    return lines


def write_report(path):
    """Write the report to a file with a timestamp header, for later
    reference — the point being a QA person can open this without re-running
    anything or scrolling back through a terminal."""
    from datetime import datetime, timezone
    lines = [f"Report generated {datetime.now(timezone.utc).isoformat()}", ""]
    lines += report_lines()
    Path(path).write_text("\n".join(lines) + "\n")
