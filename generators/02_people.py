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

import importlib
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.base import BaseGenerator, SYSTEMIC_FAILURE_THRESHOLD, parse_args
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
        self.set_target("coworkers", len(self.coworker_defs))
        self.set_target("visitors", len(self.visitor_defs))

    @staticmethod
    def _load_data(filename):
        path = DATA_DIR / filename
        if not path.exists():
            raise FileNotFoundError(
                f"{path} not found. Run 'python prebuild.py' first."
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def run(self, nexudus_list, nexudus_create, nexudus_update, nexudus_get, prev_output):
        biz = prev_output["business_id"]
        country = prev_output.get("country_id")
        tz = prev_output.get("timezone_id")
        team_ids = prev_output.get("team_ids", {})

        self._create_coworkers(biz, country, tz, team_ids, nexudus_list, nexudus_create,
                               nexudus_get)
        self._create_visitors(biz, nexudus_list, nexudus_create)
        self._set_team_paying_members(team_ids, nexudus_list, nexudus_update)

        self.log.info("Layer 2 complete. Coworkers: %d, Visitors: %d",
                      len(self.coworker_ids), len(self.visitor_ids))

        return {
            **prev_output,
            "coworker_ids": self.coworker_ids,
            "coworker_defs": self.coworker_defs,
            "visitor_ids": self.visitor_ids,
        }

    def _create_coworkers(self, biz, country, tz, team_ids, nexudus_list, nexudus_create,
                          nexudus_get=None):
        self.log.info("--- Coworkers (%d) ---", len(self.coworker_defs))

        # Coworker_Email is an exact-match filter, not a substring/contains —
        # filtering by "@{domain}" always returns zero results. List every
        # coworker and filter client-side by domain instead.
        #
        # Scoped to this business client-side (rule 46's convention) rather
        # than adopting anything the login can see: this list is unfiltered,
        # and on a multi-business login (rule 8) that includes coworkers this
        # run has no business writing children against.
        existing = nexudus_list("coworkers", {})
        existing_by_email = {
            r.get("Email", ""): r["Id"] for r in existing
            if r.get("Email", "").endswith(f"@{TEST_EMAIL_DOMAIN}")
            and str(r.get("InvoicingBusinessId")) == str(biz)
        }

        for defn in self.coworker_defs:
            email = defn["Email"]
            idx = defn["index"]

            if email in existing_by_email:
                existing_id = existing_by_email[email]
                if self._coworker_is_usable(existing_id, nexudus_get):
                    self.log.info("Coworker '%s' already exists (id=%s)", email, existing_id)
                    self.count_skip(entity="coworkers")
                    self.coworker_ids[idx] = existing_id
                    continue
                self.log.warning(
                    "Coworker '%s' is listed (id=%s) but can't be fetched — a deleted record "
                    "the list endpoint still returns. Creating a fresh one instead of adopting "
                    "an id every child record would be refused against.", email, existing_id)

            att = defn["Attendance"]
            body = {
                "FullName": defn["FullName"],
                "Email": email,
                "Gender": defn["Gender"],
                "CoworkerType": 1,
                # Coworker has no BusinessId field. There are two separate
                # fields instead, confirmed via the Nexudus API docs and a
                # live update test: Businesses (plural — which businesses
                # this coworker is linked to) and InvoicingBusinessId (their
                # actual "home" business — confirmed live that updating
                # Businesses alone does NOT move InvoicingBusinessId, so
                # both need to be set). Omitting them entirely (as this body
                # did until now) doesn't error — it silently falls back to
                # some other business tied to the login/token, not the one
                # this run actually selected. Confirmed live: a
                # multi-business login had coworkers land in the wrong
                # business with these fields missing.
                "Businesses": [biz],
                "InvoicingBusinessId": biz,
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

            # ChurnProbability/EngagementLevel are readOnly on Coworker (confirmed
            # via schema) — server-computed, not writable — so they're deliberately
            # left out of this body. Data still exists per-coworker in
            # coworkers.json (harmless, just unused here) in case a future report
            # wants to reference the intended synthetic value.

            if self.dry_run:
                self.log_would_create("coworkers", body)
                self.coworker_ids[idx] = f"DRY-CW-{idx}"
            else:
                try:
                    result = nexudus_create("coworkers", body)
                except Exception as e:  # noqa: BLE001
                    # "Access Denied" used to break out of this loop on its
                    # very first occurrence, on the theory that it meant the
                    # account had hit a per-day coworker/seat cap (CLAUDE.md
                    # rule 30, user-reported, never independently confirmed).
                    # A full day of live runs disproved that: the same message
                    # turned out to mean "this write references a record that
                    # no longer exists" — see _coworker_is_usable — and it
                    # appeared on thirteen other entities in runs where every
                    # coworker created fine. Abandoning ~50 coworkers on one
                    # ambiguous error is far more expensive than skipping the
                    # one record, and if a real cap does exist, the ordinary
                    # streak counter below still stops the loop once enough
                    # records fail consecutively.
                    verdict = self.classify_failure("coworkers", e)
                    if verdict == "systemic":
                        self.log.warning(
                            "Coworker creation stopped at #%d — %d records in a row failed "
                            "with none succeeding, skipping the rest rather than spending "
                            "the wall-clock time to fail on each: %s",
                            idx, SYSTEMIC_FAILURE_THRESHOLD, e,
                            skip=True, entity="coworkers", reason="systemic_repeated_failure")
                        break
                    self.log.warning("Skipping coworker #%d — create failed: %s", idx, e,
                                      skip=True, entity="coworkers", reason="unknown_error")
                    continue
                self.coworker_ids[idx] = result["Id"]
                self.track_id({
                    "entity": "coworkers", **result,
                    "Email": email, "Scenario": defn["Scenario"],
                    "Index": idx,
                })
                self.log.info("Created coworker #%d '%s' [%s] (id=%s)",
                              idx, defn["FullName"], defn["Scenario"], result["Id"])

    def _coworker_is_usable(self, coworker_id, nexudus_get):
        """True if `coworker_id` can actually be referenced by other records.

        The coworkers list endpoint keeps returning records that no longer
        exist — confirmed live: five coworkers from an earlier session
        (test-001/009/012/015/034) come back in the list, carrying this
        business's own InvoicingBusinessId, but a GET on any of their ids
        fails 401 "Not found", and four of the five read Active=False with
        UpdatedBy=[System]. They're what a previous teardown deleted; only
        the list hasn't caught up.

        Adopting one of those ids is silently fatal, because Nexudus reports
        a write referencing a dead coworker as 401 "Access Denied." — not a
        validation error naming the field. Every such record then fails on
        every run forever: two visitors, three contracts, two inventory
        assignments, two team updates and two bill runs in one real run, all
        of them tracing back to these five ids, and each cluster tripping
        classify_failure's breaker and taking the rest of its entity down
        with it (2 bad contracts became 67 missing ones).

        Active=False alone isn't the test — one of the five reads
        Active=True and still 404s on GET — so this fetches the record.
        """
        if nexudus_get is None:  # standalone/dry-run callers without the fetcher
            return True
        try:
            nexudus_get("coworkers", coworker_id)
            return True
        except Exception:  # noqa: BLE001 — any failure to fetch means don't adopt
            return False

    def _create_visitors(self, biz, nexudus_list, nexudus_create):
        self.log.info("--- Visitors (%d) ---", len(self.visitor_defs))

        existing = nexudus_list("visitors", {"Visitor_Business": biz})
        existing_by_email = {r.get("Email", ""): r["Id"] for r in existing}

        for defn in self.visitor_defs:
            email = defn["Email"]
            idx = defn["index"]

            if email in existing_by_email:
                self.log.info("Visitor '%s' already exists (id=%s)", email, existing_by_email[email])
                self.count_skip(entity="visitors")
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

            # A visitor whose host coworker was never created is not
            # salvageable by dropping the field: confirmed live, Nexudus
            # answers a hostless Visitor with 401 "Access Denied.", which
            # reads as an account-wide problem and, three in a row, stops the
            # whole entity (see classify_failure). Skipping it is the same
            # treatment every other generator gives a missing parent
            # (rule 29) — one clearly-attributed record lost instead of the
            # rest of the run's visitors.
            host_idx = defn.get("HostCoworkerIndex")
            if host_idx and host_idx not in self.coworker_ids:
                self.log.warning(
                    "Skipping visitor #%d — host coworker #%s was never created",
                    idx, host_idx, skip=True, entity="visitors", reason="parent_skipped")
                continue
            if host_idx:
                body["CoworkerId"] = self.coworker_ids[host_idx]

            if self.dry_run:
                self.log_would_create("visitors", body)
                self.visitor_ids[idx] = f"DRY-VIS-{idx}"
                continue

            try:
                result = nexudus_create("visitors", body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("visitors", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping visitor creation — this error has repeated several "
                        "records in a row with none succeeding — skipping the rest of them rather than spending the wall-clock time to fail on each: %s", e,
                        skip=True, entity="visitors", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping visitor #%d — create failed: %s", idx, e,
                                  skip=True, entity="visitors", reason="unknown_error")
                continue

            self.visitor_ids[idx] = result["Id"]
            self.track_id({
                "entity": "visitors", **result,
                "Email": email, "Index": idx,
            })
            self.log.info("Created visitor #%d '%s' (id=%s)",
                          idx, defn["FullName"], result["Id"])

    def _set_team_paying_members(self, team_ids, nexudus_list, nexudus_update):
        """Merged-billing teams need CreateSingleInvoiceForTeam + PayingMemberId,
        which the API only accepts once the paying member is an actual Coworker
        on the team — see generators/01_structural.py's MERGED_BILLING_TEAMS."""
        struct = importlib.import_module("generators.01_structural")
        merged = struct.MERGED_BILLING_TEAMS
        if not merged:
            return

        self.log.info("--- Team paying members (merged billing) ---")

        for team_name, extra_fields in merged.items():
            team_id = team_ids.get(team_name)
            if not team_id:
                continue

            members = [d for d in self.coworker_defs if d.get("Team") == team_name]
            if not members:
                self.log.warning("No coworker assigned to team '%s' — skipping paying member setup", team_name,
                                  skip=True, entity="teams", reason="parent_skipped")
                continue

            # Any member who actually exists will do, not strictly the first
            # one on the team: picking the first blindly is what failed both
            # 'Acme Corp' (member #1) and 'Bright Studio' (member #9) on every
            # run — both teams led with a coworker that was never created, and
            # the update came back 401 "Access Denied.". 'Echo Digital', the
            # one team whose first member existed, succeeded every time.
            member = next((d for d in members if self.coworker_ids.get(d["index"])), None)
            if member is None:
                self.log.warning("Skipping paying member setup for '%s' — none of its %d "
                                  "coworkers were created", team_name, len(members),
                                  skip=True, entity="teams", reason="parent_skipped")
                continue

            coworker_id = self.coworker_ids[member["index"]]

            fields = {"PayingMemberId": coworker_id, "CreateSingleInvoiceForTeam": True, **extra_fields}

            if self.dry_run:
                self.log.info("WOULD SET paying member for '%s' -> coworker id=%s (%s)",
                              team_name, coworker_id, fields)
                continue

            try:
                nexudus_update("teams", team_id, fields)
            except Exception as e:  # noqa: BLE001
                self.log.warning("Failed to set paying member for '%s': %s", team_name, e,
                                  skip=True, entity="teams", reason="unknown_error")
                continue
            self.log.info("Set paying member for '%s' (team id=%s, coworker id=%s)",
                          team_name, team_id, coworker_id)


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
            nexudus_update=lambda entity, id, body: None,
            nexudus_get=lambda entity, id: {"Id": id},
            prev_output=mock_prev,
        )
    else:
        import pipeline
        pipeline.run_up_to(2, business_id=args.business_id)
