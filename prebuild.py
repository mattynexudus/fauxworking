"""
Pre-generate all faker-dependent data into static JSON files.

Run this once (or when you want to regenerate with a new seed/volume).
The generated files are committed to the repo — generators read them at runtime.

Usage:
    python prebuild.py               # Generate all data files
    python prebuild.py --seed 123    # Use a different seed
"""

import argparse
import json
import random
import sys
from datetime import timedelta
from pathlib import Path

from faker import Faker

from config import (
    DATA_DIR,
    RANDOM_SEED,
    TEST_EMAIL_DOMAIN,
    TEST_EMAIL_PREFIX,
    TEST_NAME_PREFIX,
    TODAY,
    VOLUMES,
)

# ---------------------------------------------------------------------------
# Lifecycle + engagement definitions (shared with generators)
# ---------------------------------------------------------------------------

LIFECYCLE_SCENARIOS = [
    ("long_term_active", 20),
    ("new_joiner",        8),
    ("plan_change",       6),
    ("churned",          10),
    ("returned",          4),
    ("multi_contract",    6),
    ("unsubscribed",      3),
    ("ending_soon",       3),
]

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

TEAM_NAMES = [
    f"{TEST_NAME_PREFIX}Acme Corp",
    f"{TEST_NAME_PREFIX}Bright Studio",
    f"{TEST_NAME_PREFIX}CloudNine Labs",
    f"{TEST_NAME_PREFIX}Delta Ventures",
    f"{TEST_NAME_PREFIX}Echo Digital",
]


def _attendance_pattern(rng, scenario):
    """Generate weekday attendance pattern as dict of enum values."""
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    if scenario in ("long_term_active", "multi_contract"):
        return {d: rng.choice([1, 1, 1, 2]) for d in days}
    elif scenario in ("new_joiner", "returned"):
        return {d: rng.choice([1, 1, 2, 2, 5]) for d in days}
    elif scenario in ("churned", "ending_soon"):
        return {d: rng.choice([4, 5, 5, 2]) for d in days}
    else:
        return {d: rng.choice([1, 2, 5]) for d in days}


def generate_coworkers(rng, fake):
    """Generate coworker profile definitions."""
    coworkers = []
    idx = 0

    for scenario, count in LIFECYCLE_SCENARIOS:
        for _ in range(count):
            idx += 1
            gender_val = rng.choice([2, 3, 4, 5])
            if gender_val == 2:
                first = fake.first_name_male()
            elif gender_val == 3:
                first = fake.first_name_female()
            else:
                first = fake.first_name()
            last = fake.last_name()

            team = rng.choice(TEAM_NAMES) if idx <= 40 else None
            churn, engagement = ENGAGEMENT_PROFILES[scenario]
            churn = round(max(0, min(1, churn + rng.uniform(-0.1, 0.1))), 2)

            coworkers.append({
                "index": idx,
                "FullName": f"{first} {last}",
                "Email": f"{TEST_EMAIL_PREFIX}{idx:03d}@{TEST_EMAIL_DOMAIN}",
                "Gender": gender_val,
                "Team": team,
                "Scenario": scenario,
                "ChurnProbability": churn,
                "EngagementLevel": engagement,
                "Attendance": _attendance_pattern(rng, scenario),
            })

    return coworkers


def generate_visitors(rng, fake, coworker_count):
    """Generate visitor definitions with day offsets instead of absolute dates."""
    visitors = []
    sources = [1, 1, 2, 2, 2, 3]
    statuses = [1, 1, 1, 5, 5, 2]

    for i in range(1, VOLUMES["visitors"] + 1):
        host_idx = rng.randint(1, coworker_count) if rng.random() < 0.7 else None

        visitors.append({
            "index": i,
            "FullName": fake.name(),
            "Email": f"visitor-{i:03d}@{TEST_EMAIL_DOMAIN}",
            "VisitorSource": rng.choice(sources),
            "HostApprovalStatus": rng.choice(statuses),
            "HostCoworkerIndex": host_idx,
            "ArrivalDayOffset": rng.randint(-180, 30),
            "ArrivalHour": rng.randint(8, 17),
        })

    return visitors


# ---------------------------------------------------------------------------
# Layer 3 — Contracts & occupancy (shared with 03_contracts.py)
# ---------------------------------------------------------------------------

# (name, price, category) — mirrors generators/01_structural.py TARIFFS
TARIFFS_INFO = [
    ("Hot Desk Monthly",       150.00,  "desk"),
    ("Dedicated Desk Monthly", 350.00,  "desk"),
    ("Private Office Small",   1200.00, "office"),
    ("Private Office Large",   2000.00, "office"),
    ("Hot Desk Quarterly",     400.00,  "desk"),
    ("Private Office Annual",  12000.00, "office"),
    ("Flex Weekly",            50.00,   "flex"),
    ("Flex Fortnightly",       250.00,  "flex"),
]
TARIFF_PRICE_BY_NAME = {name: price for name, price, _cat in TARIFFS_INFO}

RECURRING_PRODUCTS = ["Storage Locker", "Parking Space", "Mail Handling"]

OFFICE_DESK_NAMES = [f"{TEST_NAME_PREFIX}Office Unit {i:02d}" for i in range(1, 11)]
DEDICATED_DESK_NAMES = [f"{TEST_NAME_PREFIX}Desk {i:02d}" for i in range(11, 23)]
HOT_DESK_NAMES = [f"{TEST_NAME_PREFIX}Hot Desk {i:02d}" for i in range(23, 33)]
STORAGE_DESK_NAMES = [f"{TEST_NAME_PREFIX}Storage {i:02d}" for i in range(33, 37)]
ROOM_DESK_NAMES = [f"{TEST_NAME_PREFIX}Room {i:02d}" for i in range(37, 41)]

LOCKER_ASSET_NAMES = [f"{TEST_NAME_PREFIX}Locker A-{i:02d}" for i in range(1, 9)]
MONITOR_ASSET_NAMES = [f'{TEST_NAME_PREFIX}Monitor Dell 27" #{i}' for i in range(1, 4)]
STANDING_DESK_ASSET_NAMES = [f"{TEST_NAME_PREFIX}Standing Desk Converter #{i}" for i in range(1, 3)]

# eCancellationReason values (see reference/field-enums.md style docs)
REASON_UPGRADE_DOWNGRADE = [12, 13]
REASON_GENERIC_CHURN = [1, 2, 3, 4, 6, 7, 99]
REASON_RETURN_TRIGGER = [2, 3, 9, 99]


def _pick_tariff(rng, weights):
    """Pick a (name, price, category) tuple biased by category weight."""
    cats = list(weights.keys())
    w = list(weights.values())
    cat = rng.choices(cats, weights=w, k=1)[0]
    options = [t for t in TARIFFS_INFO if t[2] == cat]
    return rng.choice(options)


def _base_tariff_name(contract):
    return contract["TariffName"][len(TEST_NAME_PREFIX):]


def _is_active(contract):
    """True if the contract has no cancellation, or is cancelled in the future."""
    c = contract["CancellationDayOffset"]
    return c is None or c > 0


def generate_contracts(rng, coworkers):
    """Generate CoworkerContract definitions per §4a lifecycle scenarios."""
    contracts = []
    idx = 0

    def make(coworker_index, tariff, scenario, start_offset, cancel_offset=None, reason=None):
        name, _price, cat = tariff
        return {
            "index": None,  # filled by caller
            "CoworkerIndex": coworker_index,
            "TariffName": f"{TEST_NAME_PREFIX}{name}",
            "TariffCategory": cat,
            "Scenario": scenario,
            "StartDayOffset": start_offset,
            "CancellationDayOffset": cancel_offset,
            "CancellationReason": reason,
            "ContractTermMonths": None,
            "BillingDay": rng.randint(1, 28),
            "PriceOverride": None,
            "ValueOverride": None,
        }

    def add(c):
        nonlocal idx
        idx += 1
        c["index"] = idx
        contracts.append(c)

    # Padding counters — a handful of coworkers get an extra contract beyond
    # their base scenario pattern, bringing the total from 79 to ~90 while
    # staying inside each scenario's narrative (see §4a).
    long_term_extra = 0   # target 3: a second small add-on plan (e.g. parking)
    churned_extra = 0     # target 2: an earlier plan they had before the one that churned
    multi_extra = 0       # target 3: a third concurrent contract
    ending_soon_extra = 0  # target 3 (all of them): the plan they renewed from

    for cw in coworkers:
        scenario = cw["Scenario"]
        cw_idx = cw["index"]

        if scenario == "long_term_active":
            t = _pick_tariff(rng, {"office": 0.35, "desk": 0.55, "flex": 0.10})
            main_start = rng.randint(-730, -365)
            add(make(cw_idx, t, scenario, main_start))

            if long_term_extra < 3:
                t_extra = _pick_tariff(rng, {"flex": 0.6, "desk": 0.4})
                add(make(cw_idx, t_extra, scenario, rng.randint(main_start + 30, -60)))
                long_term_extra += 1

        elif scenario == "new_joiner":
            t = _pick_tariff(rng, {"office": 0.10, "desk": 0.60, "flex": 0.30})
            add(make(cw_idx, t, scenario, rng.randint(-90, -3)))

        elif scenario == "plan_change":
            t1 = _pick_tariff(rng, {"desk": 0.7, "flex": 0.3})
            a_start = rng.randint(-500, -300)
            a_end = rng.randint(-200, -60)
            add(make(cw_idx, t1, scenario, a_start, cancel_offset=a_end,
                      reason=rng.choice(REASON_UPGRADE_DOWNGRADE)))
            t2 = _pick_tariff(rng, {"office": 0.5, "desk": 0.5})
            b_start = a_end + rng.choice([0, 1, 3, 7])
            add(make(cw_idx, t2, scenario, min(b_start, -1)))

        elif scenario == "churned":
            t = _pick_tariff(rng, {"office": 0.2, "desk": 0.6, "flex": 0.2})
            start = rng.randint(-600, -200)
            end = rng.randint(-180, -10)

            if churned_extra < 2:
                t_prior = _pick_tariff(rng, {"desk": 0.7, "flex": 0.3})
                prior_start = rng.randint(start - 300, start - 90)
                add(make(cw_idx, t_prior, scenario, prior_start, cancel_offset=start,
                          reason=rng.choice(REASON_UPGRADE_DOWNGRADE)))
                churned_extra += 1

            add(make(cw_idx, t, scenario, start, cancel_offset=end,
                      reason=rng.choice(REASON_GENERIC_CHURN)))

        elif scenario == "returned":
            t1 = _pick_tariff(rng, {"desk": 0.6, "flex": 0.4})
            old_start = rng.randint(-700, -420)
            old_end = rng.randint(-360, -190)
            add(make(cw_idx, t1, scenario, old_start, cancel_offset=old_end,
                      reason=rng.choice(REASON_RETURN_TRIGGER)))
            t2 = _pick_tariff(rng, {"office": 0.3, "desk": 0.7})
            add(make(cw_idx, t2, scenario, rng.randint(-60, -5)))

        elif scenario == "multi_contract":
            t1 = _pick_tariff(rng, {"office": 0.5, "desk": 0.5})
            t2 = _pick_tariff(rng, {"desk": 0.6, "flex": 0.4})
            add(make(cw_idx, t1, scenario, rng.randint(-400, -90)))
            add(make(cw_idx, t2, scenario, rng.randint(-300, -30)))

            if multi_extra < 3:
                t3 = _pick_tariff(rng, {"flex": 0.7, "desk": 0.3})
                add(make(cw_idx, t3, scenario, rng.randint(-200, -20)))
                multi_extra += 1

        elif scenario == "unsubscribed":
            t1 = _pick_tariff(rng, {"desk": 0.5, "office": 0.5})
            start1 = rng.randint(-500, -250)
            end1 = rng.randint(-150, -30)
            add(make(cw_idx, t1, scenario, start1, cancel_offset=end1,
                      reason=rng.choice(REASON_GENERIC_CHURN)))
            t2 = _pick_tariff(rng, {"desk": 0.6, "flex": 0.4})
            add(make(cw_idx, t2, scenario, rng.randint(-400, -60)))

        elif scenario == "ending_soon":
            t = _pick_tariff(rng, {"office": 0.3, "desk": 0.6, "flex": 0.1})
            start = rng.randint(-400, -120)

            if ending_soon_extra < 3:
                t_prior = _pick_tariff(rng, {"desk": 0.7, "flex": 0.3})
                prior_start = rng.randint(start - 250, start - 60)
                add(make(cw_idx, t_prior, scenario, prior_start, cancel_offset=start,
                          reason=rng.choice([11])))  # Renewed
                ending_soon_extra += 1

            add(make(cw_idx, t, scenario, start, cancel_offset=rng.randint(30, 90),
                      reason=rng.choice(REASON_GENERIC_CHURN)))

    # ~30% get a ContractTerm (minimum term), biased toward currently-active contracts
    term_pool = [c for c in contracts if _is_active(c) and c["TariffCategory"] != "flex"]
    rng.shuffle(term_pool)
    for c in term_pool[:round(len(contracts) * 0.30)]:
        if c["TariffCategory"] == "office":
            c["ContractTermMonths"] = rng.choice([12, 12, 24])
        else:
            c["ContractTermMonths"] = 6

    # ~20 contracts get a Value (benchmark) that differs from the discounted Price
    value_pool = [c for c in contracts if _is_active(c)]
    rng.shuffle(value_pool)
    for c in value_pool[:20]:
        tariff_price = TARIFF_PRICE_BY_NAME[_base_tariff_name(c)]
        discount = rng.uniform(0.05, 0.15)
        c["PriceOverride"] = round(tariff_price * (1 - discount), 2)
        c["ValueOverride"] = tariff_price

    return contracts


def generate_contract_products(rng, contracts):
    """~30 recurring ContractProduct add-ons on active contracts."""
    pool = [c for c in contracts if _is_active(c)]
    rng.shuffle(pool)
    out = []
    for i, c in enumerate(pool[:30], start=1):
        product = rng.choice(RECURRING_PRODUCTS)
        out.append({
            "index": i,
            "ContractIndex": c["index"],
            "ProductName": f"{TEST_NAME_PREFIX}{product}",
            "Quantity": 1,
        })
    return out


def generate_contract_schedules(rng, contracts):
    """8 future price-change schedules on active, non-flex contracts."""
    pool = [c for c in contracts if _is_active(c) and c["TariffCategory"] != "flex"]
    rng.shuffle(pool)
    out = []
    for i, c in enumerate(pool[:8], start=1):
        base_price = TARIFF_PRICE_BY_NAME[_base_tariff_name(c)]
        out.append({
            "index": i,
            "ContractIndex": c["index"],
            "ApplyOnDayOffset": rng.randint(30, 180),
            "NewPrice": round(base_price * rng.uniform(1.05, 1.12), 2),
        })
    return out


def generate_contract_paused_periods(rng, contracts):
    """12 freezes (past/current/future) aligned to month boundaries, monthly plans only."""
    pool = [c for c in contracts if c["TariffCategory"] in ("office", "desk")]
    rng.shuffle(pool)
    selected = pool[:12]
    buckets = (["past"] * 4 + ["current"] * 4 + ["future"] * 4)[:len(selected)]
    rng.shuffle(buckets)

    out = []
    for i, (c, bucket) in enumerate(zip(selected, buckets), start=1):
        if bucket == "past":
            from_month, duration = -rng.randint(6, 12), rng.randint(1, 3)
        elif bucket == "current":
            from_month, duration = -rng.randint(1, 3), rng.randint(2, 4)
        else:
            from_month, duration = rng.randint(1, 4), rng.randint(1, 3)
        out.append({
            "index": i,
            "ContractIndex": c["index"],
            "PauseFromMonthOffset": from_month,
            "DurationMonths": duration,
            "Bucket": bucket,
        })
    return out


def generate_contract_deposits(rng, contracts):
    """10 deposits: 6 office (£1000) + 4 desk (£250), mostly refundable — §4k."""
    office_pool = [c for c in contracts if c["TariffCategory"] == "office"]
    desk_pool = [c for c in contracts if c["TariffCategory"] == "desk"]
    rng.shuffle(office_pool)
    rng.shuffle(desk_pool)

    out = []
    idx = 0

    office_refundable = [True, True, True, True, False, False]
    rng.shuffle(office_refundable)
    for c, refundable in zip(office_pool[:6], office_refundable):
        idx += 1
        out.append({
            "index": idx, "ContractIndex": c["index"],
            "ProductName": f"{TEST_NAME_PREFIX}Security Deposit - Office",
            "Price": 1000.00, "Refundable": refundable,
        })

    desk_refundable = [True, True, True, False]
    rng.shuffle(desk_refundable)
    for c, refundable in zip(desk_pool[:4], desk_refundable):
        idx += 1
        out.append({
            "index": idx, "ContractIndex": c["index"],
            "ProductName": f"{TEST_NAME_PREFIX}Security Deposit - Desk",
            "Price": 250.00, "Refundable": refundable,
        })

    return out


def generate_coworker_inventory_assets(rng, coworkers):
    """12 locker/monitor/standing-desk assignments — §4n. Excludes churned coworkers."""
    pool = [cw for cw in coworkers if cw["Scenario"] != "churned"]
    rng.shuffle(pool)

    out = []
    idx = 0

    for name, cw in zip(LOCKER_ASSET_NAMES[:6], pool[0:6]):
        idx += 1
        out.append({
            "index": idx, "AssetName": name, "CoworkerIndex": cw["index"],
            "AssignedFromDayOffset": rng.randint(-300, -30), "AssignedToDayOffset": None,
        })

    for name, cw in zip(LOCKER_ASSET_NAMES[6:8], pool[6:8]):
        idx += 1
        assigned_from = rng.randint(-400, -200)
        assigned_to = min(assigned_from + rng.randint(30, 120), -10)
        out.append({
            "index": idx, "AssetName": name, "CoworkerIndex": cw["index"],
            "AssignedFromDayOffset": assigned_from, "AssignedToDayOffset": assigned_to,
        })

    for name, cw in zip(MONITOR_ASSET_NAMES, pool[8:11]):
        idx += 1
        out.append({
            "index": idx, "AssetName": name, "CoworkerIndex": cw["index"],
            "AssignedFromDayOffset": rng.randint(-200, -10), "AssignedToDayOffset": None,
        })

    if len(pool) > 11:
        idx += 1
        out.append({
            "index": idx, "AssetName": STANDING_DESK_ASSET_NAMES[0], "CoworkerIndex": pool[11]["index"],
            "AssignedFromDayOffset": rng.randint(-150, -10), "AssignedToDayOffset": None,
        })

    return out


def generate_desk_assignments(rng, contracts):
    """~28 FloorPlanDesk.CoworkerId occupancy assignments — §4d."""
    active = [c for c in contracts if _is_active(c)]

    office_contracts = [c for c in active if _base_tariff_name(c) in
                         ("Private Office Small", "Private Office Large", "Private Office Annual")]
    dedicated_contracts = [c for c in active if _base_tariff_name(c) == "Dedicated Desk Monthly"]
    hotdesk_contracts = [c for c in active if _base_tariff_name(c) in
                          ("Hot Desk Monthly", "Hot Desk Quarterly", "Flex Weekly", "Flex Fortnightly")]
    rng.shuffle(office_contracts)
    rng.shuffle(dedicated_contracts)
    rng.shuffle(hotdesk_contracts)

    out = []
    idx = 0

    def assign(desk_names, occupied_count, contract_pool):
        nonlocal idx
        for name, c in zip(desk_names[:occupied_count], contract_pool):
            idx += 1
            out.append({"index": idx, "DeskName": name, "CoworkerIndex": c["CoworkerIndex"]})

    assign(OFFICE_DESK_NAMES, 8, office_contracts)
    assign(DEDICATED_DESK_NAMES, 9, dedicated_contracts)
    assign(HOT_DESK_NAMES, 6, hotdesk_contracts)

    active_coworker_ids = list({c["CoworkerIndex"] for c in active})
    rng.shuffle(active_coworker_ids)
    for name, cw_idx in zip(STORAGE_DESK_NAMES[:3], active_coworker_ids[:3]):
        idx += 1
        out.append({"index": idx, "DeskName": name, "CoworkerIndex": cw_idx})
    for name, cw_idx in zip(ROOM_DESK_NAMES[:2], active_coworker_ids[3:5]):
        idx += 1
        out.append({"index": idx, "DeskName": name, "CoworkerIndex": cw_idx})

    return out


# ---------------------------------------------------------------------------
# Layer 4a — Activity: bookings, check-ins, credits, passes (shared with
# generators/04_activity.py)
# ---------------------------------------------------------------------------

# name lists mirror generators/01_structural.py RESOURCES, grouped by category,
# each paired with the ExtraService that represents its per-use booking rate.
RESOURCE_CATEGORIES = {
    "meeting_room": (
        ["Boardroom Alpha", "Meeting Room Beta", "Meeting Room Gamma",
         "Meeting Room Delta", "Meeting Room Epsilon", "Meeting Room Zeta"],
        "Meeting Room Rate",
    ),
    "hot_desk": (
        ["Hot Desk Area A", "Hot Desk Area B", "Hot Desk Area C",
         "Hot Desk Area D", "Hot Desk Area E"],
        "Hot Desk Rate",
    ),
    "private_office": (
        ["Office 101", "Office 102", "Office 103", "Office 104", "Office 105"],
        "Private Office Rate",
    ),
    "phone_booth": (["Phone Booth 1", "Phone Booth 2"], "Phone Booth Rate"),
    "parking": (["Parking Bay P1", "Parking Bay P2"], "Parking Rate"),
}
# mirrors generators/01_structural.py EXTRA_SERVICES prices
RATE_PRICE_BY_NAME = {
    "Meeting Room Rate": 25.00, "Hot Desk Rate": 15.00, "Private Office Rate": 50.00,
    "Phone Booth Rate": 10.00, "Parking Rate": 8.00,
}

BOOKING_PRODUCT_NAMES = ["Catering - Tea/Coffee", "Catering - Lunch", "AV Equipment"]
DISCOUNT_CODES_LIST = ["WELCOME20", "TEAM10", "FREEMONTH", "BOOKFREE", "PRODUCT15", "LAUNCH50"]
TIME_PASS_NAMES = ["Day Pass", "Half Day Pass", "10-Visit Pass", "Evening Pass"]

# 40 to-cancel bookings, tagged with a cancellation category for documentation
# only — CancelledBooking.CancellationReason is system-populated on delete,
# not something we can set directly (see §4j).
CANCEL_CATEGORIES = (
    ["no_longer_needed"] * 15 + ["rebooked"] * 10 + ["no_show"] * 8
    + ["cost_concerns"] * 4 + ["failed_to_pay"] * 3
)

RECURRING_BOOKING_DEFS = [
    {"count": 5, "category": "meeting_room", "repeats": 2, "repeat_every": 1, "big": False},
    {"count": 3, "category": "hot_desk", "repeats": 2, "repeat_every": 1, "big": False},
    {"count": 2, "category": "meeting_room", "repeats": 3, "repeat_every": 1, "big": True},
]


def _pick_hour(rng):
    bucket = rng.choices(["morning", "afternoon", "evening"], weights=[0.40, 0.40, 0.20], k=1)[0]
    if bucket == "morning":
        return rng.randint(8, 11)
    if bucket == "afternoon":
        return rng.randint(12, 16)
    return rng.randint(17, 19)


def _pick_start_offset(rng):
    """Past-only day offset, recency-biased (heavier in the last ~6 months)."""
    return -min(365, int(abs(rng.triangular(0, 365, 30))))


def _weekday_bias(rng, day_offset):
    """85% chance to nudge a weekend date onto the following Monday."""
    d = TODAY + timedelta(days=day_offset)
    if d.weekday() >= 5 and rng.random() < 0.85:
        return day_offset + (1 if d.weekday() == 6 else 2)
    return day_offset


def generate_bookings(rng, coworkers, visitors):
    """~240 bookings — §4g. 10 recurring + 230 one-off (40 flagged to-cancel),
    with inline BookingProducts (~36) and linked guests (~50) layered on top."""
    bookings = []
    idx = 0

    def add(b):
        nonlocal idx
        idx += 1
        b["index"] = idx
        bookings.append(b)

    active_pool = [cw for cw in coworkers if cw["Scenario"] != "churned"]

    # Recurring bookings (10)
    for rdef in RECURRING_BOOKING_DEFS:
        names, rate_name = RESOURCE_CATEGORIES[rdef["category"]]
        for _ in range(rdef["count"]):
            cw = rng.choice(active_pool)
            add({
                "CoworkerIndex": cw["index"],
                "ResourceCategory": rdef["category"],
                "ResourceName": f"{TEST_NAME_PREFIX}{rng.choice(names)}",
                "RateName": f"{TEST_NAME_PREFIX}{rate_name}",
                "StartDayOffset": -rng.randint(30, 90),
                "FromHour": _pick_hour(rng),
                "DurationMinutes": 240 if rdef["big"] else rng.choice([30, 60]),
                "Repeats": rdef["repeats"],
                "RepeatEvery": rdef["repeat_every"],
                "RepeatUntilDayOffset": rng.randint(60, 120),
                "Tentative": False,
                "AdminBooked": rng.random() < 0.10,
                "DiscountCode": None,
                "ToCancel": False,
                "CancellationCategory": None,
                "BookingProducts": [],
                "GuestVisitorIndices": [],
            })

    # One-off bookings (230)
    category_weights = {"meeting_room": 0.40, "hot_desk": 0.25, "private_office": 0.15,
                         "phone_booth": 0.10, "parking": 0.10}
    one_offs = []
    for _ in range(230):
        cw = rng.choice(coworkers)
        cat = rng.choices(list(category_weights.keys()), weights=list(category_weights.values()), k=1)[0]
        names, rate_name = RESOURCE_CATEGORIES[cat]
        one_offs.append({
            "CoworkerIndex": cw["index"],
            "ResourceCategory": cat,
            "ResourceName": f"{TEST_NAME_PREFIX}{rng.choice(names)}",
            "RateName": f"{TEST_NAME_PREFIX}{rate_name}",
            "StartDayOffset": _weekday_bias(rng, _pick_start_offset(rng)),
            "FromHour": _pick_hour(rng),
            "DurationMinutes": rng.choices([30, 60, 120, 240, 480],
                                            weights=[0.20, 0.35, 0.25, 0.15, 0.05], k=1)[0],
            "Repeats": None,
            "RepeatEvery": None,
            "RepeatUntilDayOffset": None,
            "Tentative": rng.random() < 0.05,
            "AdminBooked": rng.random() < 0.10,
            "DiscountCode": rng.choice(DISCOUNT_CODES_LIST) if rng.random() < 0.05 else None,
            "ToCancel": False,
            "CancellationCategory": None,
            "BookingProducts": [],
            "GuestVisitorIndices": [],
        })

    rng.shuffle(one_offs)
    cancel_labels = CANCEL_CATEGORIES[:]
    rng.shuffle(cancel_labels)
    for b, label in zip(one_offs[:40], cancel_labels):
        b["ToCancel"] = True
        b["CancellationCategory"] = label

    for b in one_offs:
        add(b)

    # Layer on BookingProducts (~36) and guests (~50) across the full pool
    products_pool = bookings[:]
    rng.shuffle(products_pool)
    for b in products_pool[:36]:
        b["BookingProducts"] = [{
            "ProductName": f"{TEST_NAME_PREFIX}{rng.choice(BOOKING_PRODUCT_NAMES)}",
            "Quantity": 1,
        }]

    visitor_indices = [v["index"] for v in visitors]
    guests_pool = bookings[:]
    rng.shuffle(guests_pool)
    for b in guests_pool[:50]:
        n = rng.choice([1, 1, 2, 2, 3])
        b["GuestVisitorIndices"] = rng.sample(visitor_indices, min(n, len(visitor_indices)))

    return bookings


def generate_checkins(rng, coworkers):
    """300 check-ins across ~40 of 60 coworkers — §4m. ~5 left open (no ToTime)."""
    checking_members = rng.sample(coworkers, min(40, len(coworkers)))
    heavy_indices = {cw["index"] for cw in rng.sample(checking_members, k=len(checking_members) // 2)}

    def _make(cw):
        return {
            "CoworkerIndex": cw["index"],
            "FromDayOffset": -rng.randint(1, 180),
            "FromHour": rng.randint(7, 10),
            "FromMinute": rng.choice([0, 15, 30, 45]),
            "DurationHours": round(rng.uniform(2, 10), 1),
            "Source": rng.choices([1, 2, 3], weights=[0.3, 0.5, 0.2], k=1)[0],
            "Open": False,
        }

    raw = []
    for cw in checking_members:
        visits_per_week = rng.uniform(4, 5) if cw["index"] in heavy_indices else rng.uniform(1, 2)
        total_visits = max(1, round(visits_per_week * (180 / 7)))
        raw.extend(_make(cw) for _ in range(total_visits))

    rng.shuffle(raw)
    if len(raw) > 300:
        raw = raw[:300]
    else:
        while len(raw) < 300:
            raw.append(_make(rng.choice(checking_members)))

    recent = [c for c in raw if c["FromDayOffset"] >= -3]
    for c in rng.sample(recent if len(recent) >= 5 else raw, 5):
        c["Open"] = True

    for i, c in enumerate(raw, start=1):
        c["index"] = i

    return raw


def generate_extra_services(rng, coworkers, bookings):
    """80 CoworkerExtraService rows — 47 booking charges + 25 time credits + 8
    printing credits — §4e. Booking charges reuse each booking's resource rate."""
    out = []
    idx = 0

    def add(e):
        nonlocal idx
        idx += 1
        e["index"] = idx
        out.append(e)

    charge_pool = [b for b in bookings if not b["ToCancel"]]
    rng.shuffle(charge_pool)
    for b in charge_pool[:47]:
        rate_base = b["RateName"][len(TEST_NAME_PREFIX):]
        add({
            "Kind": "booking_charge",
            "CoworkerIndex": b["CoworkerIndex"],
            "ExtraServiceName": b["RateName"],
            "BookingIndex": b["index"],
            "TotalUses": 1,
            "ChargePeriod": 5,  # Uses
            "Price": RATE_PRICE_BY_NAME[rate_base],
            "ExpireDateDayOffset": None,
        })

    time_pool = rng.sample(coworkers, min(25, len(coworkers)))
    for i, cw in enumerate(time_pool):
        active = i < 15
        add({
            "Kind": "time_credit",
            "CoworkerIndex": cw["index"],
            "ExtraServiceName": f"{TEST_NAME_PREFIX}Time Credit",
            "BookingIndex": None,
            "TotalUses": rng.choice([300, 600, 900]),
            "ChargePeriod": 1,  # Minutes
            "Price": 0,
            "ExpireDateDayOffset": rng.randint(30, 180) if active else -rng.randint(5, 60),
        })

    printing_pool = rng.sample(coworkers, min(8, len(coworkers)))
    for i, cw in enumerate(printing_pool):
        active = i < 5
        add({
            "Kind": "printing_credit",
            "CoworkerIndex": cw["index"],
            "ExtraServiceName": f"{TEST_NAME_PREFIX}Printing Credit",
            "BookingIndex": None,
            "TotalUses": 500,
            "ChargePeriod": 5,  # Uses
            "Price": 0,
            "ExpireDateDayOffset": rng.randint(60, 180) if active else -rng.randint(5, 60),
        })

    return out


def generate_booking_credits(rng, coworkers):
    """25 monetary CoworkerBookingCredits — 12 active, 8 expired, 5 near-expiry — §4e."""
    pool = rng.sample(coworkers, min(25, len(coworkers)))
    out = []
    for i, cw in enumerate(pool, start=1):
        if i <= 12:
            bucket, expire = "active", rng.randint(60, 365)
        elif i <= 20:
            bucket, expire = "expired", -rng.randint(10, 180)
        else:
            bucket, expire = "near_expiry", rng.randint(1, 30)
        out.append({
            "index": i,
            "CoworkerIndex": cw["index"],
            "TotalCredit": round(rng.uniform(20, 200), 2),
            "Bucket": bucket,
            "ExpireDateDayOffset": expire,
            "ValidFromDayOffset": -rng.randint(30, 365),
        })
    return out


def generate_credit_use_history(rng, credits, bookings):
    """50 spend transactions against active/expired (already-used) credits."""
    pool_credits = [c for c in credits if c["Bucket"] != "near_expiry"]
    kept_bookings = [b for b in bookings if not b["ToCancel"]]
    out = []
    for i in range(1, 51):
        c = rng.choice(pool_credits)
        out.append({
            "index": i,
            "CreditIndex": c["index"],
            "CreditUsed": round(min(c["TotalCredit"], rng.uniform(5, 40)), 2),
            "BookingIndex": rng.choice(kept_bookings)["index"] if rng.random() < 0.5 and kept_bookings else None,
        })
    return out


def generate_time_passes(rng, coworkers):
    """40 CoworkerTimePasses — 15 unused, 20 used, 5 expiring within 30 days — §4e."""
    chosen = rng.sample(coworkers, min(40, len(coworkers)))
    out = []
    for i, cw in enumerate(chosen, start=1):
        name = rng.choice(TIME_PASS_NAMES)
        if i <= 15:
            status, expire_offset = "unused", rng.randint(60, 200)
        elif i <= 35:
            status, expire_offset = "used", rng.randint(30, 200)
        else:
            status, expire_offset = "expiring_soon", rng.randint(3, 30)
        out.append({
            "index": i,
            "CoworkerIndex": cw["index"],
            "TimePassName": f"{TEST_NAME_PREFIX}{name}",
            "Status": status,
            "ExpireDateDayOffset": expire_offset,
            "UsedDateDayOffset": -rng.randint(1, 20) if status == "used" else None,
        })
    return out


def generate_coworker_products(rng, coworkers):
    """20 standalone recurring CoworkerProducts."""
    pool = rng.sample(coworkers, min(20, len(coworkers)))
    out = []
    for i, cw in enumerate(pool, start=1):
        out.append({
            "index": i,
            "CoworkerIndex": cw["index"],
            "ProductName": f"{TEST_NAME_PREFIX}{rng.choice(RECURRING_PRODUCTS)}",
            "Quantity": 1,
            "RepeatCycle": 4,  # Month
        })
    return out


# ---------------------------------------------------------------------------
# Layer 4b — Community: deliveries, events, help desk, community, blogs,
# tasks (shared with generators/05_community.py)
# ---------------------------------------------------------------------------

# (label, DeliveryType enum, count, {outcome: count}) — mirrors §4q
DELIVERY_TYPE_PLAN = [
    ("Mail", 1, 15, {"collected": 10, "pending": 3, "forwarded": 2}),
    ("Parcel", 2, 12, {"collected": 7, "pending": 4, "returned": 1}),
    ("Check", 3, 3, {"collected": 3}),
    ("Publicity", 4, 5, {"collected": 2, "pending": 1, "recycled": 2}),
    ("Other", 5, 5, {"collected": 3, "pending": 2}),
]
DELIVERY_NAME_POOL = {
    "Mail": ["Bank Statement", "Insurance Letter", "Tax Document", "Postcard"],
    "Parcel": ["Amazon Parcel", "Office Supplies Box", "Equipment Delivery", "Gift Package"],
    "Check": ["Client Payment Check", "Refund Check"],
    "Publicity": ["Marketing Flyer", "Trade Show Brochure"],
    "Other": ["Sample Package", "Unlabeled Box"],
}
# eDeliveryHandlingPreference values: StoreForCollection=1, Forward=2, Recycle=7, Shred=9, ReturnToSender=8
DELIVERY_HANDLING_POOL = [1] * 25 + [2] * 5 + [7] * 4 + [9] * 3 + [8] * 3

NAMED_EVENTS = [
    {"Name": "Weekly Networking Lunch", "Category": "Networking", "Recurring": True, "Repeats": 2, "RepeatEvery": 1, "DurationMinutes": 90},
    {"Name": "Monthly All-Hands", "Category": "Social", "Recurring": True, "Repeats": 3, "RepeatEvery": 1, "DurationMinutes": 60},
    {"Name": "Yoga Wednesday", "Category": "Wellness", "Recurring": True, "Repeats": 2, "RepeatEvery": 1, "DurationMinutes": 45},
    {"Name": "Summer BBQ", "Category": "Social", "PastOnly": True, "DurationMinutes": 180},
    {"Name": "Workshop: Startup Finance", "Category": "Workshop", "PastOnly": True, "DurationMinutes": 90},
    {"Name": "Workshop: Marketing 101", "Category": "Workshop", "PastOnly": True, "DurationMinutes": 90},
    {"Name": "New Member Orientation", "Category": "Social", "Recurring": True, "Repeats": 3, "RepeatEvery": 1, "DurationMinutes": 60},
    {"Name": "Q3 Town Hall", "Category": "Social", "FutureOnly": True, "DurationMinutes": 60},
    {"Name": "Demo Day", "Category": "Networking", "FutureOnly": True, "DurationMinutes": 120},
    {"Name": "Holiday Party", "Category": "Social", "FutureOnly": True, "DurationMinutes": 180},
]
EXTRA_EVENT_NAMES = [
    "Coffee & Networking", "Wellness Wednesday: Meditation", "Product Demo Night",
    "Investor Pitch Night", "Book Club", "Freelancer Meetup", "Portfolio Review Session",
    "Hackathon Kickoff", "Year-End Wrap Party", "New Year Kickoff Breakfast",
]

HELPDESK_DEPT_PLAN = [("IT Support", 10), ("Facilities", 9), ("Billing", 6)]
# (Priority enum, open_count, closed_count) — eHelpDeskMessagePriority: Low=1, Normal=2, High=3
HELPDESK_PRIORITY_PLAN = [(3, 2, 5), (2, 3, 8), (1, 2, 5)]
HELPDESK_SUBJECTS_BY_DEPT = {
    "IT Support": ["WiFi not working in meeting room", "VPN connection issues",
                   "Printer not responding", "Laptop won't turn on", "Monitor flickering"],
    "Facilities": ["AC too cold in the office", "Locker jammed", "Broken chair at hot desk",
                   "Leak in kitchen", "Parking gate not opening"],
    "Billing": ["Invoice query", "Wrong charge on invoice", "Need refund for cancelled booking",
                "Payment method update needed", "Missing receipt"],
}

COMMUNITY_GROUP_PLAN = [("General", 6, (2, 5)), ("Networking", 5, (2, 4)), ("Announcements", 4, (1, 2))]

BLOG_TITLES = [
    "Welcome to Our New Space", "Summer Networking Tips", "How to Maximise Your Hot Desk",
    "New Facilities Announcement", "Member Spotlight: Community Champion",
    "Community Guidelines Update", "Year in Review", "New Plans Available",
    "Booking Best Practices", "Upcoming Events This Quarter",
]

TASK_NAMES = [
    "Complete induction checklist", "Return equipment", "Update payment method",
    "Sign contract", "Collect delivery", "Renew ID badge", "Submit feedback survey",
    "Confirm parking permit", "Attend orientation session", "Update emergency contact",
]


def generate_deliveries(rng, coworkers):
    """40 CoworkerDeliveries across 5 types with mixed collection outcomes — §4q."""
    handling_pool = DELIVERY_HANDLING_POOL[:]
    rng.shuffle(handling_pool)

    out = []
    idx = 0
    h = 0
    for type_name, type_val, count, outcomes in DELIVERY_TYPE_PLAN:
        outcome_list = []
        for outcome, n in outcomes.items():
            outcome_list += [outcome] * n
        rng.shuffle(outcome_list)

        for outcome in outcome_list:
            idx += 1
            arrival_offset = -rng.randint(1, 180)
            out.append({
                "index": idx,
                "CoworkerIndex": rng.choice(coworkers)["index"],
                "Name": rng.choice(DELIVERY_NAME_POOL[type_name]),
                "DeliveryType": type_val,
                "HandlingPreference": handling_pool[h],
                "ArrivalDayOffset": arrival_offset,
                "Outcome": outcome,  # collected / pending / forwarded / returned / recycled
                "OutcomeDayOffset": (min(arrival_offset + rng.randint(1, 10), -1)
                                      if outcome != "pending" else None),
            })
            h += 1

    return out


def generate_calendar_events(rng):
    """20 CalendarEvents — §4r. 4 recurring series + 16 one-offs, past and upcoming."""
    events = []
    idx = 0

    def add(e):
        nonlocal idx
        idx += 1
        e["index"] = idx
        events.append(e)

    for e in NAMED_EVENTS:
        if e.get("Recurring"):
            start_offset = -rng.randint(30, 90)
        elif e.get("PastOnly"):
            start_offset = -rng.randint(30, 300)
        elif e.get("FutureOnly"):
            start_offset = rng.randint(10, 120)
        else:
            start_offset = -rng.randint(10, 200)

        add({
            "Name": e["Name"],
            "Category": e["Category"],
            "StartDayOffset": start_offset,
            "StartHour": rng.choice([9, 12, 17, 18]),
            "DurationMinutes": e["DurationMinutes"],
            "Repeats": e.get("Repeats"),
            "RepeatEvery": e.get("RepeatEvery"),
            "RepeatUntilDayOffset": rng.randint(60, 150) if e.get("Recurring") else None,
            "ResourceLinked": rng.random() < 0.3,
            "OnlyForMembers": rng.random() < 0.4,
            "ShowInHomePage": rng.random() < 0.5,
        })

    for name in EXTRA_EVENT_NAMES:
        past = rng.random() < 0.5
        add({
            "Name": name,
            "Category": rng.choice(["Workshop", "Networking", "Social", "Wellness"]),
            "StartDayOffset": -rng.randint(10, 250) if past else rng.randint(5, 100),
            "StartHour": rng.choice([9, 12, 17, 18]),
            "DurationMinutes": rng.choice([45, 60, 90, 120]),
            "Repeats": None,
            "RepeatEvery": None,
            "RepeatUntilDayOffset": None,
            "ResourceLinked": rng.random() < 0.3,
            "OnlyForMembers": rng.random() < 0.4,
            "ShowInHomePage": rng.random() < 0.5,
        })

    return events


def generate_event_products(rng, events):
    """One EventProduct (ticket type) per event — required before EventAttendee."""
    out = []
    for e in events:
        out.append({
            "index": e["index"],
            "EventIndex": e["index"],
            "Price": rng.choice([0, 0, 0, 10, 15, 25]),
            "SaleStartDayOffset": e["StartDayOffset"] - 30,
            "SaleEndDayOffset": e["StartDayOffset"],
        })
    return out


def generate_event_attendees(rng, fake, events, coworkers):
    """~60 EventAttendees — §4r. ~70% linked to a seeded coworker, rest ad-hoc guests."""
    raw = []
    for e in events:
        for _ in range(rng.randint(1, 6)):
            is_coworker = rng.random() < 0.7
            raw.append({
                "EventIndex": e["index"],
                "CoworkerIndex": rng.choice(coworkers)["index"] if is_coworker else None,
                "FullName": None if is_coworker else fake.name(),
                "Email": None if is_coworker else fake.email(),
                "IsCoworker": is_coworker,
                "CheckedIn": e["StartDayOffset"] < 0 and rng.random() < 0.7,
            })

    rng.shuffle(raw)
    raw = raw[:60]
    for i, a in enumerate(raw, start=1):
        a["index"] = i
    return raw


def generate_helpdesk_messages(rng, coworkers):
    """25 HelpDeskMessages — §4s."""
    priority_slots = []
    for priority_val, open_count, closed_count in HELPDESK_PRIORITY_PLAN:
        priority_slots += [(priority_val, False)] * open_count
        priority_slots += [(priority_val, True)] * closed_count
    rng.shuffle(priority_slots)

    dept_pool = []
    for name, count in HELPDESK_DEPT_PLAN:
        dept_pool += [name] * count
    rng.shuffle(dept_pool)

    out = []
    for i, ((priority, closed), dept) in enumerate(zip(priority_slots, dept_pool), start=1):
        out.append({
            "index": i,
            "CoworkerIndex": rng.choice(coworkers)["index"],
            "Priority": priority,
            "Closed": closed,
            "DepartmentName": dept,
            "Subject": rng.choice(HELPDESK_SUBJECTS_BY_DEPT[dept]),
            "DayOffset": -rng.randint(1, 180),
        })
    return out


def generate_community_threads(rng, fake, coworkers):
    """15 CommunityThreads — §4t."""
    out = []
    idx = 0
    for group, count, (lo, hi) in COMMUNITY_GROUP_PLAN:
        for _ in range(count):
            idx += 1
            out.append({
                "index": idx,
                "GroupName": group,
                "CoworkerIndex": rng.choice(coworkers)["index"],
                "Subject": fake.sentence(nb_words=6).rstrip("."),
                "Message": fake.sentence(nb_words=12),
                "DayOffset": -rng.randint(1, 180),
                "MessageCount": rng.randint(lo, hi),
                "Private": rng.random() < 0.15,
            })
    return out


def generate_community_messages(rng, fake, threads, coworkers):
    """~40 CommunityMessage replies (2-5 per thread) — §4t."""
    out = []
    idx = 0
    for t in threads:
        for i in range(t["MessageCount"]):
            idx += 1
            out.append({
                "index": idx,
                "ThreadIndex": t["index"],
                "CoworkerIndex": rng.choice(coworkers)["index"],
                "Message": fake.sentence(nb_words=10),
                "DayOffsetAfterThread": rng.randint(0, 5) * (i + 1),
            })
    return out


def generate_blog_posts(rng):
    """10 BlogPosts spread over 12 months — §4u."""
    n = len(BLOG_TITLES)
    member_only_idx = set(rng.sample(range(n), 4))
    home_idx = set(rng.sample(range(n), 6))

    out = []
    for i, title in enumerate(BLOG_TITLES):
        offset = round(-365 + i * (355 / (n - 1)))
        out.append({
            "index": i + 1,
            "Title": title,
            "PublishDayOffset": offset,
            "OnlyForMembers": i in member_only_idx,
            "ShowInHomePage": i in home_idx,
        })
    return out


def generate_coworker_tasks(rng, coworkers):
    """20 CoworkerTasks — §4v. 10 completed, 5 overdue, 5 upcoming."""
    pool = rng.sample(coworkers, min(20, len(coworkers)))
    out = []
    for i, cw in enumerate(pool, start=1):
        if i <= 10:
            completed, due_offset = True, -rng.randint(10, 150)
        elif i <= 15:
            completed, due_offset = False, -rng.randint(5, 60)
        else:
            completed, due_offset = False, rng.randint(5, 30)
        out.append({
            "index": i,
            "CoworkerIndex": cw["index"],
            "Name": rng.choice(TASK_NAMES),
            "DueDayOffset": due_offset,
            "Completed": completed,
        })
    return out


def write_json(path, data):
    """Write data to JSON file with consistent formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"  Written {path} ({len(data)} records)")


def main():
    parser = argparse.ArgumentParser(description="Pre-generate test data files")
    parser.add_argument("--seed", type=int, default=RANDOM_SEED, help="Random seed")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    fake = Faker("en_GB")
    Faker.seed(args.seed)

    print(f"Generating data with seed={args.seed}...")

    coworkers = generate_coworkers(rng, fake)
    write_json(DATA_DIR / "coworkers.json", coworkers)

    visitors = generate_visitors(rng, fake, len(coworkers))
    write_json(DATA_DIR / "visitors.json", visitors)

    contracts = generate_contracts(rng, coworkers)
    write_json(DATA_DIR / "contracts.json", contracts)

    write_json(DATA_DIR / "contract_products.json", generate_contract_products(rng, contracts))
    write_json(DATA_DIR / "contract_schedules.json", generate_contract_schedules(rng, contracts))
    write_json(DATA_DIR / "contract_paused_periods.json", generate_contract_paused_periods(rng, contracts))
    write_json(DATA_DIR / "contract_deposits.json", generate_contract_deposits(rng, contracts))
    write_json(DATA_DIR / "coworker_inventory_assets.json", generate_coworker_inventory_assets(rng, coworkers))
    write_json(DATA_DIR / "desk_assignments.json", generate_desk_assignments(rng, contracts))

    bookings = generate_bookings(rng, coworkers, visitors)
    write_json(DATA_DIR / "bookings.json", bookings)

    write_json(DATA_DIR / "checkins.json", generate_checkins(rng, coworkers))
    write_json(DATA_DIR / "extra_services.json", generate_extra_services(rng, coworkers, bookings))

    booking_credits = generate_booking_credits(rng, coworkers)
    write_json(DATA_DIR / "booking_credits.json", booking_credits)
    write_json(DATA_DIR / "credit_use_history.json",
               generate_credit_use_history(rng, booking_credits, bookings))

    write_json(DATA_DIR / "time_passes.json", generate_time_passes(rng, coworkers))
    write_json(DATA_DIR / "coworker_products.json", generate_coworker_products(rng, coworkers))

    write_json(DATA_DIR / "deliveries.json", generate_deliveries(rng, coworkers))

    calendar_events = generate_calendar_events(rng)
    write_json(DATA_DIR / "calendar_events.json", calendar_events)
    write_json(DATA_DIR / "event_products.json", generate_event_products(rng, calendar_events))
    write_json(DATA_DIR / "event_attendees.json", generate_event_attendees(rng, fake, calendar_events, coworkers))

    write_json(DATA_DIR / "helpdesk_messages.json", generate_helpdesk_messages(rng, coworkers))

    community_threads = generate_community_threads(rng, fake, coworkers)
    write_json(DATA_DIR / "community_threads.json", community_threads)
    write_json(DATA_DIR / "community_messages.json",
               generate_community_messages(rng, fake, community_threads, coworkers))

    write_json(DATA_DIR / "blog_posts.json", generate_blog_posts(rng))
    write_json(DATA_DIR / "coworker_tasks.json", generate_coworker_tasks(rng, coworkers))

    print("Done.")


if __name__ == "__main__":
    main()
