"""
Daily Update — fresh "today" records so live dashboards look alive.

Unlike the layer generators (00-07), this script is meant to be run
repeatedly — daily, via cron/launchd, or on demand before reviewing a
dashboard — so it cannot follow the "prebuild once, commit the JSON" pattern.
Each run needs genuinely fresh dates, and future runs need data that doesn't
exist yet. Randomness therefore lives here at run time, seeded by the target
date so re-running for the same day is reproducible.

It is also self-contained rather than chained: the layer generators pass a
`prev_output` dict down the pipeline (business_id, coworker_ids, etc.), but
this script may run standalone, long after that one-time seed run. So it
resolves its own context live — querying for the business, seeded coworkers
(by test email domain), their active contracts, and resources — rather than
requiring the full Layer 0-4 chain to have just run in the same process.

What it creates per run (§5):
- CheckIn: 8-15 active members check in 07:30-10:00, ~3 left open.
- Booking: 3-6 bookings for today on random resources.
- Visitor: 2-4 guests arriving today, mix hosted/walk-in.
- CoworkerDelivery: 1-3 mail/parcel arrivals, left pending.

Before creating today's records it also:
- Closes yesterday's open check-ins (ToTime null) with a 16:00-18:30 checkout.
- Marks ~50% of yesterday's pending deliveries as collected.

Usage:
    python generators/daily_update.py              # Today's records
    python generators/daily_update.py --days 7      # Backfill the last 7 days
    python generators/daily_update.py --date 2026-08-01
    python generators/daily_update.py --dry-run
"""

import argparse
import random
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipeline
from generators.base import BaseGenerator
from config import TEST_EMAIL_DOMAIN, TEST_NAME_PREFIX, to_utc_str

DELIVERY_NAMES = ["Amazon Parcel", "Bank Statement", "Office Supplies Box", "Client Payment Check"]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--date", type=str, help="Specific date, YYYY-MM-DD (default: today)")
    parser.add_argument("--days", type=int, default=1, help="Backfill this many days ending at --date/today")
    parser.add_argument("--business-id", type=int, default=None,
                         help="Which business/location to run against, if this login has access to more than one")
    return parser.parse_args()


class DailyUpdateGenerator(BaseGenerator):
    entity_name = "daily"

    def __init__(self, target_date, **kwargs):
        super().__init__(**kwargs)
        self.target_date = target_date
        # Date-seeded RNG: reruns for the same day are reproducible.
        self.rng = random.Random(int(target_date.strftime("%Y%m%d")))

    def run(self, nexudus_list, nexudus_create, nexudus_update, context):
        biz = context["business_id"]
        coworkers = context["active_coworkers"]  # [{"Id":..., "Email":...}, ...]
        visitors_seed = context["visitor_pool"]   # unregistered ad-hoc guest name/email pairs
        resource_ids = context["resource_ids"]    # name -> id

        self._close_yesterdays_open_checkins(biz, nexudus_list, nexudus_update)
        self._collect_some_pending_deliveries(biz, nexudus_list, nexudus_update)

        if not coworkers:
            # Every entity created below needs a CoworkerId — if filtering
            # to Active test-domain coworkers left nothing (all archived,
            # or none seeded yet), there's genuinely nothing valid to
            # attach today's records to. Skip cleanly instead of crashing
            # on whichever call happens to run first.
            self.log.warning(
                "No active test-domain coworkers found — skipping today's "
                "check-ins, bookings, visitors, and deliveries.", skip=True)
            return

        self._create_checkins(biz, coworkers, context.get("day_pass_id"), nexudus_list, nexudus_create, nexudus_update)
        self._create_bookings(coworkers, resource_ids, nexudus_create)
        self._create_visitors(biz, coworkers, visitors_seed, nexudus_create)
        self._create_deliveries(biz, coworkers, nexudus_create)

        self.log.info("Daily update complete for %s.", self.target_date.isoformat())

    # ------------------------------------------------------------------
    # Date helpers
    # ------------------------------------------------------------------

    def _at(self, hour, minute=0, day_delta=0):
        d = self.target_date + timedelta(days=day_delta)
        return to_utc_str(d, hour=hour, minute=minute)

    # ------------------------------------------------------------------
    # Close yesterday's open check-ins
    # ------------------------------------------------------------------
    def _close_yesterdays_open_checkins(self, biz, nexudus_list, nexudus_update):
        yesterday = self.target_date - timedelta(days=1)
        self.log.info("--- Closing open check-ins from %s ---", yesterday.isoformat())

        open_checkins = nexudus_list("checkins", {
            "Checkin_Business": biz,
            "Checkin_FromTime": to_utc_str(yesterday),
        })
        open_checkins = [c for c in open_checkins if not c.get("ToTime")]

        for c in open_checkins:
            checkout_hour = self.rng.randint(16, 18)
            checkout_minute = self.rng.choice([0, 15, 30, 45])
            body = {"ToTime": self._at(checkout_hour, checkout_minute, day_delta=-1)}
            if self.dry_run:
                self.log.info("WOULD UPDATE checkins %s: %s", c.get("Id"), body)
            else:
                nexudus_update("checkins", c["Id"], body)
                self.log.info("Closed check-in %s", c["Id"])

    # ------------------------------------------------------------------
    # Collect ~50% of yesterday's pending deliveries
    # ------------------------------------------------------------------
    def _collect_some_pending_deliveries(self, biz, nexudus_list, nexudus_update):
        self.log.info("--- Collecting some of yesterday's pending deliveries ---")

        pending = nexudus_list("coworkerdeliveries", {
            "CoworkerDelivery_Business": biz,
            "CoworkerDelivery_Collected": "false",
        })
        to_collect = self.rng.sample(pending, k=len(pending) // 2) if pending else []

        for d in to_collect:
            body = {"Collected": True, "CollectedOn": self._at(self.rng.randint(9, 17))}
            if self.dry_run:
                self.log.info("WOULD UPDATE coworkerdeliveries %s: %s", d.get("Id"), body)
            else:
                nexudus_update("coworkerdeliveries", d["Id"], body)
                self.log.info("Collected delivery %s", d["Id"])

    # ------------------------------------------------------------------
    # Today's check-ins
    # ------------------------------------------------------------------
    def _create_checkins(self, biz, coworkers, day_pass_id, nexudus_list, nexudus_create, nexudus_update):
        count = self.rng.randint(8, 15)
        self.log.info("--- Today's Check-ins (%d) ---", count)

        already_today = nexudus_list("checkins", {
            "Checkin_Business": biz, "Checkin_FromTime": to_utc_str(self.target_date),
        })
        if already_today:
            self.log.info("Check-ins already exist for %s (%d found) — skipping",
                          self.target_date.isoformat(), len(already_today))
            self.count_skip()
            return

        chosen = self.rng.sample(coworkers, k=min(count, len(coworkers)))
        open_count = min(3, len(chosen))

        for i, cw in enumerate(chosen):
            hour = self.rng.randint(7, 9)
            minute = self.rng.choice([0, 15, 30, 45]) if hour > 7 else self.rng.choice([30, 45])
            body = {
                "BusinessId": biz,
                "CoworkerId": cw["Id"],
                "FromTime": self._at(hour, minute),
                "Source": self.rng.choices([1, 2, 3], weights=[0.3, 0.5, 0.2], k=1)[0],
            }
            if i >= open_count:
                checkout_hour = self.rng.randint(16, 19)
                body["ToTime"] = self._at(checkout_hour, self.rng.choice([0, 15, 30, 45]))

            if self.dry_run:
                self.log_would_create("checkins", body)
                continue

            # An active contract doesn't always imply access on its own —
            # same "no valid time pass" pattern already handled in
            # 04_activity.py::_create_checkins. On that specific failure,
            # grant a Day Pass covering today and retry once instead of
            # crashing the whole daily run over one coworker.
            try:
                result = nexudus_create("checkins", body)
            except Exception as e:  # noqa: BLE001
                if "does not have a valid time pass" not in str(e) or day_pass_id is None:
                    self.log.warning("Skipping check-in for coworker %s — create failed: %s",
                                      cw["Id"], e, skip=True)
                    continue
                pass_guid = self._grant_day_pass(biz, cw["Id"], day_pass_id, nexudus_create, nexudus_update)
                if pass_guid is None:
                    self.log.warning("Skipping check-in for coworker %s — could not grant a covering day pass",
                                      cw["Id"], skip=True)
                    continue
                body["CoworkerTimePassGuid"] = pass_guid
                try:
                    result = nexudus_create("checkins", body)
                except Exception as e2:  # noqa: BLE001
                    self.log.warning("Skipping check-in for coworker %s — still failed after granting a day pass: %s",
                                      cw["Id"], e2, skip=True)
                    continue

            self.log.info("Created check-in (id=%s)", result["Id"])
            self.count_create()

    def _grant_day_pass(self, biz, coworker_id, day_pass_id, nexudus_create, nexudus_update):
        """Create + mark-used a CoworkerTimePass covering today, for a
        check-in that couldn't rely on implicit contract access — mirrors
        04_activity.py::_grant_day_pass. Doesn't track_id() the pass
        (this generator deliberately doesn't track IDs at all, see the
        module docstring), but does count it as a real created record."""
        body = {
            "CoworkerId": coworker_id,
            "BusinessId": biz,
            "TimePassId": day_pass_id,
            "CreateMultiple": 1,
            "ExpireDate": self._at(0, day_delta=1),
        }
        try:
            result = nexudus_create("coworkertimepasses", body)
        except Exception as e:  # noqa: BLE001
            self.log.warning("Failed to grant a day pass to coworker %s: %s", coworker_id, e)
            return None
        self.count_create()

        try:
            nexudus_update("coworkertimepasses", result["Id"], {
                "Used": True,
                "UsedDate": self._at(0),
            })
        except Exception as e:  # noqa: BLE001
            self.log.warning("Granted day pass %s but failed to mark it used: %s", result["Id"], e)
        return result.get("UniqueId")

    # ------------------------------------------------------------------
    # Today's bookings
    # ------------------------------------------------------------------
    def _create_bookings(self, coworkers, resource_ids, nexudus_create):
        count = self.rng.randint(3, 6)
        self.log.info("--- Today's Bookings (%d) ---", count)

        resource_items = list(resource_ids.items())
        for _ in range(count):
            cw = self.rng.choice(coworkers)
            _name, resource_id = self.rng.choice(resource_items)
            hour = self.rng.randint(8, 17)
            duration_minutes = self.rng.choice([30, 60, 120])
            from_time_dt = datetime.fromisoformat(self._at(hour).replace("Z", "+00:00"))
            body = {
                "ResourceId": resource_id,
                "CoworkerId": cw["Id"],
                "FromTime": self._at(hour),
                "ToTime": to_utc_str(from_time_dt + timedelta(minutes=duration_minutes)),
                "Repeats": 1,
                "Tentative": False,
            }

            if self.dry_run:
                self.log_would_create("bookings", body)
            else:
                result = nexudus_create("bookings", body)
                self.log.info("Created booking (id=%s)", result["Id"])
                self.count_create()

    # ------------------------------------------------------------------
    # Today's visitors
    # ------------------------------------------------------------------
    def _create_visitors(self, biz, coworkers, visitors_seed, nexudus_create):
        count = self.rng.randint(2, 4)
        self.log.info("--- Today's Visitors (%d) ---", count)

        for i in range(count):
            hosted = self.rng.random() < 0.7
            full_name, email = self.rng.choice(visitors_seed)
            body = {
                "BusinessId": biz,
                "FullName": full_name,
                "Email": f"{email}-{self.target_date.isoformat()}-{i}@{TEST_EMAIL_DOMAIN}",
                "VisitorSource": self.rng.choice([1, 2, 3]),
                "HostApprovalStatus": 5 if hosted else 1,
                "ExpectedArrival": self._at(self.rng.randint(8, 17)),
            }
            if hosted:
                body["CoworkerId"] = self.rng.choice(coworkers)["Id"]

            if self.dry_run:
                self.log_would_create("visitors", body)
            else:
                result = nexudus_create("visitors", body)
                self.log.info("Created visitor (id=%s)", result["Id"])
                self.count_create()

    # ------------------------------------------------------------------
    # Today's deliveries
    # ------------------------------------------------------------------
    def _create_deliveries(self, biz, coworkers, nexudus_create):
        count = self.rng.randint(1, 3)
        self.log.info("--- Today's Deliveries (%d) ---", count)

        for _ in range(count):
            cw = self.rng.choice(coworkers)
            body = {
                "BusinessId": biz,
                "CoworkerId": cw["Id"],
                "Name": self.rng.choice(DELIVERY_NAMES),
                "Location": "Reception",
                "DeliveryType": self.rng.choice([1, 2]),
                "HandlingPreference": 1,
            }

            if self.dry_run:
                self.log_would_create("coworkerdeliveries", body)
            else:
                result = nexudus_create("coworkerdeliveries", body)
                self.log.info("Created delivery (id=%s)", result["Id"])
                self.count_create()


def resolve_context(nexudus_list, business_id=None):
    """Live-query business/coworker/resource context — no prev_output chain here.

    business_id picks which business (location) to run against, for logins
    with access to more than one — see pipeline._select_business. Left None,
    that resolves the normal way: the account's one business, or a loud
    SystemExit listing the options if there's more than one and none was
    given, same as every other entry point (CLAUDE.md rule 8). This used to
    always silently pick businesses[0] regardless — harmless on a
    single-business login, but a real trap the moment a caller (e.g. the web
    control panel's location selector) expects the chosen business to
    actually be honored here too.
    """
    businesses = nexudus_list("businesses", {})
    business_id = pipeline._select_business(businesses, business_id)["Id"]

    # Coworker_Email is an exact-match filter, not a substring/contains —
    # filtering by "@{domain}" always returns zero results. List every
    # coworker and filter client-side by domain instead.
    #
    # Also filter to Active coworkers only. Repeated reseed cycles on the
    # same account can leave multiple coworker records behind for the same
    # test email (confirmed live: 127 test-domain records for only 60
    # unique emails on this account) — some active, some archived/inactive
    # duplicates from an earlier cycle. Nexudus rejects creating a checkin/
    # booking/delivery for an inactive coworker with a generic "This
    # account is disabled" error, which previously surfaced as a raw
    # traceback on whichever record happened to be picked. Filtering here
    # means only genuinely usable coworkers are ever selected.
    # Scoped to this business as well as to Active: the list itself is
    # unfiltered, so on a multi-business login (rule 8) it also returns
    # coworkers this run can't write children against. The Active check
    # additionally screens out most of the deleted-but-still-listed records
    # described in 02_people.py::_coworker_is_usable — most, not all: one of
    # the five found live read Active=True and still failed a GET, so a
    # picked coworker can in principle still be a ghost. It surfaces as a
    # 401 "Access Denied." on whichever record referenced it.
    all_coworkers = nexudus_list("coworkers", {})
    active_coworkers = [
        {"Id": c["Id"], "Email": c.get("Email")} for c in all_coworkers
        if c.get("Email", "").endswith(f"@{TEST_EMAIL_DOMAIN}") and c.get("Active")
        and str(c.get("InvoicingBusinessId")) == str(business_id)
    ]

    resources = nexudus_list("resources", {"Resource_Business": business_id})
    resource_ids = {r["Name"]: r["Id"] for r in resources}

    # Resolved for _grant_day_pass's "no valid time pass" fallback — see
    # _create_checkins. None if this business has no "Day Pass" time pass
    # (e.g. 01_structural.py hasn't been run yet); the fallback is then
    # simply unavailable and a blocked check-in gets skipped instead.
    timepasses = nexudus_list("timepasses", {"TimePass_Business": business_id})
    day_pass_id = next(
        (t["Id"] for t in timepasses if t.get("Name") == f"{TEST_NAME_PREFIX}Day Pass"), None)

    visitor_pool = [
        ("Alex Morgan", "alex.morgan.guest"), ("Jamie Chen", "jamie.chen.guest"),
        ("Priya Patel", "priya.patel.guest"), ("Sam Okafor", "sam.okafor.guest"),
        ("Taylor Reed", "taylor.reed.guest"),
    ]

    return {
        "business_id": business_id,
        "active_coworkers": active_coworkers,
        "resource_ids": resource_ids,
        "day_pass_id": day_pass_id,
        "visitor_pool": visitor_pool,
    }


if __name__ == "__main__":
    args = parse_args()

    if args.date:
        end_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        end_date = date.today()

    dates = [end_date - timedelta(days=i) for i in range(args.days - 1, -1, -1)]

    if args.dry_run:
        mock_context = {
            "business_id": "DRY-BIZ-1",
            "active_coworkers": [{"Id": f"DRY-CW-{i}", "Email": f"test-{i:03d}@seeddata.local"} for i in range(1, 61)],
            "resource_ids": {"Boardroom Alpha": "DRY-RES-1", "Hot Desk Area A": "DRY-RES-2"},
            "day_pass_id": "DRY-TP-Day Pass",
            "visitor_pool": [("Alex Morgan", "alex.morgan.guest"), ("Jamie Chen", "jamie.chen.guest")],
        }

        def _mock_list(entity, filters):
            return []  # no open check-ins / pending deliveries to close in dry-run

        for d in dates:
            gen = DailyUpdateGenerator(target_date=d, dry_run=True)
            gen.run(
                nexudus_list=_mock_list,
                nexudus_create=lambda entity, body: {"Id": f"DRY-{entity}-x"},
                nexudus_update=lambda entity, id, body: {"Id": id},
                context=mock_context,
            )
            print(gen.summary_line())
    else:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import nexudus_client as client

        context = resolve_context(client.nexudus_list, business_id=args.business_id)
        for d in dates:
            gen = DailyUpdateGenerator(target_date=d, dry_run=False)
            gen.run(
                nexudus_list=client.nexudus_list,
                nexudus_create=client.nexudus_create,
                nexudus_update=client.nexudus_update,
                context=context,
            )
            print(gen.summary_line())
