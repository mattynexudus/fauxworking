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

    print("Done.")


if __name__ == "__main__":
    main()
