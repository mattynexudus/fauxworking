"""
Layer 2 — People

Reads pre-generated profiles from data/coworkers.json and data/visitors.json,
then pushes them to the Nexudus API.

Creates:
- Coworker × 60 (spread across teams, lifecycle states, engagement fields)
- Visitor × 60 (mix of sources and hosted/walk-in)

Prerequisites: Layer 0 + Layer 1 (business IDs, teams).
Data files: Run `python prebuild.py` first to generate data/*.json.

Usage:
    python generators/02_people.py              # Live mode
    python generators/02_people.py --dry-run     # Log only
"""

import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.base import BaseGenerator, parse_args
from config import (
    DATA_DIR,
    TEST_NAME_PREFIX,
    TEST_EMAIL_DOMAIN,
    TODAY,
    to_utc_str,
)


class PeopleGenerator(BaseGenerator):
    entity_name = "people"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.coworker_ids = {}   # index -> id
        self.visitor_ids = {}    # index -> id
        self.coworker_defs = self._load_data("coworkers.json")
        self.visitor_defs = self._load_data("visitors.json")

    @staticmethod
    def _load_data(filename):
        path = DATA_DIR / filename
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found. Run 'python prebuild.py' first."
            )
        return json.loads(path.read_text())

    def run(self, nexudus_list, nexudus_create, prev_output):
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
                "CoworkerType": 1,
                "CountryId": country,
                "SimpleTimeZoneId": tz,
                "TaxRateType": 1,
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

            if defn.get("Team") and defn["Team"] in team_ids:
                body["Teams"] = [team_ids[defn["Team"]]]

            if defn.get("ChurnProbability") is not None:
                body["ChurnProbability"] = defn["ChurnProbability"]
            if defn.get("EngagementLevel") is not None:
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

            # Resolve day offset to absolute date at runtime
            arrival_date = TODAY + timedelta(days=defn["ArrivalDayOffset"])
            expected_arrival = to_utc_str(arrival_date, hour=defn["ArrivalHour"])

            body = {
                "BusinessId": biz,
                "FullName": defn["FullName"],
                "Email": email,
                "VisitorSource": defn["VisitorSource"],
                "HostApprovalStatus": defn["HostApprovalStatus"],
                "ExpectedArrival": expected_arrival,
            }

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
    import importlib

    args = parse_args()
    gen = PeopleGenerator(dry_run=args.dry_run)

    if gen.dry_run:
        ref = importlib.import_module("generators.00_reference")
        mock_prev = {
            "business_id": "DRY-BIZ-1",
            "currency_id": "DRY-CUR-1",
            "country_id": "DRY-COUNTRY-1",
            "timezone_id": "DRY-TZ-1",
            "admin_user_id": "DRY-ADMIN-1",
            "tax_rate_ids": {"Standard": "DRY-TAX-STD"},
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
        import pipeline
        pipeline.run_up_to(2)
