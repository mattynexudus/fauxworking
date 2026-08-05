"""
Layer 4a — Activity

Reads pre-generated definitions from data/bookings.json + siblings, then
pushes them to the Nexudus API.

Creates:
- Booking (240: 10 recurring + 230 one-off), with inline BookingProducts
  (~36) at create time, followed by standalone BookingVisitor guest links
  (~50) once each booking has an Id.
- Booking cancellations (40) — delete the flagged bookings after their
  products/guests exist; Nexudus auto-generates the CancelledBooking
  snapshot (§4j). Recurring bookings are never in the to-cancel set.
- CheckIn (300) — ~5 left open (no ToTime).
- CoworkerExtraService (80) — 47 per-booking charges + 25 time credits +
  8 printing credits (§4e).
- CoworkerBookingCredit (25) + CoworkerBookingCreditUseHistory (50) — §4e.
- CoworkerTimePass (40) — 15 unused, 20 marked Used via a follow-up
  update (Used is updateOnly), 5 expiring soon.
- CoworkerProduct (20) — standalone recurring product subscriptions.

Prerequisites: Layer 0 (business), Layer 1 (resources, extra services,
products, time passes), Layer 2 (coworkers, visitors).
Data files: Run `python prebuild.py` first to generate data/*.json.

Usage:
    python generators/04_activity.py              # Live mode
    python generators/04_activity.py --dry-run     # Log only
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.base import BaseGenerator, parse_args
from config import DATA_DIR, TODAY, to_utc_str


class ActivityGenerator(BaseGenerator):
    entity_name = "activity"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.booking_ids = {}   # BookingIndex -> Id
        self.credit_ids = {}    # CreditIndex -> Id

        self.booking_defs = self._load_data("bookings.json")
        self.checkin_defs = self._load_data("checkins.json")
        self.extra_service_defs = self._load_data("extra_services.json")
        self.booking_credit_defs = self._load_data("booking_credits.json")
        self.credit_use_history_defs = self._load_data("credit_use_history.json")
        self.time_pass_defs = self._load_data("time_passes.json")
        self.coworker_product_defs = self._load_data("coworker_products.json")

    @staticmethod
    def _load_data(filename):
        path = DATA_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"{path} not found. Run 'python prebuild.py' first.")
        return json.loads(path.read_text())

    def run(self, nexudus_list, nexudus_create, nexudus_update, nexudus_delete, prev_output):
        biz = prev_output["business_id"]
        admin_id = prev_output["admin_user_id"]
        coworker_ids = prev_output["coworker_ids"]
        visitor_ids = prev_output["visitor_ids"]
        resource_ids = prev_output["resource_ids"]
        extra_service_ids = prev_output["extra_service_ids"]
        product_ids = prev_output["product_ids"]
        time_pass_ids = prev_output["time_pass_ids"]

        self._create_bookings(biz, admin_id, coworker_ids, resource_ids, product_ids, nexudus_create)
        self._create_booking_guests(coworker_ids, visitor_ids, nexudus_create)
        self._cancel_bookings(nexudus_delete)
        self._create_checkins(biz, coworker_ids, nexudus_create)
        self._create_extra_services(biz, coworker_ids, extra_service_ids, nexudus_create)
        self._create_booking_credits(biz, coworker_ids, nexudus_create)
        self._create_credit_use_history(nexudus_create)
        self._create_time_passes(biz, coworker_ids, time_pass_ids, nexudus_create, nexudus_update)
        self._create_coworker_products(biz, coworker_ids, product_ids, nexudus_create)

        self.log.info("Layer 4a complete. Bookings: %d", len(self.booking_ids))

        return {**prev_output, "booking_ids": self.booking_ids}

    # ------------------------------------------------------------------
    # Date helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _at(day_offset, hour=0, minute=0):
        d = TODAY + timedelta(days=day_offset)
        return to_utc_str(d, hour=hour, minute=minute)

    # ------------------------------------------------------------------
    # Bookings (+ inline BookingProducts)
    # ------------------------------------------------------------------
    def _create_bookings(self, biz, admin_id, coworker_ids, resource_ids, product_ids, nexudus_create):
        self.log.info("--- Bookings (%d) ---", len(self.booking_defs))

        for defn in self.booking_defs:
            idx = defn["index"]
            track_key = str(idx)

            if self.already_created("BookingIndex", track_key):
                existing = next(r for r in self.get_tracked_ids()
                                 if r.get("entity") == "bookings" and r.get("BookingIndex") == track_key)
                self.booking_ids[idx] = existing["Id"]
                continue

            from_time = self._at(defn["StartDayOffset"], hour=defn["FromHour"])
            to_time = to_utc_str(
                datetime.fromisoformat(from_time.replace("Z", "+00:00"))
                + timedelta(minutes=defn["DurationMinutes"])
            )

            body = {
                "ResourceId": resource_ids[defn["ResourceName"]],
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "FromTime": from_time,
                "ToTime": to_time,
                "Tentative": defn["Tentative"],
                # Bill this booking's own coworker rather than their team's payer —
                # the resulting charge is swept into that coworker's next invoice
                # by 06_financial.py's COWORKER_BILL_RUN, alongside their plan fee.
                "InvoiceThisCoworker": True,
            }
            if defn["Repeats"]:
                body["RepeatBooking"] = True
                body["Repeats"] = defn["Repeats"]
                body["RepeatEvery"] = defn["RepeatEvery"]
                body["RepeatUntil"] = self._at(defn["RepeatUntilDayOffset"])
            if defn["AdminBooked"]:
                body["BookedByAdminUserId"] = admin_id
            if defn["DiscountCode"]:
                body["DiscountCode"] = defn["DiscountCode"]
            if defn["BookingProducts"]:
                body["BookingProducts"] = [
                    {"ProductId": product_ids[p["ProductName"]], "Quantity": p["Quantity"]}
                    for p in defn["BookingProducts"]
                ]

            if self.dry_run:
                self.log_would_create("bookings", body)
                self.booking_ids[idx] = f"DRY-BOOKING-{idx}"
            else:
                result = nexudus_create("bookings", body)
                self.booking_ids[idx] = result["Id"]
                self.track_id({
                    "entity": "bookings", "Id": result["Id"], "BookingIndex": track_key,
                    "ToCancel": defn["ToCancel"],
                })
                self.log.info("Created booking #%d on '%s' (id=%s)",
                              idx, defn["ResourceName"], result["Id"])

    # ------------------------------------------------------------------
    # BookingVisitor guests (standalone — inline requires resolving a
    # Visitor from name/email; we already have real Visitor records from
    # Layer 2, so link them directly by VisitorId)
    # ------------------------------------------------------------------
    def _create_booking_guests(self, coworker_ids, visitor_ids, nexudus_create):
        self.log.info("--- Booking Guests ---")

        for defn in self.booking_defs:
            if not defn["GuestVisitorIndices"]:
                continue

            booking_id = self.booking_ids.get(defn["index"])
            for guest_idx, visitor_idx in enumerate(defn["GuestVisitorIndices"]):
                track_key = f"{defn['index']}:{guest_idx}"
                if self.already_created("GuestKey", track_key):
                    continue

                body = {"BookingId": booking_id, "VisitorId": visitor_ids[visitor_idx]}

                if self.dry_run:
                    self.log_would_create("bookingvisitors", body)
                else:
                    result = nexudus_create("bookingvisitors", body)
                    self.track_id({
                        "entity": "bookingvisitors", "Id": result["Id"], "GuestKey": track_key,
                    })
                    self.log.info("Linked visitor #%d to booking #%d (id=%s)",
                                  visitor_idx, defn["index"], result["Id"])

    # ------------------------------------------------------------------
    # Cancel bookings — delete after all children exist; system creates
    # the CancelledBooking snapshot automatically (§4j).
    # ------------------------------------------------------------------
    def _cancel_bookings(self, nexudus_delete):
        to_cancel = [d for d in self.booking_defs if d["ToCancel"]]
        self.log.info("--- Cancelling Bookings (%d) ---", len(to_cancel))

        for defn in to_cancel:
            idx = defn["index"]
            track_key = str(idx)
            if self.already_created("CancelledBookingIndex", track_key):
                continue

            booking_id = self.booking_ids.get(idx)
            if booking_id is None:
                continue

            if self.dry_run:
                self.log.info("WOULD DELETE bookings %s [%s]", booking_id, defn["CancellationCategory"])
            else:
                nexudus_delete("bookings", booking_id)
                self.track_id({
                    "entity": "cancelledbookings", "Id": booking_id,
                    "CancelledBookingIndex": track_key, "Category": defn["CancellationCategory"],
                })
                self.log.info("Cancelled booking #%d [%s] (id=%s)",
                              idx, defn["CancellationCategory"], booking_id)

    # ------------------------------------------------------------------
    # CheckIns
    # ------------------------------------------------------------------
    def _create_checkins(self, biz, coworker_ids, nexudus_create):
        self.log.info("--- Check-ins (%d) ---", len(self.checkin_defs))

        for defn in self.checkin_defs:
            track_key = str(defn["index"])
            if self.already_created("CheckinIndex", track_key):
                continue

            from_time = self._at(defn["FromDayOffset"], hour=defn["FromHour"], minute=defn["FromMinute"])
            body = {
                "BusinessId": biz,
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "FromTime": from_time,
                "Source": defn["Source"],
            }
            if not defn["Open"]:
                from_dt = datetime.fromisoformat(from_time.replace("Z", "+00:00"))
                to_dt = from_dt + timedelta(hours=defn["DurationHours"])
                body["ToTime"] = to_utc_str(to_dt)

            if self.dry_run:
                self.log_would_create("checkins", body)
            else:
                result = nexudus_create("checkins", body)
                self.track_id({"entity": "checkins", "Id": result["Id"], "CheckinIndex": track_key})
                self.log.info("Created check-in #%d (id=%s)", defn["index"], result["Id"])

    # ------------------------------------------------------------------
    # CoworkerExtraService (booking charges + time/printing credits)
    # ------------------------------------------------------------------
    def _create_extra_services(self, biz, coworker_ids, extra_service_ids, nexudus_create):
        self.log.info("--- Extra Services (%d) ---", len(self.extra_service_defs))

        for defn in self.extra_service_defs:
            track_key = str(defn["index"])
            if self.already_created("ExtraServiceIndex", track_key):
                continue

            body = {
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "BusinessId": biz,
                "ExtraServiceId": extra_service_ids[defn["ExtraServiceName"]],
                "TotalUses": defn["TotalUses"],
                "ChargePeriod": defn["ChargePeriod"],
                "InvoiceThisCoworker": True,
            }
            if defn["Price"] is not None:
                body["Price"] = defn["Price"]
            if defn["BookingIndex"] is not None:
                booking_id = self.booking_ids.get(defn["BookingIndex"])
                if booking_id is not None:
                    body["BookingId"] = booking_id
            if defn["ExpireDateDayOffset"] is not None:
                body["ExpireDate"] = self._at(defn["ExpireDateDayOffset"])

            if self.dry_run:
                self.log_would_create("coworkerextraservices", body)
            else:
                result = nexudus_create("coworkerextraservices", body)
                self.track_id({
                    "entity": "coworkerextraservices", "Id": result["Id"],
                    "ExtraServiceIndex": track_key, "Kind": defn["Kind"],
                })
                self.log.info("Created extra service #%d [%s] (id=%s)",
                              defn["index"], defn["Kind"], result["Id"])

    # ------------------------------------------------------------------
    # CoworkerBookingCredit
    # ------------------------------------------------------------------
    def _create_booking_credits(self, biz, coworker_ids, nexudus_create):
        self.log.info("--- Booking Credits (%d) ---", len(self.booking_credit_defs))

        for defn in self.booking_credit_defs:
            idx = defn["index"]
            track_key = str(idx)
            if self.already_created("CreditIndex", track_key):
                existing = next(r for r in self.get_tracked_ids()
                                 if r.get("entity") == "coworkerbookingcredits" and r.get("CreditIndex") == track_key)
                self.credit_ids[idx] = existing["Id"]
                continue

            body = {
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "BusinessId": biz,
                "TotalCredit": defn["TotalCredit"],
                "ValidFrom": self._at(defn["ValidFromDayOffset"]),
                "ExpireDate": self._at(defn["ExpireDateDayOffset"]),
                "CaneBeUsedForBookings": True,  # sic — API typo, see entity guide
            }

            if self.dry_run:
                self.log_would_create("coworkerbookingcredits", body)
                self.credit_ids[idx] = f"DRY-CREDIT-{idx}"
            else:
                result = nexudus_create("coworkerbookingcredits", body)
                self.credit_ids[idx] = result["Id"]
                self.track_id({
                    "entity": "coworkerbookingcredits", "Id": result["Id"],
                    "CreditIndex": track_key, "Bucket": defn["Bucket"],
                })
                self.log.info("Created booking credit #%d [%s] (id=%s)",
                              idx, defn["Bucket"], result["Id"])

    # ------------------------------------------------------------------
    # CoworkerBookingCreditUseHistory
    # ------------------------------------------------------------------
    def _create_credit_use_history(self, nexudus_create):
        self.log.info("--- Credit Use History (%d) ---", len(self.credit_use_history_defs))

        for defn in self.credit_use_history_defs:
            track_key = str(defn["index"])
            if self.already_created("UseHistoryIndex", track_key):
                continue

            credit_id = self.credit_ids.get(defn["CreditIndex"])
            if credit_id is None:
                continue

            body = {"CoworkerBookingCreditId": credit_id, "CreditUsed": defn["CreditUsed"]}
            if defn["BookingIndex"] is not None:
                booking_id = self.booking_ids.get(defn["BookingIndex"])
                if booking_id is not None:
                    body["BookingId"] = booking_id

            if self.dry_run:
                self.log_would_create("coworkerbookingcreditusehistories", body)
            else:
                result = nexudus_create("coworkerbookingcreditusehistories", body)
                self.track_id({
                    "entity": "coworkerbookingcreditusehistories", "Id": result["Id"],
                    "UseHistoryIndex": track_key,
                })
                self.log.info("Created credit use #%d on credit #%d (id=%s)",
                              defn["index"], defn["CreditIndex"], result["Id"])

    # ------------------------------------------------------------------
    # CoworkerTimePass (+ follow-up update to mark Used)
    # ------------------------------------------------------------------
    def _create_time_passes(self, biz, coworker_ids, time_pass_ids, nexudus_create, nexudus_update):
        self.log.info("--- Time Passes (%d) ---", len(self.time_pass_defs))

        for defn in self.time_pass_defs:
            track_key = str(defn["index"])
            if self.already_created("TimePassIndex", track_key):
                continue

            body = {
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "BusinessId": biz,
                "TimePassId": time_pass_ids[defn["TimePassName"]],
                "CreateMultiple": 1,
                "ExpireDate": self._at(defn["ExpireDateDayOffset"]),
            }

            if self.dry_run:
                self.log_would_create("coworkertimepasses", body)
                if defn["Status"] == "used":
                    self.log.info("WOULD UPDATE coworkertimepasses DRY: Used=true")
            else:
                result = nexudus_create("coworkertimepasses", body)
                self.track_id({
                    "entity": "coworkertimepasses", "Id": result["Id"],
                    "TimePassIndex": track_key, "Status": defn["Status"],
                })
                self.log.info("Created time pass #%d [%s] (id=%s)",
                              defn["index"], defn["Status"], result["Id"])

                if defn["Status"] == "used":
                    nexudus_update("coworkertimepasses", result["Id"], {
                        "Used": True,
                        "UsedDate": self._at(defn["UsedDateDayOffset"]),
                    })
                    self.log.info("Marked time pass #%d as used", defn["index"])

    # ------------------------------------------------------------------
    # CoworkerProduct
    # ------------------------------------------------------------------
    def _create_coworker_products(self, biz, coworker_ids, product_ids, nexudus_create):
        self.log.info("--- Coworker Products (%d) ---", len(self.coworker_product_defs))

        for defn in self.coworker_product_defs:
            track_key = str(defn["index"])
            if self.already_created("CoworkerProductIndex", track_key):
                continue

            body = {
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "BusinessId": biz,
                "ProductId": product_ids[defn["ProductName"]],
                "Quantity": defn["Quantity"],
                "RepeatCycle": defn["RepeatCycle"],
                "InvoiceThisCoworker": True,
                "CreditAmount": 0,
                "DiscountAmount": 0,
            }

            if self.dry_run:
                self.log_would_create("coworkerproducts", body)
            else:
                result = nexudus_create("coworkerproducts", body)
                self.track_id({
                    "entity": "coworkerproducts", "Id": result["Id"],
                    "CoworkerProductIndex": track_key,
                })
                self.log.info("Created coworker product #%d (id=%s)", defn["index"], result["Id"])


if __name__ == "__main__":
    args = parse_args()
    gen = ActivityGenerator(dry_run=args.dry_run)

    if gen.dry_run:
        import importlib
        struct = importlib.import_module("generators.01_structural")

        mock_coworker_ids = {i: f"DRY-CW-{i}" for i in range(1, 61)}
        mock_visitor_ids = {i: f"DRY-VIS-{i}" for i in range(1, 61)}
        mock_resource_ids = {r["Name"]: f"DRY-RES-{r['Name']}" for r in struct.RESOURCES}
        mock_extra_service_ids = {e["Name"]: f"DRY-ES-{e['Name']}" for e in struct.EXTRA_SERVICES}
        mock_product_ids = {p["Name"]: f"DRY-PROD-{p['Name']}" for p in struct.PRODUCTS}
        mock_time_pass_ids = {t["Name"]: f"DRY-TP-{t['Name']}" for t in struct.TIME_PASSES}

        mock_prev = {
            "business_id": "DRY-BIZ-1",
            "admin_user_id": "DRY-ADMIN-1",
            "coworker_ids": mock_coworker_ids,
            "visitor_ids": mock_visitor_ids,
            "resource_ids": mock_resource_ids,
            "extra_service_ids": mock_extra_service_ids,
            "product_ids": mock_product_ids,
            "time_pass_ids": mock_time_pass_ids,
        }

        _counter = {"n": 0}

        def _mock_create(entity, body):
            _counter["n"] += 1
            return {"Id": f"DRY-{entity}-{_counter['n']}"}

        gen.run(
            nexudus_list=lambda entity, filters: [],
            nexudus_create=_mock_create,
            nexudus_update=lambda entity, id, body: {"Id": id},
            nexudus_delete=lambda entity, id: None,
            prev_output=mock_prev,
        )
    else:
        print("Live mode requires MCP context. Run via agent or use --dry-run.")
        sys.exit(1)
