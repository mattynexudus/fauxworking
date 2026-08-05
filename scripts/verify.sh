#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Record Count Verification ==="
echo "(counts tracked records in data/created-ids/*.json against config.py targets)"
echo ""

python3 - <<'EOF'
import json
from pathlib import Path
import sys

sys.path.insert(0, ".")
from config import CREATED_IDS_DIR, VOLUMES

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

if not counts:
    print("No tracked records found — nothing has been seeded live yet.")
    sys.exit(0)

print(f"{'Entity':<32} {'Created':>8} {'Target':>8}")
print("-" * 50)
for entity in sorted(counts):
    created = counts[entity]
    target_key = TARGET_KEY_BY_ENTITY.get(entity)
    target = VOLUMES.get(target_key, "-") if target_key else "-"
    flag = "" if target == "-" or created >= target else "  <-- below target"
    print(f"{entity:<32} {created:>8} {str(target):>8}{flag}")

print()
print(f"Total tracked records: {sum(counts.values())}")
EOF
