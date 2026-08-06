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

    # Recurring bookings (10) — these run in parallel for months (see
    # RepeatUntilDayOffset), so two series on the same resource at
    # overlapping times/days is a real collision the live API rejects
    # ("already booked by ..."). Retry with a different hour/resource
    # until it doesn't overlap any previously placed recurring booking.
    recurring_slots = []  # (resource_name, from_hour, to_hour)

    def _overlaps(a_from, a_to, b_from, b_to):
        return a_from < b_to and b_from < a_to

    for rdef in RECURRING_BOOKING_DEFS:
        names, rate_name = RESOURCE_CATEGORIES[rdef["category"]]
        for _ in range(rdef["count"]):
            cw = rng.choice(active_pool)
            duration = 240 if rdef["big"] else rng.choice([30, 60])
            for _attempt in range(50):
                resource_name = f"{TEST_NAME_PREFIX}{rng.choice(names)}"
                from_hour = _pick_hour(rng)
                to_hour = from_hour + duration / 60
                if not any(r == resource_name and _overlaps(from_hour, to_hour, f, t)
                           for r, f, t in recurring_slots):
                    break
            recurring_slots.append((resource_name, from_hour, to_hour))
            add({
                "CoworkerIndex": cw["index"],
                "ResourceCategory": rdef["category"],
                "ResourceName": resource_name,
                "RateName": f"{TEST_NAME_PREFIX}{rate_name}",
                "StartDayOffset": -rng.randint(30, 90),
                "FromHour": from_hour,
                "DurationMinutes": duration,
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
        # ValidFrom must chronologically precede ExpireDate — picking both
        # independently at random (as before) can produce ExpireDate before
        # ValidFrom, which the live API rejects. Anchor ValidFrom to a
        # random span before whichever expire offset was chosen instead.
        out.append({
            "index": i,
            "CoworkerIndex": cw["index"],
            "TotalCredit": round(rng.uniform(20, 200), 2),
            "Bucket": bucket,
            "ExpireDateDayOffset": expire,
            "ValidFromDayOffset": expire - rng.randint(30, 200),
        })
    return out


def generate_credit_use_history(rng, credits, bookings):
    """50 spend transactions against active/expired (already-used) credits.

    Cumulative CreditUsed per credit is capped at that credit's TotalCredit
    — the live API rejects a use-history entry once its credit's recorded
    usage would exceed the total (the actual booking cost at the normal
    rate is billed separately via a CoworkerExtraService, not blended into
    this record)."""
    pool_credits = [c for c in credits if c["Bucket"] != "near_expiry"]
    kept_bookings = [b for b in bookings if not b["ToCancel"]]
    remaining = {c["index"]: c["TotalCredit"] for c in pool_credits}
    out = []
    i = 0
    attempts = 0
    while i < 50 and attempts < 1000:
        attempts += 1
        available = [c for c in pool_credits if remaining[c["index"]] >= 0.5]
        if not available:
            break
        c = rng.choice(available)
        amount = round(min(remaining[c["index"]], rng.uniform(5, 40)), 2)
        remaining[c["index"]] -= amount
        i += 1
        out.append({
            "index": i,
            "CreditIndex": c["index"],
            "CreditUsed": amount,
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

# (ShortDescription, LongDescription) keyed by event Name — CalendarEvent has
# real description fields that were never populated, leaving events blank.
EVENT_DESCRIPTIONS = {
    "Weekly Networking Lunch": (
        "Casual lunch to meet other members.",
        "Join fellow members for an informal lunch in the communal area. No agenda, no pitches — "
        "just a relaxed way to put faces to names and see who else is working nearby."),
    "Monthly All-Hands": (
        "Community update on what's new in the space.",
        "A short, informal update covering what's changed in the space this month — new members, "
        "upcoming facilities work, and any community announcements. Drinks and snacks provided."),
    "Yoga Wednesday": (
        "Midweek yoga session for all levels.",
        "A relaxed, all-levels yoga session to break up the week. Mats provided, just bring "
        "comfortable clothing. Runs rain or shine in the wellness room."),
    "Summer BBQ": (
        "Our annual summer BBQ for members and their guests.",
        "Food, drinks, and good company on the terrace. Bring a guest if you'd like — just let "
        "reception know numbers in advance so we can plan catering."),
    "Workshop: Startup Finance": (
        "Practical session on managing early-stage startup finances.",
        "A hands-on workshop covering the finance basics every early-stage founder needs: cash "
        "flow forecasting, runway planning, and what investors actually look for in your numbers."),
    "Workshop: Marketing 101": (
        "Introduction to marketing fundamentals for small teams.",
        "A practical introduction to marketing for founders and small teams with no dedicated "
        "marketing hire — covering positioning, low-cost channels, and how to measure what's working."),
    "New Member Orientation": (
        "Everything new members need to know about the space.",
        "A short welcome session covering the essentials: booking rooms, using the app, printing, "
        "and who to ask when you need help. Open to all new members, coffee included."),
    "Q3 Town Hall": (
        "Quarterly update and open Q&A with the team.",
        "Our quarterly town hall — an update on what's happened this quarter, what's coming next, "
        "and an open floor for questions. All members welcome."),
    "Demo Day": (
        "Members showcase what they're building to the community.",
        "A handful of members take the stage to share what they're working on, in front of the "
        "wider community and a few invited guests. Sign up at reception if you'd like to present."),
    "Holiday Party": (
        "End-of-year celebration for the whole community.",
        "Our end-of-year get-together — food, drinks, and a chance to celebrate the year with the "
        "community before things wind down for the holidays."),
    "Coffee & Networking": (
        "Drop-in coffee morning, no agenda required.",
        "An informal drop-in session over coffee — come and go as you please. A good excuse to "
        "step away from the desk and meet someone new."),
    "Wellness Wednesday: Meditation": (
        "Guided meditation session to reset midweek.",
        "A short guided meditation session to help reset midweek. No experience necessary, just "
        "bring yourself and find a quiet spot in the wellness room."),
    "Product Demo Night": (
        "Members demo their products to a friendly audience.",
        "An evening of short product demos from members building something new, followed by "
        "informal feedback and networking over drinks."),
    "Investor Pitch Night": (
        "Practice pitches in front of a supportive audience.",
        "A low-pressure environment for members to practice their investor pitch and get honest "
        "feedback from peers before the real thing."),
    "Book Club": (
        "Monthly discussion on a business or startup book.",
        "A casual monthly discussion group covering a different business, startup, or productivity "
        "book each time. Check the community group for this month's pick."),
    "Freelancer Meetup": (
        "Meetup for freelancers and independent contractors in the space.",
        "A regular meetup for freelancers and independent contractors to swap notes on clients, "
        "rates, and the realities of working solo — and to feel a little less solo doing it."),
    "Portfolio Review Session": (
        "Get feedback on your portfolio or project work.",
        "Bring your portfolio or current project for informal feedback from other members working "
        "in similar fields. Useful for designers, developers, and creatives alike."),
    "Hackathon Kickoff": (
        "Kickoff session for our community hackathon weekend.",
        "The opening session for our community hackathon — team formation, idea pitching, and "
        "ground rules before the building begins."),
    "Year-End Wrap Party": (
        "Casual celebration to close out the year.",
        "A relaxed get-together to mark the end of the year and celebrate what the community "
        "built and achieved together."),
    "New Year Kickoff Breakfast": (
        "Start the year with breakfast and a fresh set of goals.",
        "Kick off the new year with breakfast and an informal chat about goals for the months "
        "ahead — both for the space and for your own work."),
}

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

# fake.sentence() defaults to Faker's Latin lorem-ipsum generator, not real
# English — looked like gibberish in the live UI ("Dignissimos in
# temporibus nesciunt"). Curated, on-topic content per group instead.
COMMUNITY_THREAD_SUBJECTS = {
    "General": [
        "Anyone know a good coffee spot nearby?", "Wifi's been patchy on the 2nd floor",
        "Lost a black jacket in the kitchen area", "Best time to book the meeting rooms?",
        "Recommendations for lunch places around here", "Printer on 1st floor out of toner again",
    ],
    "Networking": [
        "Looking for a UX designer to collaborate with", "Anyone else working in fintech?",
        "Freelancers — want to grab coffee and swap notes?", "Looking for a co-founder for a side project",
        "Any developers interested in a weekend hackathon?",
    ],
    "Announcements": [
        "Building maintenance this Saturday morning", "New meeting room booking system live now",
        "Reminder: annual member survey closes Friday", "Kitchen will be closed for cleaning Monday AM",
    ],
}

COMMUNITY_THREAD_OPENERS = [
    "Just wondering if anyone else has run into this.", "Would appreciate any pointers, thanks!",
    "Happy to buy coffee for anyone who can help.", "Posting here in case it helps someone else too.",
    "Let me know if you've dealt with this before.", "Open to suggestions from anyone in the space.",
]

COMMUNITY_REPLY_MESSAGES = [
    "Yeah, I noticed that too — glad it's not just me.", "Thanks, that's really helpful!",
    "I had the same issue last week, still not fixed.", "Following this thread, interested too.",
    "Reception should be able to sort that out for you.", "+1, would love to see this happen.",
    "I can help with this, sending you a message.", "Same here, has been going on for a few days.",
    "Good shout — count me in.", "Appreciate you flagging this.",
]

BLOG_TITLES = [
    "Welcome to Our New Space", "Summer Networking Tips", "How to Maximise Your Hot Desk",
    "New Facilities Announcement", "Member Spotlight: Community Champion",
    "Community Guidelines Update", "Year in Review", "New Plans Available",
    "Booking Best Practices", "Upcoming Events This Quarter",
]

# (SummaryText, FullText) keyed by title — BlogPost has real content fields
# (SummaryText/FullText) that were never populated, leaving articles blank.
BLOG_CONTENT = {
    "Welcome to Our New Space": (
        "We're delighted to officially open our doors — here's what's new.",
        "After months of planning and renovation, we're thrilled to welcome you to the space. "
        "From redesigned hot desks to brand new meeting rooms, every corner has been built with "
        "our community in mind. Drop by reception if you'd like a tour, and keep an eye on this "
        "blog for updates as we settle in."
    ),
    "Summer Networking Tips": (
        "A few ideas for making the most of the community this summer.",
        "Summer is a great time to build new connections — the space tends to be a little quieter, "
        "which makes it easier to strike up conversations. Try grabbing a coffee at the shared "
        "kitchen during peak hours, joining one of our upcoming events, or posting an introduction "
        "in the Networking community group. You'd be surprised how many members are just as keen "
        "to meet new people as you are."
    ),
    "How to Maximise Your Hot Desk": (
        "Simple habits that make hot-desking smoother for everyone.",
        "Hot desking works best when a few small courtesies are followed: clear your desk at the "
        "end of the day, keep noise levels considerate near the quiet zones, and use the booking "
        "system if you know you'll need a specific spot. Lockers are available if you'd rather not "
        "carry everything home each evening — ask reception for details."
    ),
    "New Facilities Announcement": (
        "Two new meeting rooms and an upgraded kitchen are now open.",
        "We've expanded our facilities based on member feedback. Two additional meeting rooms are "
        "now bookable through the app, and the kitchen has been upgraded with a second coffee "
        "machine and more fridge space. We're always listening — if there's something you'd like "
        "to see next, let the team know."
    ),
    "Member Spotlight: Community Champion": (
        "Celebrating one of our longest-standing members and their journey here.",
        "Every so often we like to shine a light on members who've made this space feel like home. "
        "This month, we're celebrating someone who's been with us since the early days, has helped "
        "organise several community events, and is always the first to welcome newcomers. Thank you "
        "for everything you bring to this community."
    ),
    "Community Guidelines Update": (
        "A few updates to our community guidelines, effective this month.",
        "We've refreshed our community guidelines to keep things running smoothly as we grow. The "
        "key changes cover shared space etiquette, guest policies, and how to report an issue. "
        "Nothing here should come as a surprise — it's mostly a matter of putting into writing the "
        "standards we already hold ourselves to. Full details are available on the members portal."
    ),
    "Year in Review": (
        "Looking back at a busy year for the space and our community.",
        "It's been a big year — new members joined, new facilities opened, and dozens of events "
        "brought the community together. Thank you to everyone who made it what it was. We're "
        "already planning for an even better year ahead, with more events, more flexibility, and "
        "more ways to connect."
    ),
    "New Plans Available": (
        "A look at the new membership plans now available to join.",
        "Based on member feedback, we've introduced more flexible plan options — whether you need "
        "a dedicated desk, a private office, or just a few days a month. Existing members can switch "
        "plans at any time from the portal, and our team is happy to help you find the right fit."
    ),
    "Booking Best Practices": (
        "How to get the most out of the booking system.",
        "Booking a room or resource is quick, but a few habits make it even smoother: book as early "
        "as you can for popular slots, cancel promptly if your plans change so others can use the "
        "space, and double check the resource capacity before inviting guests. The booking system "
        "is available in the app and on the web portal."
    ),
    "Upcoming Events This Quarter": (
        "A preview of the workshops, socials, and networking events coming up.",
        "We've got a packed quarter ahead — from hands-on workshops to casual social nights and "
        "focused networking sessions. Keep an eye on the events calendar and RSVP early, as spaces "
        "for some sessions are limited. We hope to see you there."
    ),
}

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

        short_desc, long_desc = EVENT_DESCRIPTIONS[e["Name"]]
        add({
            "Name": e["Name"],
            "ShortDescription": short_desc,
            "LongDescription": long_desc,
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
        short_desc, long_desc = EVENT_DESCRIPTIONS[name]
        add({
            "Name": name,
            "ShortDescription": short_desc,
            "LongDescription": long_desc,
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
            # OnlyForMembers events reject non-coworker guest purchases live
            # ("You cannot purchase this product") — only real coworkers can
            # attend one, regardless of the usual 70% coworker split.
            is_coworker = True if e.get("OnlyForMembers") else rng.random() < 0.7
            raw.append({
                "EventIndex": e["index"],
                "CoworkerIndex": rng.choice(coworkers)["index"] if is_coworker else None,
                "FullName": None if is_coworker else fake.name(),
                # fake.email() defaults to safe=True, which always uses a
                # reserved example.com/.org/.net domain (RFC 2606) — Nexudus
                # rejects those live as "Invalid Email Address". safe=False
                # produces realistic-looking domains instead.
                "Email": None if is_coworker else fake.email(safe=False),
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
        subjects = rng.sample(COMMUNITY_THREAD_SUBJECTS[group], count)
        for subject in subjects:
            idx += 1
            out.append({
                "index": idx,
                "GroupName": group,
                "CoworkerIndex": rng.choice(coworkers)["index"],
                "Subject": subject,
                "Message": rng.choice(COMMUNITY_THREAD_OPENERS),
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
                "Message": rng.choice(COMMUNITY_REPLY_MESSAGES),
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
        summary, full_text = BLOG_CONTENT[title]
        out.append({
            "index": i + 1,
            "Title": title,
            "SummaryText": summary,
            "FullText": full_text,
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


# ---------------------------------------------------------------------------
# Layer 5 — CRM & Proposals (shared with generators/07_crm_proposals.py)
# ---------------------------------------------------------------------------

# (stage, count) — sums to 30, scaled down from §4l's 35 to hit the §2 target
CRM_STAGE_PLAN = [
    ("Lead", 6), ("Qualified", 5), ("Proposal Sent", 4),
    ("Negotiation", 4), ("Won", 5), ("Lost", 6),
]
# CrmBoardColumn full_key = "<board short name>/<column name>", matching
# generators/01_structural.py's CRM_BOARD_COLUMNS
CRM_STAGE_COLUMN_KEY = {
    "Lead": "New Business/Lead",
    "Qualified": "New Business/Qualified",
    "Proposal Sent": "New Business/Proposal Sent",
    "Negotiation": "Expansion/Negotiation",
    "Won": "New Business/Won",
    "Lost": "New Business/Lost",
}
CRM_STAGE_AVG_VALUE = {
    "Lead": 800, "Qualified": 1200, "Proposal Sent": 1500,
    "Negotiation": 2000, "Won": 1800, "Lost": 1000,
}
# eCrmOpportunitySource: Web=1, Phone=2, Referral=5, Broker=11, GoogleSearch=19
CRM_LEAD_SOURCES = [1, 2, 5, 11, 19]
CRM_STAGE_ORDER = ["Lead", "Qualified", "Proposal Sent", "Negotiation", "Won"]

# (ProposalStatus enum, count) — Draft=1, Sent=2, Accepted=3, Rejected=4
PROPOSAL_STATUS_PLAN = [(1, 3), (2, 4), (3, 5), (4, 3)]


def generate_crm_opportunities(rng, coworkers):
    """30 CrmOpportunities across the pipeline — §4l."""
    out = []
    idx = 0
    for stage, count in CRM_STAGE_PLAN:
        for _ in range(count):
            idx += 1
            base_value = CRM_STAGE_AVG_VALUE[stage]
            out.append({
                "index": idx,
                "CoworkerIndex": rng.choice(coworkers)["index"],
                "Stage": stage,
                "Value": round(base_value * rng.uniform(0.8, 1.2), 2),
                "LeadSource": rng.choice(CRM_LEAD_SOURCES),
                "Position": idx,
                "DueDayOffset": (rng.randint(5, 60) if stage not in ("Won", "Lost")
                                  else -rng.randint(1, 30)),
                "CreatedDayOffset": -rng.randint(10, 120),
            })
    return out


def generate_crm_opportunity_history(rng, opportunities):
    """2-5 CrmOpportunityHistory rows per opportunity, tracing its stage path — §4l."""
    out = []
    idx = 0
    for opp in opportunities:
        stage = opp["Stage"]
        if stage == "Lost":
            path = CRM_STAGE_ORDER[:rng.randint(1, 3)] + ["Lost"]
        elif stage == "Won":
            path = CRM_STAGE_ORDER[:]
        else:
            path = CRM_STAGE_ORDER[:CRM_STAGE_ORDER.index(stage) + 1]

        prev = None
        n_days_ago = abs(opp["CreatedDayOffset"])
        steps = len(path)
        for i, stg in enumerate(path):
            idx += 1
            out.append({
                "index": idx,
                "OpportunityIndex": opp["index"],
                "OldStage": prev,
                "NewStage": stg,
                "DayOffset": -int(n_days_ago * (1 - i / max(steps, 1))),
            })
            prev = stg
    return out


def generate_proposals(rng, opportunities, coworkers):
    """15 Proposals — §4f. Accepted ones are tied to a Won opportunity."""
    won_pool = [o for o in opportunities if o["Stage"] == "Won"]
    rng.shuffle(won_pool)

    out = []
    idx = 0
    won_i = 0
    for status, count in PROPOSAL_STATUS_PLAN:
        for _ in range(count):
            idx += 1
            if status == 3 and won_i < len(won_pool):
                opp = won_pool[won_i]
                won_i += 1
                cw_idx, opp_idx = opp["CoworkerIndex"], opp["index"]
            else:
                cw_idx, opp_idx = rng.choice(coworkers)["index"], None

            tariff_name, tariff_price, _cat = rng.choice(TARIFFS_INFO)
            out.append({
                "index": idx,
                "CoworkerIndex": cw_idx,
                "OpportunityIndex": opp_idx,
                "Reference": f"PROP-{idx:03d}",
                "ProposalStatus": status,
                "TariffName": f"{TEST_NAME_PREFIX}{tariff_name}",
                "Price": round(tariff_price * rng.uniform(0.9, 1.0), 2),
                "StartDayOffset": rng.randint(5, 60),
                "BillingDay": rng.randint(1, 28),
                "Quantity": 1,
                "UseDiscountCode": status == 3 and rng.random() < 0.4,
            })
    return out


def generate_coworker_data_files(rng, proposals):
    """10 CoworkerDataFiles (placeholder documents) — §2, prefers Accepted proposals."""
    accepted = [p for p in proposals if p["ProposalStatus"] == 3]
    rest = [p for p in proposals if p["ProposalStatus"] != 3]
    pool = accepted + rest
    chosen = pool[:10]

    out = []
    for i, p in enumerate(chosen, start=1):
        signed = p["ProposalStatus"] == 3 and rng.random() < 0.7
        out.append({
            "index": i,
            "CoworkerIndex": p["CoworkerIndex"],
            "ProposalIndex": p["index"],
            "Name": f"Membership Agreement - {p['Reference']}",
            "RequestSignature": signed,
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

    crm_opportunities = generate_crm_opportunities(rng, coworkers)
    write_json(DATA_DIR / "crm_opportunities.json", crm_opportunities)
    write_json(DATA_DIR / "crm_opportunity_history.json",
               generate_crm_opportunity_history(rng, crm_opportunities))

    proposals = generate_proposals(rng, crm_opportunities, coworkers)
    write_json(DATA_DIR / "proposals.json", proposals)
    write_json(DATA_DIR / "coworker_data_files.json", generate_coworker_data_files(rng, proposals))

    print("Done.")


if __name__ == "__main__":
    main()
