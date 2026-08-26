"""
Shared "what's actually in the account" reporting logic — reused by
scripts/verify.sh (a standalone check-anytime command) and pipeline.py
(printed automatically at the end of a live run). Kept as one implementation
so the two never drift apart.

For the entities in config.CONFIGURABLE_VOLUME_KEYS, the "target" reported
is the actual count in the corresponding data/*.json file — i.e. whatever
this account was actually configured to generate via prebuild.py/wizard.py —
not config.VOLUMES' fixed default. Everything else still compares against
VOLUMES, since those entities have no per-run override.
"""

import csv
import json
from collections import Counter
from pathlib import Path

from config import CREATED_IDS_DIR, DATA_DIR, PROJECT_ROOT, VOLUMES, CONFIGURABLE_VOLUME_KEYS

# Lives at the project root, not under data/ — data/ is generated content,
# this is a report about a run.
REPORT_PATH = PROJECT_ROOT / "last-run-report.txt"

# Nexudus apiPath -> config.VOLUMES key, for the entities with a direct 1:1 mapping.
TARGET_KEY_BY_ENTITY = {
    "taxrates": "tax_rates", "financialaccounts": "financial_accounts",
    "resourcetypes": "resource_types", "teams": "teams", "tariffs": "tariffs",
    "products": "products", "extraservices": "extra_services", "timepasses": "time_passes",
    "resources": "resources", "floorplans": "floor_plans", "floorplandesks": "floor_plan_desks",
    "inventoryassets": "inventory_assets", "discountcodes": "discount_codes",
    "crmboards": "crm_boards", "crmboardcolumns": "crm_board_columns",
    "businesstimeslots": "business_time_slots", "helpdeskdepartments": "help_desk_departments",
    "communitygroups": "community_groups", "calendareventcategories": "calendar_event_categories",
    "coworkers": "coworkers", "visitors": "visitors",
    "coworkercontracts": "coworker_contracts", "contractproducts": "contract_products",
    "contractpausedperiods": "contract_paused_periods", "contractdeposits": "contract_deposits",
    "coworkerinventoryassets": "coworker_inventory_assets",
    "bookings": "bookings_total", "checkins": "check_ins",
    "coworkerextraservices": "coworker_extra_services",
    "coworkerbookingcredits": "coworker_booking_credits",
    "coworkertimepasses": "coworker_time_passes", "coworkerproducts": "coworker_products",
    "coworkerdeliveries": "coworker_deliveries", "calendarevents": "calendar_events",
    "eventattendees": "event_attendees", "helpdeskmessages": "help_desk_messages",
    "communitythreads": "community_threads", "communitymessages": "community_messages",
    "blogposts": "blog_posts", "coworkertasks": "coworker_tasks",
    "crmopportunities": "crm_opportunities", "crmopportunityhistories": "crm_opportunity_histories",
    "proposals": "proposals", "coworkerdatafiles": "coworker_data_files",
}

# config.VOLUMES key -> the prebuild.py output file it actually controls,
# for the CONFIGURABLE_VOLUME_KEYS subset only.
DATA_FILE_BY_VOLUME_KEY = {
    "coworkers": "coworkers.json",
    "visitors": "visitors.json",
    "bookings_total": "bookings.json",
    "check_ins": "checkins.json",
    "crm_opportunities": "crm_opportunities.json",
    "proposals": "proposals.json",
    "help_desk_messages": "helpdesk_messages.json",
    "community_threads": "community_threads.json",
    "coworker_tasks": "coworker_tasks.json",
    "coworker_time_passes": "time_passes.json",
    "coworker_products": "coworker_products.json",
}


def _grouped_records():
    """(by_entity, malformed_count) — every data/created-ids/*.json record,
    grouped by its "entity" tag. Records missing that tag aren't a real
    entity (they're not what track_id() produces) — most likely a stray
    file sitting in data/created-ids/ that belongs elsewhere (e.g.
    prebuild.py's plan output, which lives in data/ instead — this is
    exactly what silently inflated a bogus "?" row here before). They're
    counted separately rather than folded into the table so that keeps
    happening loudly instead of quietly.
    """
    by_entity = {}
    malformed_count = 0
    if CREATED_IDS_DIR.exists():
        for path in sorted(CREATED_IDS_DIR.glob("*.json")):
            try:
                records = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                records = []
            for r in records:
                entity = r.get("entity")
                if entity is None:
                    malformed_count += 1
                    continue
                by_entity.setdefault(entity, []).append(r)
    return by_entity, malformed_count


def tracked_counts():
    """{entity: count}, tallied from every data/created-ids/*.json record."""
    by_entity, _malformed_count = _grouped_records()
    return {entity: len(records) for entity, records in by_entity.items()}


def target_for(entity):
    """Expected count for an entity, or None if it has no known target."""
    target_key = TARGET_KEY_BY_ENTITY.get(entity)
    if target_key is None:
        return None
    if target_key in CONFIGURABLE_VOLUME_KEYS:
        data_file = DATA_FILE_BY_VOLUME_KEY.get(target_key)
        path = DATA_DIR / data_file if data_file else None
        if path and path.exists():
            try:
                return len(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                pass
    return VOLUMES.get(target_key)


def report_lines():
    """The full 'what's in the account' table, as a list of printable lines."""
    by_entity, malformed_count = _grouped_records()
    counts = {entity: len(records) for entity, records in by_entity.items()}
    if not counts and not malformed_count:
        return ["No tracked records found — nothing has been seeded live yet."]

    lines = [f"{'Entity':<32} {'Created':>8} {'Target':>8}", "-" * 50]
    for entity in sorted(counts):
        created = counts[entity]
        target = target_for(entity)
        flag = "" if target is None or created >= target else "  <-- below target"
        lines.append(f"{entity:<32} {created:>8} {str(target) if target is not None else '-':>8}{flag}")
    lines.append("")
    lines.append(f"Total tracked records: {sum(counts.values())}")
    if malformed_count:
        lines.append("")
        lines.append(
            f"WARNING: {malformed_count} records across data/created-ids/*.json are "
            f"missing an 'entity' tag and were excluded from the counts above — "
            f"check data/created-ids/ for stray files that belong in data/ instead."
        )
    return lines


def merge_entity_counts(accumulator, layer_entity_counts):
    """Add one generator's entity_counts into a running accumulator —
    additive, not overwrite, since more than one layer can touch the same
    entity (e.g. floorplandesks: created in 01_structural.py, occupancy-
    assigned via update in 03_contracts.py — both legitimately contribute
    to the same entity's tally for one run)."""
    for entity, c in layer_entity_counts.items():
        bucket = accumulator.setdefault(entity, {
            "target": 0, "created": 0, "skipped": 0, "failed": 0, "failure_reasons": Counter(),
        })
        bucket["target"] += c["target"]
        bucket["created"] += c["created"]
        bucket["skipped"] += c["skipped"]
        bucket["failed"] += c["failed"]
        bucket["failure_reasons"].update(c["failure_reasons"])


def run_reconciliation_lines(entity_counts):
    """What THIS run specifically planned vs. actually achieved, per
    entity — distinct from report_lines()'s cumulative, lifetime-tracked
    view. Built from entity_counts accumulated across every generator this
    run touched (see merge_entity_counts / pipeline.run_up_to). Only
    entities with a real shortfall get flagged, and only ones with at
    least one failure get their reasons shown — a clean run stays quiet,
    matching report_lines()'s own "only flag below target" convention."""
    if not entity_counts:
        return []

    lines = [f"{'Entity':<32} {'Target':>8} {'Created':>8} {'Existed':>8} {'Failed':>8}", "-" * 68]
    any_shortfall = False
    for entity in sorted(entity_counts):
        c = entity_counts[entity]
        target, created, skipped, failed = c["target"], c["created"], c["skipped"], c["failed"]
        accounted = created + skipped
        short = target and accounted < target
        any_shortfall = any_shortfall or short
        flag = "  <-- short" if short else ""
        lines.append(f"{entity:<32} {target:>8} {created:>8} {skipped:>8} {failed:>8}{flag}")
        if c["failure_reasons"]:
            reasons = ", ".join(f"{reason}={n}" for reason, n in c["failure_reasons"].most_common())
            lines.append(f"    reasons: {reasons}")

    lines.append("")
    lines.append(
        "Some entities fell short of this run's target — see reasons above."
        if any_shortfall else
        "Every entity's target was fully accounted for this run (created + already-existing)."
    )
    return lines


def has_shortfall(entity_counts):
    """True if any entity in a run_reconciliation_lines()-shaped dict fell
    short of its target — the machine-readable version of that report's
    "<-- short" flag, for a caller (e.g. wizard.py) that wants to signal a
    non-fully-successful run (exit code) without parsing report text."""
    return any(
        c["target"] and (c["created"] + c["skipped"]) < c["target"]
        for c in entity_counts.values()
    )


def write_entity_csvs(output_dir):
    """One CSV per entity, from the full accumulated created-ids records —
    each track_id() call across the generators now stores the full live
    record (create response, or the matching record from a list/get lookup),
    not just a curated few fields, so this needs no extra Nexudus calls of
    its own. Overwrites on every call, which is safe (and simple) since it
    always re-derives the complete picture from data/created-ids/*.json
    rather than trying to append incrementally."""
    by_entity, _malformed_count = _grouped_records()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for entity, rows in by_entity.items():
        fieldnames = ["Id"] + sorted({k for r in rows for k in r if k not in ("Id", "entity")})
        with (output_dir / f"{entity}.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, restval="", extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)


def write_report(path, reconciliation_entity_counts=None, layer_failures=None):
    """Write the report to a file with a timestamp header, for later
    reference — the point being a QA person can open this without re-running
    anything or scrolling back through a terminal.

    reconciliation_entity_counts (optional) adds a "this run" section ahead
    of the existing cumulative "what's in the account now" table — see
    run_reconciliation_lines. Omitted by callers with nothing run-specific
    to report (e.g. scripts/verify.sh, which only ever wants the cumulative
    view).

    layer_failures (optional) — an entire independent-tier layer (see
    pipeline.py::HARD_DEPENDENCY_LAYER_COUNT) that failed outright this run.
    Surfaced in its own section, ahead of everything else, so a caught
    layer-level failure is never silently swept away by a report that
    otherwise looks clean (a layer that dies before creating anything
    leaves no trace in the reconciliation table below — there's nothing to
    compare a target against)."""
    from datetime import datetime, timezone
    lines = [f"Report generated {datetime.now(timezone.utc).isoformat()}", ""]
    if layer_failures:
        lines.append(f"=== Layers that failed entirely this run ({len(layer_failures)}) ===")
        lines += [f"  - {lf}" for lf in layer_failures]
        lines.append("")
    if reconciliation_entity_counts is not None:
        lines.append("=== This run: target vs. actual ===")
        lines += run_reconciliation_lines(reconciliation_entity_counts)
        lines.append("")
        lines.append("=== What's in the account now (cumulative, all runs) ===")
    lines += report_lines()
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
