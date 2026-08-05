"""
Layer 2 — People

Creates:
- Coworker × 60 (spread across teams, lifecycle states, engagement fields)
- Visitor × 60 (mix of sources and hosted/walk-in)

Prerequisites: Layer 0 + Layer 1 (business IDs, teams).

Usage:
    python generators/02_people.py              # Live mode
    python generators/02_people.py --dry-run     # Log only
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import importlib
from faker import Faker

from generators.base import BaseGenerator, parse_args
from config import (
    TEST_NAME_PREFIX,
    TEST_EMAIL_DOMAIN,
    TEST_EMAIL_PREFIX,
    VOLUMES,
    RANDOM_SEED,
    TODAY,
    MONTHS_AGO_3,
    MONTHS_AGO_6,
    MONTHS_AGO_12,
    MONTHS_AGO_24,
    DAYS_AGO_30,
    DAYS_AHEAD_30,
    to_utc_str,
)

fake = Faker("en_GB")
Faker.seed(RANDOM_SEED)

# ---------------------------------------------------------------------------
# Coworker lifecycle assignments — §4a
# Maps scenario name → count + description
# These tags drive contract creation in Layer 3
# ---------------------------------------------------------------------------
LIFECYCLE_SCENARIOS = [
    ("long_term_active", 20),   # StartDate 12–24mo ago, no EndDate
    ("new_joiner",        8),   # StartDate within last 3 months
    ("plan_change",       6),   # Contract A ends, Contract B starts
    ("churned",          10),   # EndDate in past, no subsequent contract
    ("returned",          4),   # Churned 6+mo ago, new contract recently
    ("multi_contract",    6),   # Two active contracts simultaneously
    ("unsubscribed",      3),   # Had 2 contracts, one ended, one remains
    ("ending_soon",       3),   # CancellationDate within 30–90 days
]

# Engagement distribution — §4o
# (churn_probability, engagement_level) pairs
ENGAGEMENT_PROFILES = {
    "long_term_active": (0.1, "High"),
    "new_joiner":       (0.2, "High"),
    "plan_change":      (0.3, "Medium"),
    "churned":          (0.8, "Low"),
    "returned":         (0.3, "Medium"),
    "multi_contract":   (0.1, "High"),
    "unsubscribed":     (0.5, "Medium"),
    "ending_soon":      (0.7, "Low"),
}


def _generate_coworker_definitions(rng):
    """Generate 60 coworker definitions with lifecycle tags and engagement."""
    coworkers = []
    idx = 0

    team_names = [
        f"{TEST_NAME_PREFIX}Acme Corp",
        f"{TEST_NAME_PREFIX}Bright Studio",
        f"{TEST_NAME_PREFIX}CloudNine Labs",
        f"{TEST_NAME_PREFIX}Delta Ventures",
        f"{TEST_NAME_PREFIX}Echo Digital",
    ]

    for scenario, count in LIFECYCLE_SCENARIOS:
        for _ in range(count):
            idx += 1
            gender_val = rng.choice([2, 3, 4, 5])  # Male, Female, Other, RatherNotSay
            first = fake.first_name_male() if gender_val == 2 else fake.first_name_female() if gender_val == 3 else fake.first_name()
            last = fake.last_name()
            full_name = f"{first} {last}"

            # Distribute across teams — ~40 members have teams, ~20 are freelancers
            team = rng.choice(team_names) if idx <= 40 else None

            churn, engagement = ENGAGEMENT_PROFILES[scenario]
            # Add some variance
            churn = round(max(0, min(1, churn + rng.uniform(-0.1, 0.1))), 2)

            coworkers.append({
                "index": idx,
                "FullName": full_name,
                "Email": f"{TEST_EMAIL_PREFIX}{idx:03d}@{TEST_EMAIL_DOMAIN}",
                "Gender": gender_val,
                "Team": team,
                "Scenario": scenario,
                "ChurnProbability": churn,
                "EngagementLevel": engagement,
                # Attendance — varied by scenario
                "Attendance": _attendance_pattern(rng, scenario),
            })

    return coworkers


def _attendance_pattern(rng, scenario):
    """Generate realistic weekday attendance patterns."""
    # eCoworkerAttendance: 1=Office, 2=Home, 3=Abroad, 4=NotWorking, 5=Undefined
    if scenario in ("long_term_active", "multi_contract"):
        # Mostly in-office
        return {day: rng.choice([1, 1, 1, 2]) for day in
                ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
    elif scenario in ("new_joiner", "returned"):
        # Mix of office and home
        return {day: rng.choice([1, 1, 2, 2, 5]) for day in
                ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
    elif scenario in ("churned", "ending_soon"):
        # Mostly undefined/not working
        return {day: rng.choice([4, 5, 5, 2]) for day in
                ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
    else:
        # Default mix
        return {day: rng.choice([1, 2, 5]) for day in
                ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}


def _generate_visitor_definitions(rng, coworker_count):
    """Generate 60 visitor definitions."""
    visitors = []
    # VisitorSource: 1=Administrator, 2=NexIO, 3=Customer
    sources = [1, 1, 2, 2, 2, 3]  # Weighted toward NexIO
    # HostApprovalStatus: 1=NotRequired, 2=Requested, 5=AcceptedAndGrant
    statuses = [1, 1, 1, 5, 5, 2]

    for i in range(1, VOLUMES["visitors"] + 1):
        host_idx = rng.randint(1, coworker_count) if rng.random() < 0.7 else None
        # Spread expected arrivals across last 6 months to today + 30 days
        days_offset = rng.randint(-180, 30)
        arrival_date = TODAY + __import__("datetime").timedelta(days=days_offset)

        visitors.append({
            "index": i,
            "FullName": fake.name(),
            "Email": f"visitor-{i:03d}@{TEST_EMAIL_DOMAIN}",
            "VisitorSource": rng.choice(sources),
            "HostApprovalStatus": rng.choice(statuses),
            "HostCoworkerIndex": host_idx,
            "ExpectedArrival": to_utc_str(arrival_date, hour=rng.randint(8, 17)),
        })

    return visitors


class PeopleGenerator(BaseGenerator):
    entity_name = "people"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.coworker_ids = {}   # index -> id
        self.visitor_ids = {}    # index -> id
        self.coworker_defs = _generate_coworker_definitions(self.rng)
        self.visitor_defs = _generate_visitor_definitions(self.rng, len(self.coworker_defs))

    def run(self, nexudus_list, nexudus_create, prev_output):
        """
        Execute Layer 2 creation.

        Args:
            nexudus_list: callable(entity, filters) -> list of records
            nexudus_create: callable(entity, body) -> created record
            prev_output: dict from Layer 1 output (includes business_id, team_ids, etc.)
        """
        biz = prev_output["business_id"]
        country = prev_output.get("country_id")
        tz = prev_output.get("timezone_id")
        team_ids = prev_output.get("team_ids", {})

        self._create_coworkers(biz, country, tz, team_ids, nexudus_list, nexudus_create)
        self._create_visitors(biz, nexudus_list, nexudus_create)

        self.log.info("Layer 2 complete. Coworkers: %d, Visitors: %d",
                      len(self.coworker_ids), len(self.visitor_ids))

        return {
            **prev_output,
            "coworker_ids": self.coworker_ids,
            "coworker_defs": self.coworker_defs,
            "visitor_ids": self.visitor_ids,
        }

    def _create_coworkers(self, biz, country, tz, team_ids, nexudus_list, nexudus_create):
        self.log.info("--- Coworkers (%d) ---", len(self.coworker_defs))

        # Check for already-created test coworkers
        existing = nexudus_list("coworkers", {"Coworker_Email": f"@{TEST_EMAIL_DOMAIN}"})
        existing_by_email = {r.get("Email", ""): r["Id"] for r in existing}

        for defn in self.coworker_defs:
            email = defn["Email"]
            idx = defn["index"]

            if email in existing_by_email:
                self.log.info("Coworker '%s' already exists (id=%s)", email, existing_by_email[email])
                self.coworker_ids[idx] = existing_by_email[email]
                continue

            att = defn["Attendance"]
            body = {
                "FullName": defn["FullName"],
                "Email": email,
                "Gender": defn["Gender"],
                "CoworkerType": 1,  # Individual
                "CountryId": country,
                "SimpleTimeZoneId": tz,
                "TaxRateType": 1,  # Default
                "CheckinSinceLastRenewal": 0,
                "MinutesSinceLastRenewal": 0,
                "TariffInvoiceEvery": 0,
                "TariffInvoiceEveryWeeks": 0,
                "MondayAttendance": att.get("Monday", 5),
                "TuesdayAttendance": att.get("Tuesday", 5),
                "WednesdayAttendance": att.get("Wednesday", 5),
                "ThursdayAttendance": att.get("Thursday", 5),
                "FridayAttendance": att.get("Friday", 5),
                "SaturdayAttendance": att.get("Saturday", 4),
                "SundayAttendance": att.get("Sunday", 4),
            }

            # Team assignment
            if defn["Team"] and defn["Team"] in team_ids:
                body["Teams"] = [team_ids[defn["Team"]]]

            # Engagement fields
            if defn["ChurnProbability"] is not None:
                body["ChurnProbability"] = defn["ChurnProbability"]
            if defn["EngagementLevel"] is not None:
                body["EngagementLevel"] = defn["EngagementLevel"]

            if self.dry_run:
                self.log_would_create("coworkers", body)
                self.coworker_ids[idx] = f"DRY-CW-{idx}"
            else:
                result = nexudus_create("coworkers", body)
                self.coworker_ids[idx] = result["Id"]
                self.track_id({
                    "entity": "coworkers", "Id": result["Id"],
                    "Email": email, "Scenario": defn["Scenario"],
                    "Index": idx,
                })
                self.log.info("Created coworker #%d '%s' [%s] (id=%s)",
                              idx, defn["FullName"], defn["Scenario"], result["Id"])

    def _create_visitors(self, biz, nexudus_list, nexudus_create):
        self.log.info("--- Visitors (%d) ---", len(self.visitor_defs))

        existing = nexudus_list("visitors", {"Visitor_Business": biz})
        existing_by_email = {r.get("Email", ""): r["Id"] for r in existing}

        for defn in self.visitor_defs:
            email = defn["Email"]
            idx = defn["index"]

            if email in existing_by_email:
                self.log.info("Visitor '%s' already exists (id=%s)", email, existing_by_email[email])
                self.visitor_ids[idx] = existing_by_email[email]
                continue

            body = {
                "BusinessId": biz,
                "FullName": defn["FullName"],
                "Email": email,
                "VisitorSource": defn["VisitorSource"],
                "HostApprovalStatus": defn["HostApprovalStatus"],
                "ExpectedArrival": defn["ExpectedArrival"],
            }

            # Link to host coworker if applicable
            host_idx = defn.get("HostCoworkerIndex")
            if host_idx and host_idx in self.coworker_ids:
                body["CoworkerId"] = self.coworker_ids[host_idx]

            if self.dry_run:
                self.log_would_create("visitors", body)
                self.visitor_ids[idx] = f"DRY-VIS-{idx}"
            else:
                result = nexudus_create("visitors", body)
                self.visitor_ids[idx] = result["Id"]
                self.track_id({
                    "entity": "visitors", "Id": result["Id"],
                    "Email": email, "Index": idx,
                })
                self.log.info("Created visitor #%d '%s' (id=%s)",
                              idx, defn["FullName"], result["Id"])


if __name__ == "__main__":
    args = parse_args()
    gen = PeopleGenerator(dry_run=args.dry_run)

    if gen.dry_run:
        ref = importlib.import_module("generators.00_reference")
        mock_prev = {
            "business_id": "DRY-BIZ-1",
            "currency_id": "DRY-CUR-1",
            "country_id": "DRY-COUNTRY-1",
            "timezone_id": "DRY-TZ-1",
            "tax_rate_ids": {"Standard": "DRY-TAX-STD", "Reduced": "DRY-TAX-RED", "Zero-rated": "DRY-TAX-ZERO"},
            "fin_account_ids": {c["Code"]: f"DRY-FA-{c['Code']}" for c in ref.FINANCIAL_ACCOUNTS},
            "resource_type_ids": {r["Name"]: f"DRY-RT-{r['Name']}" for r in ref.RESOURCE_TYPES},
            "team_ids": {
                f"{TEST_NAME_PREFIX}Acme Corp": "DRY-TEAM-1",
                f"{TEST_NAME_PREFIX}Bright Studio": "DRY-TEAM-2",
                f"{TEST_NAME_PREFIX}CloudNine Labs": "DRY-TEAM-3",
                f"{TEST_NAME_PREFIX}Delta Ventures": "DRY-TEAM-4",
                f"{TEST_NAME_PREFIX}Echo Digital": "DRY-TEAM-5",
            },
        }
        gen.run(
            nexudus_list=lambda entity, filters: [],
            nexudus_create=lambda entity, body: {"Id": f"DRY-{entity}-{body.get('Email', 'x')}"},
            prev_output=mock_prev,
        )
    else:
        print("Live mode requires MCP context. Run via agent or use --dry-run.")
        sys.exit(1)
