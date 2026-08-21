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

# eBookingCancellationReason — confirmed live that a raw DELETE always
# leaves CancelledBooking.CancellationReason at 0 (Unknown); the
# CANCEL_BOOKING command's "Cancellation Reason" parameter is what
# actually sets it, matching our CancellationCategory labels.
CANCELLATION_REASON_MAP = {
    "no_longer_needed": 1,   # NoLongerNeeded
    "cost_concerns": 2,      # TooExpensive
    "rebooked": 4,           # RebookedForADifferentTime
    "failed_to_pay": 5,      # FailedToPayUpfront
    "no_show": 8,            # NotCheckedIn
}


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

        self.set_target("bookings", len(self.booking_defs))
        self.set_target("bookingvisitors",
                         sum(len(d["GuestVisitorIndices"]) for d in self.booking_defs))
        self.set_target("cancelledbookings", sum(1 for d in self.booking_defs if d["ToCancel"]))
        self.set_target("checkins", len(self.checkin_defs))
        self.set_target("coworkerextraservices", len(self.extra_service_defs))
        self.set_target("coworkerbookingcredits", len(self.booking_credit_defs))
        self.set_target("coworkerbookingcreditusehistories", len(self.credit_use_history_defs))
        self.set_target("coworkertimepasses", len(self.time_pass_defs))
        self.set_target("coworkerproducts", len(self.coworker_product_defs))

    @staticmethod
    def _load_data(filename):
        path = DATA_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"{path} not found. Run 'python prebuild.py' first.")
        return json.loads(path.read_text())

    def run(self, nexudus_list, nexudus_create, nexudus_update, nexudus_run_command, prev_output):
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
        self._cancel_bookings(nexudus_run_command)
        self._create_checkins(biz, coworker_ids, time_pass_ids, nexudus_create, nexudus_update)
        self._create_extra_services(biz, coworker_ids, extra_service_ids, nexudus_create, nexudus_run_command, nexudus_list)
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

            if self.already_created("BookingIndex", track_key, entity="bookings"):
                existing = next(r for r in self.get_tracked_ids()
                                 if r.get("entity") == "bookings" and r.get("BookingIndex") == track_key)
                self.booking_ids[idx] = existing["Id"]
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping booking #%d — coworker #%d was never created (seat limit?)",
                                  idx, defn["CoworkerIndex"],
                                  skip=True, entity="bookings", reason="parent_skipped")
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
                try:
                    result = nexudus_create("bookings", body)
                except Exception as e:  # noqa: BLE001
                    if "already booked" in str(e):
                        self.log.warning(
                            "Skipping booking #%d on '%s' — resource conflict with "
                            "another seeded booking (no conflict-checking in prebuild "
                            "data generation): %s", idx, defn["ResourceName"], e,
                            skip=True, entity="bookings", reason="validation_rejected")
                        continue
                    verdict = self.classify_failure("bookings", e)
                    if verdict == "systemic":
                        self.log.warning(
                            "Stopping booking creation — this error has repeated several "
                            "times in a row, likely an account-wide condition: %s", e,
                            skip=True, entity="bookings", reason="systemic_rate_limit")
                        break
                    self.log.warning("Skipping booking #%d — create failed: %s", idx, e,
                                      skip=True, entity="bookings", reason="unknown_error")
                    continue
                self.booking_ids[idx] = result["Id"]
                self.track_id({
                    "entity": "bookings", **result, "BookingIndex": track_key,
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
                if self.already_created("GuestKey", track_key, entity="bookingvisitors"):
                    continue

                if booking_id is None:
                    self.log.warning("Skipping booking guest #%s — booking #%d was never created "
                                      "(seat limit or resource conflict?)",
                                      track_key, defn["index"],
                                      skip=True, entity="bookingvisitors", reason="parent_skipped")
                    continue

                if visitor_idx not in visitor_ids:
                    self.log.warning("Skipping booking guest #%s — visitor #%s was never created",
                                      track_key, visitor_idx,
                                      skip=True, entity="bookingvisitors", reason="parent_skipped")
                    continue

                body = {"BookingId": booking_id, "VisitorId": visitor_ids[visitor_idx]}

                if self.dry_run:
                    self.log_would_create("bookingvisitors", body)
                    continue

                # An account-wide creation-rate condition has been observed
                # live on this entity specifically (401 "Access Denied",
                # undocumented by Nexudus — see CLAUDE.md). classify_failure
                # tells a one-off per-record problem (skip, keep going) apart
                # from the same error repeating (systemic — stop instead of
                # hammering the same wall for every remaining guest).
                try:
                    result = nexudus_create("bookingvisitors", body)
                except Exception as e:  # noqa: BLE001
                    verdict = self.classify_failure("bookingvisitors", e)
                    if verdict == "systemic":
                        self.log.warning(
                            "Stopping booking-guest creation — this error has repeated "
                            "several times in a row, likely an account-wide condition "
                            "rather than a one-off bad record: %s", e,
                            skip=True, entity="bookingvisitors", reason="systemic_rate_limit")
                        return
                    self.log.warning("Skipping booking guest #%s — create failed: %s",
                                      track_key, e,
                                      skip=True, entity="bookingvisitors", reason="unknown_error")
                    continue

                self.track_id({
                    "entity": "bookingvisitors", **result, "GuestKey": track_key,
                    "BookingIndex": defn["index"],
                })
                self.log.info("Linked visitor #%d to booking #%d (id=%s)",
                              visitor_idx, defn["index"], result["Id"])

    # ------------------------------------------------------------------
    # Cancel bookings — delete after all children exist; system creates
    # the CancelledBooking snapshot automatically (§4j). A booking with a
    # normal (unshared) guest cancels fine — CANCEL_BOOKING cascades the
    # BookingVisitor deletion itself, confirmed live via an isolated fresh
    # booking+guest test. The one confirmed failure mode: a Visitor invited
    # as a guest to more than one booking can leave one of that visitor's
    # BookingVisitor links permanently stuck ("You must delete all booking
    # visitors using this record before you can delete it.") — even a
    # direct, isolated DELETE on that one record fails the same way. Rare
    # (needs the same visitor on two separate to-cancel bookings), not
    # worth blocking the whole run over — skip it and move on.
    # ------------------------------------------------------------------
    def _cancel_bookings(self, nexudus_run_command):
        to_cancel = [d for d in self.booking_defs if d["ToCancel"]]
        self.log.info("--- Cancelling Bookings (%d) ---", len(to_cancel))

        for defn in to_cancel:
            idx = defn["index"]
            track_key = str(idx)
            if self.already_created("CancelledBookingIndex", track_key, entity="cancelledbookings"):
                continue

            booking_id = self.booking_ids.get(idx)
            if booking_id is None:
                self.log.warning("Skipping cancellation of booking #%d — never created (seat limit?)",
                                  idx, skip=True, entity="cancelledbookings", reason="parent_skipped")
                continue

            reason = CANCELLATION_REASON_MAP.get(defn["CancellationCategory"], 7)  # 7 = Other

            if self.dry_run:
                self.log.info("WOULD CANCEL_BOOKING %s [%s -> reason=%d]",
                              booking_id, defn["CancellationCategory"], reason)
            else:
                try:
                    nexudus_run_command("bookings", "CANCEL_BOOKING", [booking_id], parameters=[
                        {"Name": "Cancellation Reason", "Value": reason},
                        {"Name": "Cancel without applying cancellation fee rules", "Value": True},
                    ])
                except Exception as e:  # noqa: BLE001
                    if "must delete all booking visitors" in str(e):
                        self.log.warning(
                            "Skipping cancellation of booking #%d — a guest shared with "
                            "another booking left a BookingVisitor link stuck: %s",
                            idx, e, skip=True, entity="cancelledbookings", reason="validation_rejected")
                        continue
                    # Any other failure — e.g. "this command cannot be run for
                    # the booking", confirmed live on a same-day booking whose
                    # start time had already arrived by the time this ran.
                    # classify_failure tells a one-off from the same error
                    # repeating (systemic — stop rather than hammer through
                    # every remaining cancellation the same way).
                    verdict = self.classify_failure("cancelledbookings", e)
                    if verdict == "systemic":
                        self.log.warning(
                            "Stopping booking cancellation — this error has repeated "
                            "several times in a row, likely an account-wide condition: %s", e,
                            skip=True, entity="cancelledbookings", reason="systemic_rate_limit")
                        break
                    self.log.warning("Skipping cancellation of booking #%d — command failed: %s",
                                      idx, e, skip=True, entity="cancelledbookings", reason="unknown_error")
                    continue
                self.track_id({
                    "entity": "cancelledbookings", "Id": booking_id,
                    "CancelledBookingIndex": track_key, "Category": defn["CancellationCategory"],
                })
                self.log.info("Cancelled booking #%d [%s -> reason=%d] (id=%s)",
                              idx, defn["CancellationCategory"], reason, booking_id)

    # ------------------------------------------------------------------
    # CheckIns
    # ------------------------------------------------------------------
    def _create_checkins(self, biz, coworker_ids, time_pass_ids, nexudus_create, nexudus_update):
        self.log.info("--- Check-ins (%d) ---", len(self.checkin_defs))
        day_pass_id = time_pass_ids.get("Day Pass")

        for defn in self.checkin_defs:
            track_key = str(defn["index"])
            if self.already_created("CheckinIndex", track_key, entity="checkins"):
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping check-in #%d — coworker #%d was never created (seat limit?)",
                                  defn["index"], defn["CoworkerIndex"],
                                  skip=True, entity="checkins", reason="parent_skipped")
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
                continue

            # An active contract doesn't always imply access on its own —
            # confirmed live that ~40% of "no valid pass" failures happen
            # even with an active contract at that moment. On that specific
            # failure, grant a Day Pass covering this check-in and retry
            # once, rather than skip — this is what "the number of passes a
            # customer has" being considered looks like: a pass gets
            # created exactly when (and only when) one is actually needed.
            try:
                result = nexudus_create("checkins", body)
            except Exception as e:  # noqa: BLE001
                if "does not have a valid time pass" not in str(e) or day_pass_id is None:
                    # A single bad check-in shouldn't take down the rest of
                    # the layer (extra services, credits, time passes,
                    # coworker products all still run after this loop).
                    # classify_failure tells a one-off from the same error
                    # repeating (systemic — stop rather than hammer through
                    # the remaining checkins, the highest-volume loop here).
                    verdict = self.classify_failure("checkins", e)
                    if verdict == "systemic":
                        self.log.warning(
                            "Stopping check-in creation — this error has repeated "
                            "several times in a row, likely an account-wide condition: %s", e,
                            skip=True, entity="checkins", reason="systemic_rate_limit")
                        break
                    self.log.warning("Skipping check-in #%d — create failed: %s",
                                      defn["index"], e, skip=True, entity="checkins", reason="unknown_error")
                    continue
                pass_guid = self._grant_day_pass(
                    biz, coworker_ids[defn["CoworkerIndex"]], day_pass_id,
                    defn["FromDayOffset"], nexudus_create, nexudus_update)
                if pass_guid is None:
                    self.log.warning("Skipping check-in #%d — could not grant a covering day pass",
                                      defn["index"], skip=True, entity="checkins", reason="unknown_error")
                    continue
                body["CoworkerTimePassGuid"] = pass_guid
                try:
                    result = nexudus_create("checkins", body)
                except Exception as e2:  # noqa: BLE001
                    self.log.warning("Skipping check-in #%d — still failed after granting a day pass: %s",
                                      defn["index"], e2,
                                      skip=True, entity="checkins", reason="validation_rejected")
                    continue

            self.track_id({"entity": "checkins", **result, "CheckinIndex": track_key})
            self.log.info("Created check-in #%d (id=%s)", defn["index"], result["Id"])

    def _grant_day_pass(self, biz, coworker_id, day_pass_id, checkin_day_offset,
                         nexudus_create, nexudus_update):
        """Create + mark-used a CoworkerTimePass covering checkin_day_offset,
        for a check-in that couldn't rely on implicit contract access."""
        body = {
            "CoworkerId": coworker_id,
            "BusinessId": biz,
            "TimePassId": day_pass_id,
            "CreateMultiple": 1,
            "ExpireDate": self._at(checkin_day_offset + 1),
        }
        try:
            result = nexudus_create("coworkertimepasses", body)
        except Exception as e:  # noqa: BLE001
            self.log.warning("Failed to grant a day pass to coworker %s: %s", coworker_id, e)
            return None

        self.track_id({
            "entity": "coworkertimepasses", **result,
            "GrantedForCheckin": True,
        })
        try:
            nexudus_update("coworkertimepasses", result["Id"], {
                "Used": True,
                "UsedDate": self._at(checkin_day_offset),
            })
        except Exception as e:  # noqa: BLE001
            self.log.warning("Granted day pass %s but failed to mark it used: %s", result["Id"], e)
        return result.get("UniqueId")

    # ------------------------------------------------------------------
    # CoworkerExtraService (booking charges + time/printing credits)
    # ------------------------------------------------------------------
    def _create_extra_services(self, biz, coworker_ids, extra_service_ids, nexudus_create, nexudus_run_command,
                                nexudus_list):
        self.log.info("--- Extra Services (%d) ---", len(self.extra_service_defs))
        # CHARGE_BOOKING (a run_command against `bookings`) and the plain
        # coworkerextraservices create below are structurally different
        # operations that happen to end up tracked under the same entity —
        # a systemic condition on one shouldn't stop the other. Once
        # CHARGE_BOOKING looks blocked, skip the rest of *that* sub-kind
        # without further attempts, but keep the loop going for time/
        # printing credits (plain creates, unaffected by whatever's
        # blocking booking charges specifically).
        charge_booking_blocked = False

        for defn in self.extra_service_defs:
            track_key = str(defn["index"])
            if self.already_created("ExtraServiceIndex", track_key, entity="coworkerextraservices"):
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping extra service #%d — coworker #%d was never created (seat limit?)",
                                  defn["index"], defn["CoworkerIndex"],
                                  skip=True, entity="coworkerextraservices", reason="parent_skipped")
                continue

            # A booking_charge is meant to represent an actual booking being
            # charged. Manually creating a standalone CoworkerExtraService
            # with a BookingId link does NOT set Booking.Invoiced — confirmed
            # live the booking stays uncharged from the booking's own point
            # of view (Booking.Invoiced field, confusingly named — see the
            # entity guide). The CHARGE_BOOKING command is what actually
            # charges it, creating the linked CoworkerExtraService itself
            # and flipping Invoiced=true, so bookings correctly show as
            # invoiced/paid once billed.
            if defn["Kind"] == "booking_charge" and defn["BookingIndex"] is not None:
                booking_id = self.booking_ids.get(defn["BookingIndex"])
                if booking_id is None:
                    self.log.warning("Skipping charge #%d — booking #%d was never created",
                                      defn["index"], defn["BookingIndex"],
                                      skip=True, entity="coworkerextraservices", reason="parent_skipped")
                    continue
                if charge_booking_blocked:
                    self.log.warning(
                        "Skipping charge #%d — booking charging stopped earlier this "
                        "run after repeated identical failures, likely an account-wide "
                        "condition", defn["index"],
                        skip=True, entity="coworkerextraservices", reason="systemic_rate_limit")
                    continue

                if self.dry_run:
                    self.log.info("WOULD RUN COMMAND bookings.CHARGE_BOOKING id=%s", booking_id)
                else:
                    # A booking's current state can reject this command
                    # outright — confirmed live, e.g. "this command cannot be
                    # run for the booking" on a same-day booking whose start
                    # time had already arrived. One bad booking shouldn't
                    # take down the rest of the layer (time/printing credits
                    # still run after this loop). classify_failure tells a
                    # one-off from the same error repeating (systemic) — a
                    # dedicated signature key so it doesn't get confused
                    # with the unrelated plain-create path below.
                    try:
                        nexudus_run_command("bookings", "CHARGE_BOOKING", [booking_id])
                    except Exception as e:  # noqa: BLE001
                        verdict = self.classify_failure("coworkerextraservices:charge_booking", e)
                        if verdict == "systemic":
                            self.log.warning(
                                "Booking charges look blocked for the rest of this run — "
                                "this error has repeated several times in a row, likely "
                                "an account-wide condition: %s", e,
                                skip=True, entity="coworkerextraservices", reason="systemic_rate_limit")
                            charge_booking_blocked = True
                            continue
                        self.log.warning("Skipping charge for booking #%d — command failed: %s",
                                          defn["index"], e,
                                          skip=True, entity="coworkerextraservices", reason="unknown_error")
                        continue
                    # CHARGE_BOOKING's response doesn't carry the new
                    # CoworkerExtraService's own Id (just a bare success
                    # envelope) — look it up by BookingId to get a real,
                    # deletable Id for tracking. Previously this tracked a
                    # synthesized "charge-booking-{id}" string instead,
                    # which was enough for already_created() to work but
                    # not a real Nexudus Id — teardown.py could never
                    # actually delete it (confirmed live: 404).
                    charge = next(iter(nexudus_list(
                        "coworkerextraservices", {"CoworkerExtraService_BookingId": booking_id})), None)
                    self.track_id({
                        "entity": "coworkerextraservices",
                        **(charge if charge else {"Id": f"charge-booking-{booking_id}"}),
                        "ExtraServiceIndex": track_key, "Kind": defn["Kind"],
                    })
                    self.log.info("Charged booking #%d [%s] (booking id=%s)",
                                  defn["index"], defn["Kind"], booking_id)
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
            if defn["ExpireDateDayOffset"] is not None:
                body["ExpireDate"] = self._at(defn["ExpireDateDayOffset"])

            if self.dry_run:
                self.log_would_create("coworkerextraservices", body)
                continue

            try:
                result = nexudus_create("coworkerextraservices", body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("coworkerextraservices:plain_create", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping time/printing credit creation — this error has "
                        "repeated several times in a row, likely an account-wide "
                        "condition: %s", e,
                        skip=True, entity="coworkerextraservices", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping extra service #%d — create failed: %s", defn["index"], e,
                                  skip=True, entity="coworkerextraservices", reason="unknown_error")
                continue

            self.track_id({
                "entity": "coworkerextraservices", **result,
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
            if self.already_created("CreditIndex", track_key, entity="coworkerbookingcredits"):
                existing = next(r for r in self.get_tracked_ids()
                                 if r.get("entity") == "coworkerbookingcredits" and r.get("CreditIndex") == track_key)
                self.credit_ids[idx] = existing["Id"]
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping booking credit #%d — coworker #%d was never created (seat limit?)",
                                  idx, defn["CoworkerIndex"],
                                  skip=True, entity="coworkerbookingcredits", reason="parent_skipped")
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
                try:
                    result = nexudus_create("coworkerbookingcredits", body)
                except Exception as e:  # noqa: BLE001
                    if "Expiration data must be greater than valid from date" in str(e):
                        self.log.warning(
                            "Skipping booking credit #%d — ExpireDateDayOffset is before "
                            "ValidFromDayOffset in prebuild data (bucket=%s): %s",
                            idx, defn["Bucket"], e,
                            skip=True, entity="coworkerbookingcredits", reason="validation_rejected")
                        continue
                    verdict = self.classify_failure("coworkerbookingcredits", e)
                    if verdict == "systemic":
                        self.log.warning(
                            "Stopping booking credit creation — this error has repeated "
                            "several times in a row, likely an account-wide condition: %s", e,
                            skip=True, entity="coworkerbookingcredits", reason="systemic_rate_limit")
                        break
                    self.log.warning("Skipping booking credit #%d — create failed: %s", idx, e,
                                      skip=True, entity="coworkerbookingcredits", reason="unknown_error")
                    continue
                self.credit_ids[idx] = result["Id"]
                self.track_id({
                    "entity": "coworkerbookingcredits", **result,
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
            if self.already_created("UseHistoryIndex", track_key, entity="coworkerbookingcreditusehistories"):
                continue

            credit_id = self.credit_ids.get(defn["CreditIndex"])
            if credit_id is None:
                self.log.warning("Skipping credit use #%d — credit #%d was never created",
                                  defn["index"], defn["CreditIndex"],
                                  skip=True, entity="coworkerbookingcreditusehistories", reason="parent_skipped")
                continue

            body = {"CoworkerBookingCreditId": credit_id, "CreditUsed": defn["CreditUsed"]}
            if defn["BookingIndex"] is not None:
                booking_id = self.booking_ids.get(defn["BookingIndex"])
                if booking_id is not None:
                    body["BookingId"] = booking_id

            if self.dry_run:
                self.log_would_create("coworkerbookingcreditusehistories", body)
                continue

            try:
                result = nexudus_create("coworkerbookingcreditusehistories", body)
            except Exception as e:  # noqa: BLE001
                # Generic API error, but confirmed live this happens when
                # cumulative CreditUsed across a credit's use-history
                # entries exceeds its TotalCredit — a prebuild data
                # over-allocation bug, not a client bug.
                self.log.warning(
                    "Skipping credit use #%d on credit #%d — likely exceeds the "
                    "credit's TotalCredit (prebuild over-allocation): %s",
                    defn["index"], defn["CreditIndex"], e,
                    skip=True, entity="coworkerbookingcreditusehistories", reason="validation_rejected")
                continue
            self.track_id({
                "entity": "coworkerbookingcreditusehistories", **result,
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
            if self.already_created("TimePassIndex", track_key, entity="coworkertimepasses"):
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping time pass #%d — coworker #%d was never created (seat limit?)",
                                  defn["index"], defn["CoworkerIndex"],
                                  skip=True, entity="coworkertimepasses", reason="parent_skipped")
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
                continue

            try:
                result = nexudus_create("coworkertimepasses", body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("coworkertimepasses", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping time pass creation — this error has repeated several "
                        "times in a row, likely an account-wide condition: %s", e,
                        skip=True, entity="coworkertimepasses", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping time pass #%d — create failed: %s", defn["index"], e,
                                  skip=True, entity="coworkertimepasses", reason="unknown_error")
                continue

            self.track_id({
                "entity": "coworkertimepasses", **result,
                "TimePassIndex": track_key, "Status": defn["Status"],
            })
            self.log.info("Created time pass #%d [%s] (id=%s)",
                          defn["index"], defn["Status"], result["Id"])

            if defn["Status"] == "used":
                # Not a skip=True/entity= failure — the pass itself was
                # already created and counted above; this is a best-effort
                # follow-up mutation on an existing record, not a planned
                # record that failed to exist.
                try:
                    nexudus_update("coworkertimepasses", result["Id"], {
                        "Used": True,
                        "UsedDate": self._at(defn["UsedDateDayOffset"]),
                    })
                    self.log.info("Marked time pass #%d as used", defn["index"])
                except Exception as e:  # noqa: BLE001
                    self.log.warning("Created time pass #%d but failed to mark it used: %s",
                                      defn["index"], e)

    # ------------------------------------------------------------------
    # CoworkerProduct
    # ------------------------------------------------------------------
    def _create_coworker_products(self, biz, coworker_ids, product_ids, nexudus_create):
        self.log.info("--- Coworker Products (%d) ---", len(self.coworker_product_defs))

        for defn in self.coworker_product_defs:
            track_key = str(defn["index"])
            if self.already_created("CoworkerProductIndex", track_key, entity="coworkerproducts"):
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping coworker product #%d — coworker #%d was never created (seat limit?)",
                                  defn["index"], defn["CoworkerIndex"],
                                  skip=True, entity="coworkerproducts", reason="parent_skipped")
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
                continue

            try:
                result = nexudus_create("coworkerproducts", body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("coworkerproducts", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping coworker product creation — this error has repeated "
                        "several times in a row, likely an account-wide condition: %s", e,
                        skip=True, entity="coworkerproducts", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping coworker product #%d — create failed: %s", defn["index"], e,
                                  skip=True, entity="coworkerproducts", reason="unknown_error")
                continue

            self.track_id({
                "entity": "coworkerproducts", **result,
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
            nexudus_run_command=lambda entity, key, ids, parameters=None: None,
            prev_output=mock_prev,
        )
    else:
        import pipeline
        pipeline.run_up_to(4, business_id=args.business_id)
