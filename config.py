"""
Configuration for Nexudus test data seeding.

All dates are rolling — anchored to the date the generator runs.
No hardcoded dates anywhere; generators import from here.
"""

from datetime import date, datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta

# ---------------------------------------------------------------------------
# Run date — everything is relative to this
# ---------------------------------------------------------------------------
TODAY = date.today()
NOW = datetime.now(timezone.utc)

# Rolling 24-month window
WINDOW_MONTHS = 24
WINDOW_START = TODAY - relativedelta(months=WINDOW_MONTHS)  # 24 months ago
WINDOW_END = TODAY

# Convenience date helpers (all as date objects)
MONTHS_AGO_24 = TODAY - relativedelta(months=24)
MONTHS_AGO_18 = TODAY - relativedelta(months=18)
MONTHS_AGO_12 = TODAY - relativedelta(months=12)
MONTHS_AGO_6 = TODAY - relativedelta(months=6)
MONTHS_AGO_3 = TODAY - relativedelta(months=3)
MONTHS_AGO_1 = TODAY - relativedelta(months=1)
DAYS_AGO_30 = TODAY - timedelta(days=30)
DAYS_AGO_90 = TODAY - timedelta(days=90)
DAYS_AHEAD_30 = TODAY + timedelta(days=30)
DAYS_AHEAD_90 = TODAY + timedelta(days=90)


def to_utc_str(d, hour=0, minute=0, second=0):
    """Convert a date (or datetime) to a UTC ISO string with Z suffix."""
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%dT%H:%M:%SZ")
    return datetime(d.year, d.month, d.day, hour, minute, second,
                    tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Scale profile — "small" is default, "large" multiplies by 3
# ---------------------------------------------------------------------------
SCALE = "small"
SCALE_MULTIPLIER = 1 if SCALE == "small" else 3

# ---------------------------------------------------------------------------
# Business IDs — populated by Layer 0 after querying `nexudus whoami`
# ---------------------------------------------------------------------------
BUSINESS_IDS = []  # Filled at runtime from nexudus whoami
DEFAULT_BUSINESS_ID = None  # Set by 00_reference.py

# ---------------------------------------------------------------------------
# Target volumes (small profile)
# ---------------------------------------------------------------------------
VOLUMES = {
    # Layer 0
    "tax_rates": 3,
    "financial_accounts": 8,
    "resource_types": 5,

    # Layer 1
    "teams": 5,
    "tariffs": 8,
    "products": 15,
    "extra_services": 6,
    "time_passes": 4,
    "resources": 20,
    "floor_plans": 3,
    "floor_plan_desks": 40,
    "inventory_assets": 15,
    "discount_codes": 6,
    "crm_boards": 2,
    "crm_board_columns": 10,
    "business_time_slots": 3,
    "help_desk_departments": 3,
    "community_groups": 3,
    "calendar_event_categories": 4,

    # Layer 2
    "coworkers": 60,
    "visitors": 60,

    # Layer 3
    "coworker_contracts": 90,
    "contract_products": 30,
    "contract_schedules": 8,
    "contract_paused_periods": 12,
    "contract_deposits": 10,
    "coworker_inventory_assets": 12,

    # Layer 4
    "bookings_total": 240,
    "bookings_to_cancel": 40,
    "booking_visitors": 50,
    "check_ins": 300,
    "coworker_extra_services": 80,
    "coworker_booking_credits": 25,
    "coworker_booking_credit_use_histories": 50,
    "coworker_time_passes": 40,
    "coworker_products": 20,
    "coworker_deliveries": 40,
    "calendar_events": 20,
    "event_attendees": 60,
    "help_desk_messages": 25,
    "community_threads": 15,
    "community_messages": 40,
    "blog_posts": 10,
    "coworker_tasks": 20,

    # Layer 5
    "crm_opportunities": 30,
    "crm_opportunity_histories": 60,
    "proposals": 15,
    "coworker_data_files": 10,
}

# Apply scale multiplier
VOLUMES = {k: v * SCALE_MULTIPLIER for k, v in VOLUMES.items()}

# ---------------------------------------------------------------------------
# Configurable headline volumes
# ---------------------------------------------------------------------------
# Most of VOLUMES above is descriptive, not load-bearing — only the keys
# listed here are actually wired up to override generation (via prebuild.py's
# CLI flags / wizard.py's prompts). The rest describe fixed, hand-authored
# content (teams, resources, calendar events, ...) that can't just be scaled
# by a number without also writing new names/descriptions — see
# reference/extending-the-model.md.
CONFIGURABLE_VOLUME_KEYS = [
    "coworkers", "visitors", "bookings_total", "check_ins",
    "crm_opportunities", "proposals", "help_desk_messages", "community_threads",
    "coworker_tasks", "coworker_time_passes", "coworker_products",
]

# ---------------------------------------------------------------------------
# Test markers — used for idempotency and teardown
# ---------------------------------------------------------------------------
TEST_EMAIL_DOMAIN = "seeddata.local"
TEST_EMAIL_PREFIX = "test-"
# No name prefix — records should look like real data. Safe teardown relies
# on data/created-ids/<entity>.json (every created record's Id is tracked
# there), not on a naming convention. Coworker emails remain the one
# marker baked into the record itself (test-NNN@seeddata.local).
TEST_NAME_PREFIX = ""

# ---------------------------------------------------------------------------
# Seed for reproducibility
# ---------------------------------------------------------------------------
RANDOM_SEED = 42

# ---------------------------------------------------------------------------
# Live-write pacing
# ---------------------------------------------------------------------------
# A small delay between writes (create/update/delete/run_command — not
# list/get) to Nexudus, applied centrally in nexudus_client.py::_request().
# Not a proven fix for anything specific — Nexudus's documented throttle
# mechanism is 429 (already retried transparently), and the account-wide
# creation-rate condition seen live as a 401 "Access Denied" (on Coworker
# and BookingVisitor creation) has no official documentation under any
# name. This is a low-cost hedge against undiscovered rate sensitivity in
# the highest-volume tight loops (bookings, bookingvisitors, checkins),
# not a confirmed cause-and-effect fix. 0 disables it entirely.
WRITE_PACING_SECONDS = 0.1

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
CREATED_IDS_DIR = DATA_DIR / "created-ids"
OUTPUT_DIR = PROJECT_ROOT / "output"
