"""
Layer 4b — Community

Reads pre-generated definitions from data/deliveries.json + siblings, then
pushes them to the Nexudus API.

Creates:
- CoworkerDelivery (40) — create pending, then update Collected/Forwarded/
  Recycled/ReturnedToSender + its *On date, per house convention (§4q).
- CalendarEvent (20, 4 recurring) + EventProduct (1 ticket type per event,
  required before EventAttendee) + EventAttendee (~60, ~70% linked to a
  seeded coworker) — §4r.
- HelpDeskMessage (25) — §4s.
- CommunityThread (15) + CommunityMessage (~40) — §4t. UserId is a
  required FK distinct from CoworkerId; seeded coworkers have no linked
  User account, so the resolved admin user is used as UserId throughout,
  with CoworkerId set alongside for attribution.
- BlogPost (10) — §4u.
- CoworkerTask (20) — §4v.

Prerequisites: Layer 0 (business, admin user, financial accounts, tax
rates), Layer 1 (resources, help desk departments, community groups,
calendar event categories), Layer 2 (coworkers).
Data files: Run `python prebuild.py` first to generate data/*.json.

Usage:
    python generators/05_community.py              # Live mode
    python generators/05_community.py --dry-run     # Log only
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.base import BaseGenerator, parse_args
from config import DATA_DIR, TODAY, to_utc_str

# Meeting-room resource names — mirrors generators/01_structural.py RESOURCES
MEETING_ROOM_NAMES = [
    "Boardroom Alpha", "Meeting Room Beta", "Meeting Room Gamma",
    "Meeting Room Delta", "Meeting Room Epsilon", "Meeting Room Zeta",
]

DELIVERY_OUTCOME_FIELDS = {
    "collected": ("Collected", "CollectedOn"),
    "forwarded": ("Forwarded", "ForwardedOn"),
    "returned": ("ReturnedToSender", "ReturnedToSenderOn"),
    "recycled": ("Recycled", "RecycledOn"),
}


class CommunityGenerator(BaseGenerator):
    entity_name = "community"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.event_ids = {}          # EventIndex -> Id
        self.event_product_ids = {}  # EventIndex -> Id
        self.thread_ids = {}         # ThreadIndex -> Id

        self.delivery_defs = self._load_data("deliveries.json")
        self.event_defs = self._load_data("calendar_events.json")
        self.event_product_defs = self._load_data("event_products.json")
        self.event_attendee_defs = self._load_data("event_attendees.json")
        self.helpdesk_defs = self._load_data("helpdesk_messages.json")
        self.thread_defs = self._load_data("community_threads.json")
        self.message_defs = self._load_data("community_messages.json")
        self.blog_defs = self._load_data("blog_posts.json")
        self.task_defs = self._load_data("coworker_tasks.json")

    @staticmethod
    def _load_data(filename):
        path = DATA_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"{path} not found. Run 'python prebuild.py' first.")
        return json.loads(path.read_text())

    def run(self, nexudus_list, nexudus_create, nexudus_update, prev_output):
        biz = prev_output["business_id"]
        admin_id = prev_output["admin_user_id"]
        currency_id = prev_output["currency_id"]
        coworker_ids = prev_output["coworker_ids"]
        coworker_defs_by_index = {c["index"]: c for c in prev_output["coworker_defs"]}
        resource_ids = prev_output["resource_ids"]
        help_desk_dept_ids = prev_output["help_desk_dept_ids"]
        community_group_ids = prev_output["community_group_ids"]
        event_category_ids = prev_output["event_category_ids"]
        fin_account_ids = prev_output["fin_account_ids"]
        tax_rate_ids = prev_output["tax_rate_ids"]

        self._create_deliveries(biz, coworker_ids, nexudus_create, nexudus_update)
        self._create_events(biz, event_category_ids, resource_ids, nexudus_create)
        self._create_event_products(currency_id, fin_account_ids, tax_rate_ids, nexudus_create)
        self._create_event_attendees(biz, coworker_ids, coworker_defs_by_index, nexudus_create)
        self._create_helpdesk_messages(biz, coworker_ids, help_desk_dept_ids, nexudus_create)
        self._create_community_threads(biz, admin_id, coworker_ids, community_group_ids, nexudus_create)
        self._create_community_messages(admin_id, coworker_ids, nexudus_create)
        self._create_blog_posts(biz, admin_id, nexudus_create)
        self._create_coworker_tasks(biz, admin_id, coworker_ids, nexudus_create)

        self.log.info("Layer 4b complete.")
        return {**prev_output}

    # ------------------------------------------------------------------
    # Date helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _at(day_offset, hour=0, minute=0):
        d = TODAY + timedelta(days=day_offset)
        return to_utc_str(d, hour=hour, minute=minute)

    # ------------------------------------------------------------------
    # CoworkerDelivery
    # ------------------------------------------------------------------
    def _create_deliveries(self, biz, coworker_ids, nexudus_create, nexudus_update):
        self.log.info("--- Deliveries (%d) ---", len(self.delivery_defs))

        for defn in self.delivery_defs:
            track_key = str(defn["index"])
            if self.already_created("DeliveryIndex", track_key):
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping delivery #%d — coworker #%d was never created (seat limit?)",
                                  defn["index"], defn["CoworkerIndex"])
                continue

            body = {
                "BusinessId": biz,
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "Name": defn["Name"],
                "Location": "Reception",
                "DeliveryType": defn["DeliveryType"],
                "HandlingPreference": defn["HandlingPreference"],
            }

            if self.dry_run:
                self.log_would_create("coworkerdeliveries", body)
                if defn["Outcome"] != "pending":
                    self.log.info("WOULD UPDATE coworkerdeliveries DRY: %s=true", defn["Outcome"])
            else:
                result = nexudus_create("coworkerdeliveries", body)
                self.track_id({
                    "entity": "coworkerdeliveries", "Id": result["Id"], "DeliveryIndex": track_key,
                })
                self.log.info("Created delivery #%d [%s] (id=%s)",
                              defn["index"], defn["Outcome"], result["Id"])

                if defn["Outcome"] != "pending":
                    bool_field, date_field = DELIVERY_OUTCOME_FIELDS[defn["Outcome"]]
                    nexudus_update("coworkerdeliveries", result["Id"], {
                        bool_field: True,
                        date_field: self._at(defn["OutcomeDayOffset"]),
                    })

    # ------------------------------------------------------------------
    # CalendarEvent
    # ------------------------------------------------------------------
    def _create_events(self, biz, event_category_ids, resource_ids, nexudus_create):
        self.log.info("--- Calendar Events (%d) ---", len(self.event_defs))

        for defn in self.event_defs:
            idx = defn["index"]
            track_key = str(idx)
            if self.already_created("EventIndex", track_key):
                existing = next(r for r in self.get_tracked_ids()
                                 if r.get("entity") == "calendarevents" and r.get("EventIndex") == track_key)
                self.event_ids[idx] = existing["Id"]
                continue

            start_time = self._at(defn["StartDayOffset"], hour=defn["StartHour"])
            end_time = to_utc_str(
                datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                + timedelta(minutes=defn["DurationMinutes"])
            )

            body = {
                "BusinessId": biz,
                "Name": defn["Name"],
                "ShortDescription": defn["ShortDescription"],
                "LongDescription": defn["LongDescription"],
                "StartDate": start_time,
                "EndDate": end_time,
                "CalendarEventCategoryId": event_category_ids.get(defn["Category"]),
                "OnlyForMembers": defn["OnlyForMembers"],
                "ShowInHomePage": defn["ShowInHomePage"],
                # Repeats is flagged required by the schema even for one-offs;
                # RepeatEvent=False means it won't actually recur.
                "Repeats": defn["Repeats"] or 1,
                "RepeatEvent": bool(defn["Repeats"]),
            }
            if defn["Repeats"]:
                body["RepeatEvery"] = defn["RepeatEvery"]
                body["RepeatUntil"] = self._at(defn["RepeatUntilDayOffset"])
            if defn["ResourceLinked"]:
                room_name = MEETING_ROOM_NAMES[idx % len(MEETING_ROOM_NAMES)]
                room_id = resource_ids.get(room_name)
                if room_id is not None:
                    body["ResourceId"] = room_id

            if self.dry_run:
                self.log_would_create("calendarevents", body)
                self.event_ids[idx] = f"DRY-EVENT-{idx}"
            else:
                result = nexudus_create("calendarevents", body)
                self.event_ids[idx] = result["Id"]
                self.track_id({
                    "entity": "calendarevents", "Id": result["Id"], "EventIndex": track_key,
                })
                self.log.info("Created event #%d '%s' (id=%s)", idx, defn["Name"], result["Id"])

    # ------------------------------------------------------------------
    # EventProduct (ticket type — required before EventAttendee)
    # ------------------------------------------------------------------
    def _create_event_products(self, currency_id, fin_account_ids, tax_rate_ids, nexudus_create):
        self.log.info("--- Event Products (%d) ---", len(self.event_product_defs))

        for defn in self.event_product_defs:
            idx = defn["index"]
            track_key = str(idx)
            event_id = self.event_ids.get(defn["EventIndex"])
            if event_id is None:
                continue
            if self.already_created("EventProductIndex", track_key):
                existing = next(r for r in self.get_tracked_ids()
                                 if r.get("entity") == "eventproducts" and r.get("EventProductIndex") == track_key)
                self.event_product_ids[idx] = existing["Id"]
                continue

            body = {
                "CalendarEventId": event_id,
                "Name": "General Admission",
                "DisplayOrder": 1,
                "StartDate": self._at(defn["SaleStartDayOffset"]),
                "EndDate": self._at(defn["SaleEndDayOffset"]),
                "Price": defn["Price"],
                "CurrencyId": currency_id,
                "FinancialAccountId": fin_account_ids.get("EVT-001"),
                "TaxRateId": tax_rate_ids.get("Standard"),
            }

            if self.dry_run:
                self.log_would_create("eventproducts", body)
                self.event_product_ids[idx] = f"DRY-EVENTPROD-{idx}"
            else:
                result = nexudus_create("eventproducts", body)
                self.event_product_ids[idx] = result["Id"]
                self.track_id({
                    "entity": "eventproducts", "Id": result["Id"], "EventProductIndex": track_key,
                })
                self.log.info("Created ticket for event #%d (id=%s)", defn["EventIndex"], result["Id"])

    # ------------------------------------------------------------------
    # EventAttendee
    # ------------------------------------------------------------------
    def _create_event_attendees(self, biz, coworker_ids, coworker_defs_by_index, nexudus_create):
        self.log.info("--- Event Attendees (%d) ---", len(self.event_attendee_defs))

        for defn in self.event_attendee_defs:
            track_key = str(defn["index"])
            if self.already_created("AttendeeIndex", track_key):
                continue

            event_id = self.event_ids.get(defn["EventIndex"])
            event_product_id = self.event_product_ids.get(defn["EventIndex"])
            if event_id is None or event_product_id is None:
                continue

            if defn["CoworkerIndex"] is not None:
                if defn["CoworkerIndex"] not in coworker_ids:
                    self.log.warning("Skipping attendee #%d — coworker #%d was never created (seat limit?)",
                                      defn["index"], defn["CoworkerIndex"])
                    continue
                cw = coworker_defs_by_index[defn["CoworkerIndex"]]
                full_name, email = cw["FullName"], cw["Email"]
            else:
                full_name, email = defn["FullName"], defn["Email"]

            body = {
                "BusinessId": biz,
                "CalendarEventId": event_id,
                "EventProductId": event_product_id,
                "FullName": full_name,
                "Email": email,
                "CheckedIn": defn["CheckedIn"],
            }
            if defn["CoworkerIndex"] is not None:
                body["CoworkerId"] = coworker_ids[defn["CoworkerIndex"]]

            if self.dry_run:
                self.log_would_create("eventattendees", body)
            else:
                try:
                    result = nexudus_create("eventattendees", body)
                except Exception as e:  # noqa: BLE001
                    if "You cannot purchase this product" in str(e):
                        # Confirmed live this isn't about OnlyForMembers,
                        # tariff, team, or paying-member status — reproduced
                        # with several other coworkers on the same event/
                        # tariff/team combinations, all succeeded. Isolated
                        # to specific coworker records; root cause not
                        # found after significant live diagnosis.
                        self.log.warning(
                            "Skipping attendee #%d — event purchase rejected for "
                            "coworker #%s (cause not isolated, confirmed not "
                            "membership/tariff/team related): %s",
                            defn["index"], defn["CoworkerIndex"], e)
                        continue
                    raise
                self.track_id({
                    "entity": "eventattendees", "Id": result["Id"], "AttendeeIndex": track_key,
                })
                self.log.info("Registered attendee #%d for event #%d (id=%s)",
                              defn["index"], defn["EventIndex"], result["Id"])

    # ------------------------------------------------------------------
    # HelpDeskMessage
    # ------------------------------------------------------------------
    def _create_helpdesk_messages(self, biz, coworker_ids, help_desk_dept_ids, nexudus_create):
        self.log.info("--- Help Desk Messages (%d) ---", len(self.helpdesk_defs))

        for defn in self.helpdesk_defs:
            track_key = str(defn["index"])
            if self.already_created("HelpDeskIndex", track_key):
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping help desk message #%d — coworker #%d was never created (seat limit?)",
                                  defn["index"], defn["CoworkerIndex"])
                continue

            body = {
                "BusinessId": biz,
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "Subject": defn["Subject"],
                "MessageText": f"{defn['Subject']}. Please look into this when you get a chance.",
                "Priority": defn["Priority"],
                "AiProcessingResult": 1,  # NotProcessed
                "HelpDeskDepartmentId": help_desk_dept_ids.get(defn["DepartmentName"]),
                "Closed": defn["Closed"],
            }

            if self.dry_run:
                self.log_would_create("helpdeskmessages", body)
            else:
                result = nexudus_create("helpdeskmessages", body)
                self.track_id({
                    "entity": "helpdeskmessages", "Id": result["Id"], "HelpDeskIndex": track_key,
                })
                self.log.info("Created help desk ticket #%d [%s] (id=%s)",
                              defn["index"], defn["DepartmentName"], result["Id"])

    # ------------------------------------------------------------------
    # CommunityThread
    # ------------------------------------------------------------------
    def _create_community_threads(self, biz, admin_id, coworker_ids, community_group_ids, nexudus_create):
        self.log.info("--- Community Threads (%d) ---", len(self.thread_defs))

        for defn in self.thread_defs:
            idx = defn["index"]
            track_key = str(idx)
            if self.already_created("ThreadIndex", track_key):
                existing = next(r for r in self.get_tracked_ids()
                                 if r.get("entity") == "communitythreads" and r.get("ThreadIndex") == track_key)
                self.thread_ids[idx] = existing["Id"]
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping thread #%d — coworker #%d was never created (seat limit?)",
                                  idx, defn["CoworkerIndex"])
                continue

            body = {
                "BusinessId": biz,
                "UserId": admin_id,
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "CommunityGroupId": community_group_ids.get(defn["GroupName"]),
                "Subject": defn["Subject"],
                "Message": defn["Message"],
                "Private": defn["Private"],
            }

            if self.dry_run:
                self.log_would_create("communitythreads", body)
                self.thread_ids[idx] = f"DRY-THREAD-{idx}"
            else:
                result = nexudus_create("communitythreads", body)
                self.thread_ids[idx] = result["Id"]
                self.track_id({
                    "entity": "communitythreads", "Id": result["Id"], "ThreadIndex": track_key,
                })
                self.log.info("Created thread #%d [%s] (id=%s)", idx, defn["GroupName"], result["Id"])

    # ------------------------------------------------------------------
    # CommunityMessage
    # ------------------------------------------------------------------
    def _create_community_messages(self, admin_id, coworker_ids, nexudus_create):
        self.log.info("--- Community Messages (%d) ---", len(self.message_defs))

        for defn in self.message_defs:
            track_key = str(defn["index"])
            if self.already_created("MessageIndex", track_key):
                continue

            thread_id = self.thread_ids.get(defn["ThreadIndex"])
            if thread_id is None:
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping community message #%d — coworker #%d was never created (seat limit?)",
                                  defn["index"], defn["CoworkerIndex"])
                continue

            body = {
                "CommunityThreadId": thread_id,
                "UserId": admin_id,
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "Message": defn["Message"],
            }

            if self.dry_run:
                self.log_would_create("communitymessages", body)
            else:
                result = nexudus_create("communitymessages", body)
                self.track_id({
                    "entity": "communitymessages", "Id": result["Id"], "MessageIndex": track_key,
                })
                self.log.info("Created reply #%d on thread #%d (id=%s)",
                              defn["index"], defn["ThreadIndex"], result["Id"])

    # ------------------------------------------------------------------
    # BlogPost
    # ------------------------------------------------------------------
    def _create_blog_posts(self, biz, admin_id, nexudus_create):
        self.log.info("--- Blog Posts (%d) ---", len(self.blog_defs))

        for defn in self.blog_defs:
            track_key = str(defn["index"])
            if self.already_created("BlogPostIndex", track_key):
                continue

            body = {
                "BusinessId": biz,
                "Title": defn["Title"],
                "SummaryText": defn["SummaryText"],
                "FullText": defn["FullText"],
                "CommentsCount": 0,
                "PublishDate": self._at(defn["PublishDayOffset"]),
                "OnlyForMembers": defn["OnlyForMembers"],
                "ShowInHomePage": defn["ShowInHomePage"],
                "PostedById": admin_id,
            }

            if self.dry_run:
                self.log_would_create("blogposts", body)
            else:
                result = nexudus_create("blogposts", body)
                self.track_id({
                    "entity": "blogposts", "Id": result["Id"], "BlogPostIndex": track_key,
                })
                self.log.info("Created blog post #%d '%s' (id=%s)",
                              defn["index"], defn["Title"], result["Id"])

    # ------------------------------------------------------------------
    # CoworkerTask
    # ------------------------------------------------------------------
    def _create_coworker_tasks(self, biz, admin_id, coworker_ids, nexudus_create):
        self.log.info("--- Coworker Tasks (%d) ---", len(self.task_defs))

        for defn in self.task_defs:
            track_key = str(defn["index"])
            if self.already_created("TaskIndex", track_key):
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping task #%d — coworker #%d was never created (seat limit?)",
                                  defn["index"], defn["CoworkerIndex"])
                continue

            body = {
                "BusinessId": biz,
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "Name": defn["Name"],
                "ResponsibleId": admin_id,
                "DueDate": self._at(defn["DueDayOffset"]),
                "Completed": defn["Completed"],
            }

            if self.dry_run:
                self.log_would_create("coworkertasks", body)
            else:
                result = nexudus_create("coworkertasks", body)
                self.track_id({
                    "entity": "coworkertasks", "Id": result["Id"], "TaskIndex": track_key,
                })
                self.log.info("Created task #%d '%s' (id=%s)", defn["index"], defn["Name"], result["Id"])


if __name__ == "__main__":
    args = parse_args()
    gen = CommunityGenerator(dry_run=args.dry_run)

    if gen.dry_run:
        import importlib
        ref = importlib.import_module("generators.00_reference")
        struct = importlib.import_module("generators.01_structural")

        mock_coworker_defs = json.loads((DATA_DIR / "coworkers.json").read_text())
        mock_coworker_ids = {c["index"]: f"DRY-CW-{c['index']}" for c in mock_coworker_defs}
        mock_resource_ids = {r["Name"]: f"DRY-RES-{r['Name']}" for r in struct.RESOURCES}
        mock_help_desk_dept_ids = {d["Name"]: f"DRY-DEPT-{d['Name']}" for d in struct.HELP_DESK_DEPARTMENTS}
        mock_community_group_ids = {g["Name"]: f"DRY-GROUP-{g['Name']}" for g in struct.COMMUNITY_GROUPS}
        mock_event_category_ids = {c["Name"]: f"DRY-CAT-{c['Name']}" for c in struct.CALENDAR_EVENT_CATEGORIES}
        mock_fin_account_ids = {c["Code"]: f"DRY-FA-{c['Code']}" for c in ref.FINANCIAL_ACCOUNTS}

        mock_prev = {
            "business_id": "DRY-BIZ-1",
            "admin_user_id": "DRY-ADMIN-1",
            "currency_id": "DRY-CUR-1",
            "coworker_ids": mock_coworker_ids,
            "coworker_defs": mock_coworker_defs,
            "resource_ids": mock_resource_ids,
            "help_desk_dept_ids": mock_help_desk_dept_ids,
            "community_group_ids": mock_community_group_ids,
            "event_category_ids": mock_event_category_ids,
            "fin_account_ids": mock_fin_account_ids,
            "tax_rate_ids": {"Standard": "DRY-TAX-STD"},
        }

        _counter = {"n": 0}

        def _mock_create(entity, body):
            _counter["n"] += 1
            return {"Id": f"DRY-{entity}-{_counter['n']}"}

        gen.run(
            nexudus_list=lambda entity, filters: [],
            nexudus_create=_mock_create,
            nexudus_update=lambda entity, id, body: {"Id": id},
            prev_output=mock_prev,
        )
    else:
        import pipeline
        pipeline.run_up_to(5)
