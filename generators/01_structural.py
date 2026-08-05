"""
Layer 1 — Structural Setup

Creates:
- Team × 5
- Tariff × 8 (with financial account + tax rate links)
- Product × 12 (with financial account + tax rate links)
- ExtraService × 6
- TimePass × 4
- Resource × 20
- FloorPlan × 3
- FloorPlanDesk × 40
- InventoryAsset × 15
- DiscountCode × 6
- CrmBoard × 2 + CrmBoardColumn × 10
- BusinessTimeSlot × 3
- HelpDeskDepartment × 3
- CommunityGroup × 3
- CalendarEventCategory × 4

Prerequisites: Layer 0 (tax rates, financial accounts, resource types).

Usage:
    python generators/01_structural.py              # Live mode
    python generators/01_structural.py --dry-run     # Log only
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.base import BaseGenerator, parse_args
from config import TEST_NAME_PREFIX

# ---------------------------------------------------------------------------
# Data definitions
# ---------------------------------------------------------------------------

TEAMS = [
    {"Name": f"{TEST_NAME_PREFIX}Acme Corp"},
    {"Name": f"{TEST_NAME_PREFIX}Bright Studio"},
    {"Name": f"{TEST_NAME_PREFIX}CloudNine Labs"},
    {"Name": f"{TEST_NAME_PREFIX}Delta Ventures"},
    {"Name": f"{TEST_NAME_PREFIX}Echo Digital"},
]

# Tariffs reference §4b — SystemTariffType enum, billing frequency, pricing
TARIFFS = [
    {"Name": f"{TEST_NAME_PREFIX}Hot Desk Monthly",       "SystemTariffType": 5, "InvoiceEvery": 1, "InvoiceEveryWeeks": 0, "Price": 150.00,   "FinAcctCode": "MEM-001", "TaxRate": "Standard"},
    {"Name": f"{TEST_NAME_PREFIX}Dedicated Desk Monthly", "SystemTariffType": 3, "InvoiceEvery": 1, "InvoiceEveryWeeks": 0, "Price": 350.00,   "FinAcctCode": "MEM-001", "TaxRate": "Standard"},
    {"Name": f"{TEST_NAME_PREFIX}Private Office Small",   "SystemTariffType": 1, "InvoiceEvery": 1, "InvoiceEveryWeeks": 0, "Price": 1200.00,  "FinAcctCode": "MEM-001", "TaxRate": "Standard"},
    {"Name": f"{TEST_NAME_PREFIX}Private Office Large",   "SystemTariffType": 1, "InvoiceEvery": 1, "InvoiceEveryWeeks": 0, "Price": 2000.00,  "FinAcctCode": "MEM-001", "TaxRate": "Standard"},
    {"Name": f"{TEST_NAME_PREFIX}Hot Desk Quarterly",     "SystemTariffType": 5, "InvoiceEvery": 3, "InvoiceEveryWeeks": 0, "Price": 400.00,   "FinAcctCode": "MEM-001", "TaxRate": "Standard"},
    {"Name": f"{TEST_NAME_PREFIX}Private Office Annual",  "SystemTariffType": 1, "InvoiceEvery": 12,"InvoiceEveryWeeks": 0, "Price": 12000.00, "FinAcctCode": "MEM-001", "TaxRate": "Standard"},
    {"Name": f"{TEST_NAME_PREFIX}Flex Weekly",            "SystemTariffType": 6, "InvoiceEvery": 0, "InvoiceEveryWeeks": 1, "Price": 50.00,    "FinAcctCode": "MEM-001", "TaxRate": "Standard"},
    {"Name": f"{TEST_NAME_PREFIX}Flex Fortnightly",       "SystemTariffType": 6, "InvoiceEvery": 0, "InvoiceEveryWeeks": 2, "Price": 250.00,   "FinAcctCode": "MEM-001", "TaxRate": "Standard"},
]

# Products reference §4h
PRODUCTS = [
    {"Name": f"{TEST_NAME_PREFIX}Catering - Tea/Coffee",       "Price": 5.00,    "AvailableAs": 3, "SystemProductType": 10, "FinAcctCode": "PRD-001", "TaxRate": "Standard", "Description": "Tea/coffee service for bookings"},
    {"Name": f"{TEST_NAME_PREFIX}Catering - Lunch",            "Price": 15.00,   "AvailableAs": 3, "SystemProductType": 10, "FinAcctCode": "PRD-001", "TaxRate": "Standard", "Description": "Lunch catering for bookings"},
    {"Name": f"{TEST_NAME_PREFIX}AV Equipment",                "Price": 25.00,   "AvailableAs": 3, "SystemProductType": 5,  "FinAcctCode": "PRD-001", "TaxRate": "Standard", "Description": "Audio-visual equipment hire"},
    {"Name": f"{TEST_NAME_PREFIX}Storage Locker",              "Price": 30.00,   "AvailableAs": 2, "SystemProductType": 6,  "FinAcctCode": "PRD-001", "TaxRate": "Standard", "Description": "Monthly locker rental"},
    {"Name": f"{TEST_NAME_PREFIX}Parking Space",               "Price": 75.00,   "AvailableAs": 2, "SystemProductType": 99, "FinAcctCode": "PRD-001", "TaxRate": "Standard", "Description": "Monthly parking space"},
    {"Name": f"{TEST_NAME_PREFIX}Mail Handling",               "Price": 20.00,   "AvailableAs": 2, "SystemProductType": 99, "FinAcctCode": "PRD-001", "TaxRate": "Zero-rated", "Description": "Mail forwarding and handling"},
    {"Name": f"{TEST_NAME_PREFIX}Day Pass (5-pack)",           "Price": 120.00,  "AvailableAs": 3, "SystemProductType": 1,  "FinAcctCode": "MEM-001", "TaxRate": "Standard", "Description": "Bundle of 5 day passes"},
    {"Name": f"{TEST_NAME_PREFIX}Credit Bundle £50",           "Price": 50.00,   "AvailableAs": 3, "SystemProductType": 2,  "FinAcctCode": "CRD-001", "TaxRate": "Standard", "Description": "£50 booking credit"},
    {"Name": f"{TEST_NAME_PREFIX}Credit Bundle £200",          "Price": 200.00,  "AvailableAs": 3, "SystemProductType": 2,  "FinAcctCode": "CRD-001", "TaxRate": "Standard", "Description": "£200 booking credit"},
    {"Name": f"{TEST_NAME_PREFIX}Security Deposit - Office",   "Price": 1000.00, "AvailableAs": 3, "SystemProductType": 99, "FinAcctCode": "DEP-001", "TaxRate": "Zero-rated", "Description": "Refundable office deposit"},
    {"Name": f"{TEST_NAME_PREFIX}Security Deposit - Desk",     "Price": 250.00,  "AvailableAs": 3, "SystemProductType": 99, "FinAcctCode": "DEP-001", "TaxRate": "Zero-rated", "Description": "Refundable desk deposit"},
    {"Name": f"{TEST_NAME_PREFIX}Printing Credits (500 pages)","Price": 25.00,   "AvailableAs": 3, "SystemProductType": 99, "FinAcctCode": "PRD-001", "TaxRate": "Standard", "Description": "500-page printing credit"},
]

# ExtraServices — resource booking rates. ChargePeriod has no "Hours" value
# (only Minutes/Days/Weeks/Months/Uses/FourWeekMonths) — hourly-feeling rates
# are Minutes(1) rates that Nexudus's booking engine multiplies by actual
# booked duration. Meeting Room/Hot Desk/Phone Booth are billed this way;
# Private Office and Parking stay on their original daily/weekly cadence.
EXTRA_SERVICES = [
    {"Name": f"{TEST_NAME_PREFIX}Meeting Room Rate",   "Price": round(25.00 / 60, 4), "ChargePeriod": 1, "FinAcctCode": "BKG-001", "TaxRate": "Standard", "ResourceType": "Meeting Room"},
    {"Name": f"{TEST_NAME_PREFIX}Hot Desk Rate",       "Price": round(15.00 / 60, 4), "ChargePeriod": 1, "MaximumPrice": 50.00, "FinAcctCode": "BKG-001", "TaxRate": "Standard", "ResourceType": "Hot Desk"},
    {"Name": f"{TEST_NAME_PREFIX}Private Office Rate", "Price": 50.00,  "ChargePeriod": 2, "FinAcctCode": "BKG-001", "TaxRate": "Standard", "ResourceType": "Private Office"},
    {"Name": f"{TEST_NAME_PREFIX}Phone Booth Rate",    "Price": round(10.00 / 60, 4), "ChargePeriod": 1, "FinAcctCode": "BKG-001", "TaxRate": "Standard", "ResourceType": "Phone Booth"},
    {"Name": f"{TEST_NAME_PREFIX}Parking Rate",        "Price": 8.00,   "ChargePeriod": 3, "FinAcctCode": "BKG-001", "TaxRate": "Standard", "ResourceType": "Parking"},
    # IsBookingCredit/IsPrintingCredit are required for a TariffExtraService
    # to reference these as a plan benefit (see _create_tariff_benefits).
    {"Name": f"{TEST_NAME_PREFIX}Time Credit",         "Price": 0.00,   "ChargePeriod": 1, "FinAcctCode": "BKG-001", "TaxRate": "Standard", "ResourceType": "Meeting Room", "IsBookingCredit": True},
    {"Name": f"{TEST_NAME_PREFIX}Printing Credit",     "Price": 0.00,   "ChargePeriod": 5, "FinAcctCode": "BKG-001", "TaxRate": "Standard", "ResourceType": "Meeting Room", "IsPrintingCredit": True},
]

# TimePasses
TIME_PASSES = [
    {"Name": f"{TEST_NAME_PREFIX}Day Pass",           "Price": 25.00},
    {"Name": f"{TEST_NAME_PREFIX}Half Day Pass",      "Price": 15.00},
    {"Name": f"{TEST_NAME_PREFIX}10-Visit Pass",      "Price": 200.00},
    {"Name": f"{TEST_NAME_PREFIX}Evening Pass",       "Price": 10.00},
]

# Plan benefits — day passes + time/printing credit allowances included per
# billing cycle. eTimeSpanWeekMonth renewal: Week=1, TariffMonth=3.
# Flex plans (part-time) get a smaller time-credit-only benefit, no day
# passes or printing — matches their lighter usage pattern.
TARIFF_BENEFITS = {
    "Hot Desk Monthly":       {"day_passes": 2, "time_credit_minutes": 120, "printing_pages": 100, "renewal": 3},
    "Dedicated Desk Monthly": {"day_passes": 2, "time_credit_minutes": 180, "printing_pages": 150, "renewal": 3},
    "Private Office Small":   {"day_passes": 3, "time_credit_minutes": 240, "printing_pages": 200, "renewal": 3},
    "Private Office Large":   {"day_passes": 4, "time_credit_minutes": 300, "printing_pages": 300, "renewal": 3},
    "Hot Desk Quarterly":     {"day_passes": 2, "time_credit_minutes": 120, "printing_pages": 100, "renewal": 3},
    "Private Office Annual":  {"day_passes": 4, "time_credit_minutes": 300, "printing_pages": 300, "renewal": 3},
    "Flex Weekly":            {"time_credit_minutes": 60, "renewal": 1},
    "Flex Fortnightly":       {"time_credit_minutes": 90, "renewal": 1},
}

# Resources — 20 total across types
# SystemResourceType: 1=MeetingRoom, 2=HotDesk, 3=PrivateOffice, 4=PhoneBooth, 5=Parking
RESOURCES = [
    # Meeting Rooms (6)
    {"Name": f"{TEST_NAME_PREFIX}Boardroom Alpha",     "SystemResourceType": 1, "ResourceType": "Meeting Room"},
    {"Name": f"{TEST_NAME_PREFIX}Meeting Room Beta",   "SystemResourceType": 1, "ResourceType": "Meeting Room"},
    {"Name": f"{TEST_NAME_PREFIX}Meeting Room Gamma",  "SystemResourceType": 1, "ResourceType": "Meeting Room"},
    {"Name": f"{TEST_NAME_PREFIX}Meeting Room Delta",  "SystemResourceType": 1, "ResourceType": "Meeting Room"},
    {"Name": f"{TEST_NAME_PREFIX}Meeting Room Epsilon","SystemResourceType": 1, "ResourceType": "Meeting Room"},
    {"Name": f"{TEST_NAME_PREFIX}Meeting Room Zeta",   "SystemResourceType": 1, "ResourceType": "Meeting Room"},
    # Hot Desks (5)
    {"Name": f"{TEST_NAME_PREFIX}Hot Desk Area A",     "SystemResourceType": 2, "ResourceType": "Hot Desk"},
    {"Name": f"{TEST_NAME_PREFIX}Hot Desk Area B",     "SystemResourceType": 2, "ResourceType": "Hot Desk"},
    {"Name": f"{TEST_NAME_PREFIX}Hot Desk Area C",     "SystemResourceType": 2, "ResourceType": "Hot Desk"},
    {"Name": f"{TEST_NAME_PREFIX}Hot Desk Area D",     "SystemResourceType": 2, "ResourceType": "Hot Desk"},
    {"Name": f"{TEST_NAME_PREFIX}Hot Desk Area E",     "SystemResourceType": 2, "ResourceType": "Hot Desk"},
    # Private Offices (5)
    {"Name": f"{TEST_NAME_PREFIX}Office 101",          "SystemResourceType": 3, "ResourceType": "Private Office"},
    {"Name": f"{TEST_NAME_PREFIX}Office 102",          "SystemResourceType": 3, "ResourceType": "Private Office"},
    {"Name": f"{TEST_NAME_PREFIX}Office 103",          "SystemResourceType": 3, "ResourceType": "Private Office"},
    {"Name": f"{TEST_NAME_PREFIX}Office 104",          "SystemResourceType": 3, "ResourceType": "Private Office"},
    {"Name": f"{TEST_NAME_PREFIX}Office 105",          "SystemResourceType": 3, "ResourceType": "Private Office"},
    # Phone Booths (2)
    {"Name": f"{TEST_NAME_PREFIX}Phone Booth 1",       "SystemResourceType": 4, "ResourceType": "Phone Booth"},
    {"Name": f"{TEST_NAME_PREFIX}Phone Booth 2",       "SystemResourceType": 4, "ResourceType": "Phone Booth"},
    # Parking (2)
    {"Name": f"{TEST_NAME_PREFIX}Parking Bay P1",      "SystemResourceType": 5, "ResourceType": "Parking"},
    {"Name": f"{TEST_NAME_PREFIX}Parking Bay P2",      "SystemResourceType": 5, "ResourceType": "Parking"},
]

FLOOR_PLANS = [
    {"Name": f"{TEST_NAME_PREFIX}Ground Floor", "FloorLevel": 0},
    {"Name": f"{TEST_NAME_PREFIX}First Floor",  "FloorLevel": 1},
    {"Name": f"{TEST_NAME_PREFIX}Mezzanine",    "FloorLevel": 2},
]

# FloorPlanDesks — §4d distribution
# ItemType: 1=Office, 2=DedicatedDesk, 3=HotDesk, 4=Other(Storage), 5=Room
def _generate_floor_plan_desks():
    desks = []
    idx = 0
    areas = ["Ground Floor", "First Floor", "Mezzanine"]

    # Offices (10)
    for i in range(10):
        idx += 1
        desks.append({
            "Name": f"{TEST_NAME_PREFIX}Office Unit {idx:02d}",
            "ItemType": 1, "Size": 150 + i * 28, "Capacity": 2 + (i % 4) * 2,
            "Price": 1200 + i * 145, "Area": areas[i % 3],
            "FloorPlan": areas[i % 3],
        })
    # Dedicated Desks (12)
    for i in range(12):
        idx += 1
        desks.append({
            "Name": f"{TEST_NAME_PREFIX}Desk {idx:02d}",
            "ItemType": 2, "Size": 30 + (i % 3) * 10, "Capacity": 1,
            "Price": 300 + (i % 4) * 25, "Area": areas[i % 3],
            "FloorPlan": areas[i % 3],
        })
    # Hot Desks (10)
    for i in range(10):
        idx += 1
        desks.append({
            "Name": f"{TEST_NAME_PREFIX}Hot Desk {idx:02d}",
            "ItemType": 3, "Size": 20 + (i % 2) * 10, "Capacity": 1,
            "Price": 100 + (i % 5) * 20, "Area": areas[i % 3],
            "FloorPlan": areas[i % 3],
        })
    # Storage (4)
    for i in range(4):
        idx += 1
        desks.append({
            "Name": f"{TEST_NAME_PREFIX}Storage {idx:02d}",
            "ItemType": 4, "Size": 10 + i * 3, "Capacity": 0,
            "Price": 50 + i * 15, "Area": areas[i % 3],
            "FloorPlan": areas[i % 3],
        })
    # Rooms (4)
    for i in range(4):
        idx += 1
        desks.append({
            "Name": f"{TEST_NAME_PREFIX}Room {idx:02d}",
            "ItemType": 5, "Size": 200 + i * 100, "Capacity": 4 + i * 4,
            "Price": 1800 + i * 400, "Area": areas[i % 3],
            "FloorPlan": areas[i % 3],
        })
    return desks

FLOOR_PLAN_DESKS = _generate_floor_plan_desks()

# Inventory Assets — §4n
INVENTORY_ASSETS = [
    # Lockers (8)
    *[{"Name": f"{TEST_NAME_PREFIX}Locker A-{i:02d}", "AssignToType": 3, "Value": 0} for i in range(1, 9)],
    # Monitors (3)
    *[{"Name": f"{TEST_NAME_PREFIX}Monitor Dell 27\" #{i}", "AssignToType": 2, "Value": 350} for i in range(1, 4)],
    # Standing Desk Converters (2)
    *[{"Name": f"{TEST_NAME_PREFIX}Standing Desk Converter #{i}", "AssignToType": 2, "Value": 200} for i in range(1, 3)],
    # Webcams (2)
    *[{"Name": f"{TEST_NAME_PREFIX}Webcam Logitech #{i}", "AssignToType": 1, "Value": 80} for i in range(1, 3)],
]

# Discount Codes — §4i
DISCOUNT_CODES = [
    {"Code": "WELCOME20",  "Description": "20% off for new members",       "DiscountType": 1, "Value": 20,  "MaxUses": 50},
    {"Code": "TEAM10",     "Description": "10% off office tariffs",        "DiscountType": 1, "Value": 10,  "MaxUses": None},
    {"Code": "FREEMONTH",  "Description": "£350 off Dedicated Desk",       "DiscountType": 2, "Value": 350, "MaxUsesPerUser": 1},
    {"Code": "BOOKFREE",   "Description": "100% off meeting room booking", "DiscountType": 1, "Value": 100, "MaxUses": 20},
    {"Code": "PRODUCT15",  "Description": "15% off all products",          "DiscountType": 1, "Value": 15,  "MaxUses": None},
    {"Code": "LAUNCH50",   "Description": "£50 off plans + products",      "DiscountType": 2, "Value": 50,  "MaxUses": None, "Expired": True},
]

CRM_BOARDS = [
    {"Name": f"{TEST_NAME_PREFIX}New Business"},
    {"Name": f"{TEST_NAME_PREFIX}Expansion"},
]

# 5 columns per board — §4l
CRM_BOARD_COLUMNS = {
    "New Business": [
        {"Name": "Lead",           "WinOpportunity": False, "LoseOpportunity": False, "DisplayOrder": 1},
        {"Name": "Qualified",      "WinOpportunity": False, "LoseOpportunity": False, "DisplayOrder": 2},
        {"Name": "Proposal Sent",  "WinOpportunity": False, "LoseOpportunity": False, "DisplayOrder": 3},
        {"Name": "Won",            "WinOpportunity": True,  "LoseOpportunity": False, "DisplayOrder": 4},
        {"Name": "Lost",           "WinOpportunity": False, "LoseOpportunity": True,  "DisplayOrder": 5},
    ],
    "Expansion": [
        {"Name": "Upsell Lead",    "WinOpportunity": False, "LoseOpportunity": False, "DisplayOrder": 1},
        {"Name": "Negotiation",    "WinOpportunity": False, "LoseOpportunity": False, "DisplayOrder": 2},
        {"Name": "Proposal Sent",  "WinOpportunity": False, "LoseOpportunity": False, "DisplayOrder": 3},
        {"Name": "Won",            "WinOpportunity": True,  "LoseOpportunity": False, "DisplayOrder": 4},
        {"Name": "Lost",           "WinOpportunity": False, "LoseOpportunity": True,  "DisplayOrder": 5},
    ],
}

HELP_DESK_DEPARTMENTS = [
    {"Name": f"{TEST_NAME_PREFIX}IT Support", "Description": "WiFi, hardware, and access issues"},
    {"Name": f"{TEST_NAME_PREFIX}Facilities", "Description": "Building, cleaning, and maintenance issues"},
    {"Name": f"{TEST_NAME_PREFIX}Billing", "Description": "Invoices, payments, and account queries"},
]

# GroupAccess (eCommunityThreadVisibility): Restricted=1, Public=2, Private=3
COMMUNITY_GROUPS = [
    {"Name": f"{TEST_NAME_PREFIX}General", "Description": "General discussion for all members", "GroupAccess": 2},
    {"Name": f"{TEST_NAME_PREFIX}Networking", "Description": "Connect with other members", "GroupAccess": 2},
    {"Name": f"{TEST_NAME_PREFIX}Announcements", "Description": "Official space announcements", "GroupAccess": 1},
]

# Uses "Title", not "Name" — the one entity in this batch with a different key field.
CALENDAR_EVENT_CATEGORIES = [
    {"Title": f"{TEST_NAME_PREFIX}Workshop"},
    {"Title": f"{TEST_NAME_PREFIX}Networking"},
    {"Title": f"{TEST_NAME_PREFIX}Social"},
    {"Title": f"{TEST_NAME_PREFIX}Wellness"},
]


class StructuralGenerator(BaseGenerator):
    entity_name = "structural"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Populated during run
        self.team_ids = {}
        self.tariff_ids = {}
        self.product_ids = {}
        self.extra_service_ids = {}
        self.time_pass_ids = {}
        self.resource_ids = {}
        self.floor_plan_ids = {}
        self.floor_plan_desk_ids = {}
        self.inventory_asset_ids = {}
        self.discount_code_ids = {}
        self.crm_board_ids = {}
        self.crm_board_column_ids = {}
        self.help_desk_dept_ids = {}
        self.community_group_ids = {}
        self.event_category_ids = {}

    def run(self, nexudus_list, nexudus_create, layer0_output):
        """
        Execute Layer 1 creation.

        Args:
            nexudus_list: callable(entity, filters) -> list of records
            nexudus_create: callable(entity, body) -> created record
            layer0_output: dict from ReferenceGenerator.run()
        """
        biz = layer0_output["business_id"]
        cur = layer0_output["currency_id"]
        admin_id = layer0_output["admin_user_id"]
        tax_ids = layer0_output["tax_rate_ids"]
        fin_ids = layer0_output["fin_account_ids"]
        rt_ids = layer0_output["resource_type_ids"]

        # Teams
        self._create_simple_entities("teams", TEAMS, self.team_ids, biz, nexudus_list, nexudus_create,
                                     filter_key="Team_Business", extra_fields={"ActiveContracts": 0})

        # Tariffs
        self._create_tariffs(biz, cur, tax_ids, fin_ids, nexudus_list, nexudus_create)

        # Products
        self._create_products(biz, cur, tax_ids, fin_ids, nexudus_list, nexudus_create)

        # ExtraServices
        self._create_extra_services(biz, cur, tax_ids, fin_ids, rt_ids, nexudus_list, nexudus_create)

        # TimePasses
        self._create_time_passes(biz, cur, nexudus_list, nexudus_create)

        # Tariff benefits (day passes + time/printing credit allowances)
        self._create_tariff_benefits(nexudus_list, nexudus_create)

        # Resources
        self._create_resources(biz, rt_ids, nexudus_list, nexudus_create)

        # FloorPlans
        self._create_floor_plans(biz, nexudus_list, nexudus_create)

        # FloorPlanDesks
        self._create_floor_plan_desks(nexudus_list, nexudus_create)

        # InventoryAssets
        self._create_inventory_assets(biz, nexudus_list, nexudus_create)

        # DiscountCodes
        self._create_discount_codes(biz, nexudus_list, nexudus_create)

        # CRM Boards + Columns
        self._create_crm_boards(biz, nexudus_list, nexudus_create)

        # Simple entities
        self._create_simple_entities("helpdeskdepartments", HELP_DESK_DEPARTMENTS,
                                     self.help_desk_dept_ids, biz, nexudus_list, nexudus_create,
                                     filter_key="HelpDeskDepartment_Business")
        self._create_simple_entities("communitygroups", COMMUNITY_GROUPS,
                                     self.community_group_ids, biz, nexudus_list, nexudus_create,
                                     filter_key="CommunityGroup_Business",
                                     extra_fields={"UserId": admin_id})
        self._create_simple_entities("calendareventcategories", CALENDAR_EVENT_CATEGORIES,
                                     self.event_category_ids, biz, nexudus_list, nexudus_create,
                                     filter_key="CalendarEventCategory_Business",
                                     name_field="Title")

        self.log.info("Layer 1 complete.")
        return self._build_output(layer0_output)

    def _build_output(self, layer0_output):
        return {
            **layer0_output,
            "team_ids": self.team_ids,
            "tariff_ids": self.tariff_ids,
            "product_ids": self.product_ids,
            "extra_service_ids": self.extra_service_ids,
            "time_pass_ids": self.time_pass_ids,
            "resource_ids": self.resource_ids,
            "floor_plan_ids": self.floor_plan_ids,
            "floor_plan_desk_ids": self.floor_plan_desk_ids,
            "inventory_asset_ids": self.inventory_asset_ids,
            "discount_code_ids": self.discount_code_ids,
            "crm_board_ids": self.crm_board_ids,
            "crm_board_column_ids": self.crm_board_column_ids,
            "help_desk_dept_ids": self.help_desk_dept_ids,
            "community_group_ids": self.community_group_ids,
            "event_category_ids": self.event_category_ids,
        }

    # ------------------------------------------------------------------
    # Generic helper for simple BusinessId + Name entities
    # ------------------------------------------------------------------
    def _create_simple_entities(self, entity, definitions, id_map, business_id,
                                nexudus_list, nexudus_create, filter_key=None,
                                extra_fields=None, name_field="Name"):
        self.log.info("--- %s ---", entity)
        filters = {filter_key: business_id} if filter_key else {}
        existing = nexudus_list(entity, filters)
        existing_by_name = {r[name_field]: r["Id"] for r in existing}

        for defn in definitions:
            name = defn[name_field]
            if name in existing_by_name:
                self.log.info("'%s' already exists (id=%s)", name, existing_by_name[name])
                id_map[name] = existing_by_name[name]
                continue

            body = {**defn, "BusinessId": business_id}
            if extra_fields:
                body.update(extra_fields)

            if self.dry_run:
                self.log_would_create(entity, body)
                id_map[name] = f"DRY-{name}"
            else:
                result = nexudus_create(entity, body)
                id_map[name] = result["Id"]
                self.track_id({"entity": entity, "Id": result["Id"], "Name": name})
                self.log.info("Created '%s' (id=%s)", name, result["Id"])

    # ------------------------------------------------------------------
    # Tariffs
    # ------------------------------------------------------------------
    def _create_tariffs(self, biz, cur, tax_ids, fin_ids, nexudus_list, nexudus_create):
        self.log.info("--- Tariffs ---")
        existing = nexudus_list("tariffs", {"Tariff_Business": biz})
        existing_by_name = {r["Name"]: r["Id"] for r in existing}

        for defn in TARIFFS:
            name = defn["Name"]
            if name in existing_by_name:
                self.log.info("Tariff '%s' already exists (id=%s)", name, existing_by_name[name])
                self.tariff_ids[name] = existing_by_name[name]
                continue

            body = {
                "BusinessId": biz,
                "CurrencyId": cur,
                "Name": name,
                "SystemTariffType": defn["SystemTariffType"],
                "Price": defn["Price"],
                "TotalPrice": defn["Price"],
                "TotalSignUpPrice": 0,
                "InvoiceEvery": defn["InvoiceEvery"],
                "InvoiceEveryWeeks": defn["InvoiceEveryWeeks"],
                "CancellationPeriod": 30,
                "Visible": True,
                "FinancialAccountId": fin_ids.get(defn["FinAcctCode"]),
                "TaxRateId": tax_ids.get(defn["TaxRate"]),
            }

            if self.dry_run:
                self.log_would_create("tariffs", body)
                self.tariff_ids[name] = f"DRY-{name}"
            else:
                result = nexudus_create("tariffs", body)
                self.tariff_ids[name] = result["Id"]
                self.track_id({"entity": "tariffs", "Id": result["Id"], "Name": name})
                self.log.info("Created tariff '%s' (id=%s)", name, result["Id"])

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------
    def _create_products(self, biz, cur, tax_ids, fin_ids, nexudus_list, nexudus_create):
        self.log.info("--- Products ---")
        existing = nexudus_list("products", {"Product_Business": biz})
        existing_by_name = {r["Name"]: r["Id"] for r in existing}

        for idx, defn in enumerate(PRODUCTS):
            name = defn["Name"]
            if name in existing_by_name:
                self.log.info("Product '%s' already exists (id=%s)", name, existing_by_name[name])
                self.product_ids[name] = existing_by_name[name]
                continue

            body = {
                "BusinessId": biz,
                "CurrencyId": cur,
                "Name": name,
                "Description": defn["Description"],
                "Price": defn["Price"],
                "AvailableAs": defn["AvailableAs"],
                "SystemProductType": defn["SystemProductType"],
                "DisplayOrder": idx + 1,
                "FinancialAccountId": fin_ids.get(defn["FinAcctCode"]),
                "TaxRateId": tax_ids.get(defn["TaxRate"]),
            }

            if self.dry_run:
                self.log_would_create("products", body)
                self.product_ids[name] = f"DRY-{name}"
            else:
                result = nexudus_create("products", body)
                self.product_ids[name] = result["Id"]
                self.track_id({"entity": "products", "Id": result["Id"], "Name": name})
                self.log.info("Created product '%s' (id=%s)", name, result["Id"])

    # ------------------------------------------------------------------
    # ExtraServices
    # ------------------------------------------------------------------
    def _create_extra_services(self, biz, cur, tax_ids, fin_ids, rt_ids,
                                nexudus_list, nexudus_create):
        self.log.info("--- Extra Services ---")
        existing = nexudus_list("extraservices", {"ExtraService_Business": biz})
        existing_by_name = {r["Name"]: r["Id"] for r in existing}

        for idx, defn in enumerate(EXTRA_SERVICES):
            name = defn["Name"]
            if name in existing_by_name:
                self.log.info("ExtraService '%s' already exists (id=%s)", name, existing_by_name[name])
                self.extra_service_ids[name] = existing_by_name[name]
                continue

            body = {
                "BusinessId": biz,
                "CurrencyId": cur,
                "Name": name,
                "Price": defn["Price"],
                "ChargePeriod": defn["ChargePeriod"],
                "DisplayOrder": idx + 1,
                "LastMinuteAdjustmentType": 1,
                "FinancialAccountId": fin_ids.get(defn["FinAcctCode"]),
                "TaxRateId": tax_ids.get(defn["TaxRate"]),
                "ResourceTypes": [rt_ids.get(defn["ResourceType"])],
            }
            if defn.get("MaximumPrice") is not None:
                body["MaximumPrice"] = defn["MaximumPrice"]
            if defn.get("IsBookingCredit"):
                body["IsBookingCredit"] = True
            if defn.get("IsPrintingCredit"):
                body["IsPrintingCredit"] = True

            if self.dry_run:
                self.log_would_create("extraservices", body)
                self.extra_service_ids[name] = f"DRY-{name}"
            else:
                result = nexudus_create("extraservices", body)
                self.extra_service_ids[name] = result["Id"]
                self.track_id({"entity": "extraservices", "Id": result["Id"], "Name": name})
                self.log.info("Created extra service '%s' (id=%s)", name, result["Id"])

    # ------------------------------------------------------------------
    # TimePasses
    # ------------------------------------------------------------------
    def _create_time_passes(self, biz, cur, nexudus_list, nexudus_create):
        self.log.info("--- Time Passes ---")
        existing = nexudus_list("timepasses", {"TimePass_Business": biz})
        existing_by_name = {r["Name"]: r["Id"] for r in existing}

        for defn in TIME_PASSES:
            name = defn["Name"]
            if name in existing_by_name:
                self.log.info("TimePass '%s' already exists (id=%s)", name, existing_by_name[name])
                self.time_pass_ids[name] = existing_by_name[name]
                continue

            body = {"BusinessId": biz, "CurrencyId": cur, **defn}

            if self.dry_run:
                self.log_would_create("timepasses", body)
                self.time_pass_ids[name] = f"DRY-{name}"
            else:
                result = nexudus_create("timepasses", body)
                self.time_pass_ids[name] = result["Id"]
                self.track_id({"entity": "timepasses", "Id": result["Id"], "Name": name})
                self.log.info("Created time pass '%s' (id=%s)", name, result["Id"])

    # ------------------------------------------------------------------
    # Tariff benefits — TariffTimePass (day passes) + TariffExtraService
    # (time/printing credit allowances) per plan. TariffExtraService
    # requires the target ExtraService to have IsBookingCredit/
    # IsPrintingCredit set (see EXTRA_SERVICES).
    # ------------------------------------------------------------------
    def _create_tariff_benefits(self, nexudus_list, nexudus_create):
        self.log.info("--- Tariff Benefits ---")
        day_pass_id = self.time_pass_ids.get(f"{TEST_NAME_PREFIX}Day Pass")
        time_credit_id = self.extra_service_ids.get(f"{TEST_NAME_PREFIX}Time Credit")
        printing_credit_id = self.extra_service_ids.get(f"{TEST_NAME_PREFIX}Printing Credit")

        for tariff_short_name, benefits in TARIFF_BENEFITS.items():
            tariff_name = f"{TEST_NAME_PREFIX}{tariff_short_name}"
            tariff_id = self.tariff_ids.get(tariff_name)
            if tariff_id is None:
                continue
            renewal = benefits["renewal"]

            if benefits.get("day_passes") and day_pass_id is not None:
                self._create_tariff_time_pass(tariff_id, tariff_name, day_pass_id,
                                              benefits["day_passes"], renewal, nexudus_list, nexudus_create)
            if benefits.get("time_credit_minutes") and time_credit_id is not None:
                self._create_tariff_extra_service(tariff_id, tariff_name, time_credit_id, "Time Credit",
                                                  benefits["time_credit_minutes"], renewal,
                                                  nexudus_list, nexudus_create)
            if benefits.get("printing_pages") and printing_credit_id is not None:
                self._create_tariff_extra_service(tariff_id, tariff_name, printing_credit_id, "Printing Credit",
                                                  benefits["printing_pages"], renewal,
                                                  nexudus_list, nexudus_create)

    def _create_tariff_time_pass(self, tariff_id, tariff_name, time_pass_id, passes_included,
                                  renewal, nexudus_list, nexudus_create):
        existing = nexudus_list("tarifftimepasses", {"TariffTimePass_Tariff": tariff_id})
        if any(r.get("TimePassId") == time_pass_id for r in existing):
            self.log.info("'%s' already has the Day Pass benefit", tariff_name)
            return

        body = {
            "TariffId": tariff_id, "TimePassId": time_pass_id,
            "PassesIncluded": passes_included, "PassRenewalTime": renewal,
        }
        if self.dry_run:
            self.log_would_create("tarifftimepasses", body)
        else:
            result = nexudus_create("tarifftimepasses", body)
            self.track_id({"entity": "tarifftimepasses", "Id": result["Id"], "Tariff": tariff_name})
            self.log.info("Added Day Pass benefit (%dx) to '%s' (id=%s)",
                          passes_included, tariff_name, result["Id"])

    def _create_tariff_extra_service(self, tariff_id, tariff_name, extra_service_id, label,
                                      uses_included, renewal, nexudus_list, nexudus_create):
        existing = nexudus_list("tariffextraservices", {"TariffExtraService_Tariff": tariff_id})
        if any(r.get("ExtraServiceId") == extra_service_id for r in existing):
            self.log.info("'%s' already has the %s benefit", tariff_name, label)
            return

        body = {
            "TariffId": tariff_id, "ExtraServiceId": extra_service_id,
            "UsesIncluded": uses_included, "ServiceRenewalTime": renewal,
        }
        if self.dry_run:
            self.log_would_create("tariffextraservices", body)
        else:
            result = nexudus_create("tariffextraservices", body)
            self.track_id({"entity": "tariffextraservices", "Id": result["Id"], "Tariff": tariff_name})
            self.log.info("Added %s benefit (%d) to '%s' (id=%s)",
                          label, uses_included, tariff_name, result["Id"])

    # ------------------------------------------------------------------
    # Resources
    # ------------------------------------------------------------------
    def _create_resources(self, biz, rt_ids, nexudus_list, nexudus_create):
        self.log.info("--- Resources ---")
        existing = nexudus_list("resources", {"Resource_Business": biz})
        existing_by_name = {r["Name"]: r["Id"] for r in existing}

        for idx, defn in enumerate(RESOURCES):
            name = defn["Name"]
            if name in existing_by_name:
                self.log.info("Resource '%s' already exists (id=%s)", name, existing_by_name[name])
                self.resource_ids[name] = existing_by_name[name]
                continue

            body = {
                "BusinessId": biz,
                "Name": name,
                "SystemResourceType": defn["SystemResourceType"],
                "ResourceTypeId": rt_ids.get(defn["ResourceType"]),
                "DisplayOrder": idx + 1,
                "CancellationFeeType": 1,
            }

            if self.dry_run:
                self.log_would_create("resources", body)
                self.resource_ids[name] = f"DRY-{name}"
            else:
                result = nexudus_create("resources", body)
                self.resource_ids[name] = result["Id"]
                self.track_id({"entity": "resources", "Id": result["Id"], "Name": name})
                self.log.info("Created resource '%s' (id=%s)", name, result["Id"])

    # ------------------------------------------------------------------
    # FloorPlans
    # ------------------------------------------------------------------
    def _create_floor_plans(self, biz, nexudus_list, nexudus_create):
        self.log.info("--- Floor Plans ---")
        existing = nexudus_list("floorplans", {"FloorPlan_Business": biz})
        existing_by_name = {r["Name"]: r["Id"] for r in existing}

        for defn in FLOOR_PLANS:
            name = defn["Name"]
            if name in existing_by_name:
                self.log.info("FloorPlan '%s' already exists (id=%s)", name, existing_by_name[name])
                self.floor_plan_ids[name] = existing_by_name[name]
                continue

            body = {
                "BusinessId": biz,
                "Name": name,
                "FloorLevel": defn["FloorLevel"],
                "BackgroundScale": 100,
                "PositionX": 0,
                "PositionY": 0,
                "Scale": 1.0,
            }

            if self.dry_run:
                self.log_would_create("floorplans", body)
                self.floor_plan_ids[name] = f"DRY-{name}"
            else:
                result = nexudus_create("floorplans", body)
                self.floor_plan_ids[name] = result["Id"]
                self.track_id({"entity": "floorplans", "Id": result["Id"], "Name": name})
                self.log.info("Created floor plan '%s' (id=%s)", name, result["Id"])

    # ------------------------------------------------------------------
    # FloorPlanDesks
    # ------------------------------------------------------------------
    def _create_floor_plan_desks(self, nexudus_list, nexudus_create):
        self.log.info("--- Floor Plan Desks ---")

        for idx, defn in enumerate(FLOOR_PLAN_DESKS):
            name = defn["Name"]
            if self.already_created("Name", name):
                existing = next(r for r in self.get_tracked_ids()
                                 if r.get("entity") == "floorplandesks" and r.get("Name") == name)
                self.floor_plan_desk_ids[name] = existing["Id"]
                self.log.info("Desk '%s' already tracked (id=%s)", name, existing["Id"])
                continue

            fp_name = f"{TEST_NAME_PREFIX}{defn['FloorPlan']}"
            fp_id = self.floor_plan_ids.get(fp_name)

            body = {
                "FloorPlanId": fp_id,
                "Name": name,
                "ItemType": defn["ItemType"],
                "Size": defn["Size"],
                "Capacity": defn["Capacity"],
                "Price": defn["Price"],
                "Area": defn.get("Area", ""),
                "PositionX": (idx % 10) * 100,
                "PositionY": (idx // 10) * 100,
                "PositionZ": 0,
            }

            if self.dry_run:
                self.log_would_create("floorplandesks", body)
                self.floor_plan_desk_ids[name] = f"DRY-{name}"
            else:
                result = nexudus_create("floorplandesks", body)
                self.floor_plan_desk_ids[name] = result["Id"]
                self.track_id({"entity": "floorplandesks", "Id": result["Id"], "Name": name})
                self.log.info("Created desk '%s' (id=%s)", name, result["Id"])

    # ------------------------------------------------------------------
    # InventoryAssets
    # ------------------------------------------------------------------
    def _create_inventory_assets(self, biz, nexudus_list, nexudus_create):
        self.log.info("--- Inventory Assets ---")
        existing = nexudus_list("inventoryassets", {"InventoryAsset_Business": biz})
        existing_by_name = {r["Name"]: r["Id"] for r in existing}

        # AssignToType=3 (FloorPlanItem) requires FloorPlanDeskId; =2 (Resource)
        # requires ResourceId — cycle through what Layer 1 already created.
        desk_ids = list(self.floor_plan_desk_ids.values())
        resource_ids = list(self.resource_ids.values())
        desk_i = resource_i = 0

        for defn in INVENTORY_ASSETS:
            name = defn["Name"]
            if name in existing_by_name:
                self.log.info("Asset '%s' already exists (id=%s)", name, existing_by_name[name])
                self.inventory_asset_ids[name] = existing_by_name[name]
                continue

            body = {
                "BusinessId": biz,
                "Name": name,
                "AssignToType": defn["AssignToType"],
                "Value": defn["Value"],
            }
            if defn["AssignToType"] == 3 and desk_ids:
                body["FloorPlanDeskId"] = desk_ids[desk_i % len(desk_ids)]
                desk_i += 1
            elif defn["AssignToType"] == 2 and resource_ids:
                body["ResourceId"] = resource_ids[resource_i % len(resource_ids)]
                resource_i += 1

            if self.dry_run:
                self.log_would_create("inventoryassets", body)
                self.inventory_asset_ids[name] = f"DRY-{name}"
            else:
                result = nexudus_create("inventoryassets", body)
                self.inventory_asset_ids[name] = result["Id"]
                self.track_id({"entity": "inventoryassets", "Id": result["Id"], "Name": name})
                self.log.info("Created asset '%s' (id=%s)", name, result["Id"])

    # ------------------------------------------------------------------
    # DiscountCodes
    # ------------------------------------------------------------------
    def _create_discount_codes(self, biz, nexudus_list, nexudus_create):
        self.log.info("--- Discount Codes ---")
        existing = nexudus_list("discountcodes", {"DiscountCode_Business": biz})
        existing_by_code = {r.get("Code", ""): r["Id"] for r in existing}

        for defn in DISCOUNT_CODES:
            code = defn["Code"]
            if code in existing_by_code:
                self.log.info("Discount '%s' already exists (id=%s)", code, existing_by_code[code])
                self.discount_code_ids[code] = existing_by_code[code]
                continue

            body = {
                "BusinessId": biz,
                "Code": code,
                "Description": defn["Description"],
            }
            if defn.get("MaxUses"):
                body["MaxUses"] = defn["MaxUses"]
            if defn.get("MaxUsesPerUser"):
                body["MaxUsesPerUser"] = defn["MaxUsesPerUser"]

            if self.dry_run:
                self.log_would_create("discountcodes", body)
                self.discount_code_ids[code] = f"DRY-{code}"
            else:
                result = nexudus_create("discountcodes", body)
                self.discount_code_ids[code] = result["Id"]
                self.track_id({"entity": "discountcodes", "Id": result["Id"], "Code": code})
                self.log.info("Created discount '%s' (id=%s)", code, result["Id"])

    # ------------------------------------------------------------------
    # CRM Boards + Columns
    # ------------------------------------------------------------------
    def _create_crm_boards(self, biz, nexudus_list, nexudus_create):
        self.log.info("--- CRM Boards ---")
        existing = nexudus_list("crmboards", {"CrmBoard_Business": biz})
        existing_by_name = {r["Name"]: r["Id"] for r in existing}

        for defn in CRM_BOARDS:
            name = defn["Name"]
            if name in existing_by_name:
                self.log.info("Board '%s' already exists (id=%s)", name, existing_by_name[name])
                self.crm_board_ids[name] = existing_by_name[name]
            elif self.dry_run:
                self.log_would_create("crmboards", {**defn, "BusinessId": biz})
                self.crm_board_ids[name] = f"DRY-{name}"
            else:
                result = nexudus_create("crmboards", {**defn, "BusinessId": biz})
                self.crm_board_ids[name] = result["Id"]
                self.track_id({"entity": "crmboards", "Id": result["Id"], "Name": name})
                self.log.info("Created board '%s' (id=%s)", name, result["Id"])

        # Columns
        self.log.info("--- CRM Board Columns ---")
        for board_short_name, columns in CRM_BOARD_COLUMNS.items():
            board_name = f"{TEST_NAME_PREFIX}{board_short_name}"
            board_id = self.crm_board_ids.get(board_name)

            existing_cols = nexudus_list("crmboardcolumns", {"CrmBoardColumn_CrmBoard": board_id})
            existing_cols_by_name = {r["Name"]: r["Id"] for r in existing_cols}

            for col_defn in columns:
                col_name = col_defn["Name"]
                full_key = f"{board_short_name}/{col_name}"

                if col_name in existing_cols_by_name:
                    self.crm_board_column_ids[full_key] = existing_cols_by_name[col_name]
                    self.log.info("Column '%s' already exists (id=%s)",
                                  full_key, existing_cols_by_name[col_name])
                    continue

                body = {
                    "CrmBoardId": board_id,
                    "Name": col_name,
                    "WinOpportunity": col_defn["WinOpportunity"],
                    "LoseOpportunity": col_defn["LoseOpportunity"],
                    "Position": col_defn["DisplayOrder"],
                }

                if self.dry_run:
                    self.log_would_create("crmboardcolumns", body)
                    self.crm_board_column_ids[full_key] = f"DRY-{full_key}"
                else:
                    result = nexudus_create("crmboardcolumns", body)
                    self.crm_board_column_ids[full_key] = result["Id"]
                    self.track_id({"entity": "crmboardcolumns", "Id": result["Id"],
                                   "Board": board_short_name, "Name": col_name})
                    self.log.info("Created column '%s' (id=%s)", full_key, result["Id"])


if __name__ == "__main__":
    args = parse_args()
    gen = StructuralGenerator(dry_run=args.dry_run)

    if gen.dry_run:
        import importlib
        ref = importlib.import_module("generators.00_reference")
        mock_layer0 = {
            "business_id": "DRY-BIZ-1",
            "currency_id": "DRY-CUR-1",
            "country_id": "DRY-COUNTRY-1",
            "timezone_id": "DRY-TZ-1",
            "admin_user_id": "DRY-ADMIN-1",
            "tax_rate_ids": {"Standard": "DRY-TAX-STD", "Reduced": "DRY-TAX-RED", "Zero-rated": "DRY-TAX-ZERO"},
            "fin_account_ids": {c["Code"]: f"DRY-FA-{c['Code']}" for c in ref.FINANCIAL_ACCOUNTS},
            "resource_type_ids": {r["Name"]: f"DRY-RT-{r['Name']}" for r in ref.RESOURCE_TYPES},
        }
        gen.run(
            nexudus_list=lambda entity, filters: [],
            nexudus_create=lambda entity, body: {"Id": f"DRY-{entity}-{body.get('Name', body.get('Code', 'x'))}"},
            layer0_output=mock_layer0,
        )
    else:
        import pipeline
        pipeline.run_up_to(1)
