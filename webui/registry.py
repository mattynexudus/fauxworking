"""
The set of project commands the browser control panel can run, as data.

Each `Command` maps a UI card to an argv list. `build_argv()` is the single
place that turns a command id + a dict of form values into the exact list
handed to `subprocess.Popen` (no shell, ever), and is also the validation
chokepoint — a bad value fails here with a `BadRequest`, before anything is
spawned. `commands_json()` is the JSON-safe view the frontend renders forms
and grouped sections from.

Reuses the project's own sources of truth rather than re-listing anything:
`pipeline.LAYERS` (layer -> generator module) and `prebuild.FLAG_SPEC`
(volume key -> CLI flag). Nothing here touches the network.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field

import config
import pipeline
import prebuild

# The child interpreter, always unbuffered so its output streams line-by-line.
PY = [sys.executable, "-u"]

# Nexudus enforces ~50 coworker creations per day per account (CLAUDE.md
# rule 30 / wizard.COWORKER_DAILY_LIMIT). Mirrored here so the prebuild /
# wizard fields can pre-fill a default that won't immediately trip it, and
# carry that bound as Param.max so the field itself explains it.
COWORKER_DAILY_LIMIT = 50

# config key -> human label, for the 11 configurable volume knobs. Kept in
# step with wizard.VOLUME_LABELS (same keys as config.CONFIGURABLE_VOLUME_KEYS).
VOLUME_LABELS = {
    "coworkers": "Coworkers",
    "visitors": "Visitors",
    "bookings_total": "Bookings",
    "check_ins": "Check-ins",
    "crm_opportunities": "CRM opportunities",
    "proposals": "Proposals",
    "help_desk_messages": "Help desk messages",
    "community_threads": "Community threads",
    "coworker_tasks": "Coworker tasks",
    "coworker_time_passes": "Coworker time passes",
    "coworker_products": "Coworker products",
}

# The 4 shown up-front in the guided flow's first step; the rest sit behind
# "N more settings". Picked as the volumes most people actually think about.
HEADLINE_VOLUME_KEYS = ["coworkers", "bookings_total", "check_ins", "visitors"]

# Human text per layer — CLAUDE.md's own "Key commands" list, condensed.
# Shown in the layer picker instead of raw class names like
# "ReferenceGenerator", which mean nothing to someone driving this from a
# browser rather than reading generators/0N_*.py.
LAYER_DESCRIPTIONS = {
    0: "Reference — tax rates, financial accounts, resource types",
    1: "Structural — tariffs, products, resources, desks, inventory, discounts, CRM boards",
    2: "People — coworkers and visitors",
    3: "Contracts — contracts, deposits, freezes, occupancy",
    4: "Activity — bookings, check-ins, credits, passes",
    5: "Community — deliveries, events, help desk, threads, blogs, tasks",
    6: "Financial — invoices, payments, credits",
    7: "CRM & proposals — opportunities, proposals (accepting one can auto-create a contract)",
}

_MAX_LAYER = len(pipeline.LAYERS) - 1
_LAYER_CHOICES = [[i, f"{i} · {LAYER_DESCRIPTIONS.get(i, cls)}"]
                   for i, (_mod, cls) in enumerate(pipeline.LAYERS)]

# Command.group values, in the order the panel shows them — the taxonomy the
# individual-commands section is actually built from (previously present
# only as a per-card label, never used to group anything). Local data
# generation isn't its own group: the guided "Set up demo data" flow is the
# way to (re)generate data/*.json — a standalone prebuild button just
# duplicated its first step.
GROUPS = [
    {"id": "seed", "label": "Seed to Nexudus", "blurb": "Writes records to the live account.",
     "tone": "live"},
    {"id": "maintain", "label": "Keep it fresh", "blurb": "Ongoing upkeep against the live account.",
     "tone": "live"},
    {"id": "check", "label": "Check", "blurb": "Read-only — never creates, changes or deletes.",
     "tone": "safe"},
    {"id": "danger", "label": "Danger zone",
     "blurb": "Deletes records. The live variant needs a typed confirmation.", "tone": "danger"},
]
GROUP_IDS = {g["id"] for g in GROUPS}


class BadRequest(Exception):
    """A command/params combination the caller got wrong — surfaced to the
    browser as HTTP 400, never spawned."""


@dataclass
class Param:
    name: str                 # key in the POSTed params dict
    type: str                 # int | bool | tribool | str | date | choice
    label: str
    flag: str | None = None   # "--days"; None => positional (or a selector)
    default: object = None
    choices: list | None = None   # [[value, label], ...] for type == "choice"
    help: str = ""             # shown as a caption beside the field, every type
    min: object = None
    max: object = None
    selector: bool = False    # True => not an argv token; it picks the target script


@dataclass
class Command:
    id: str
    label: str
    group: str                 # one of GROUPS' ids
    description: str
    argv_head: list            # ["pipeline.py"] or ["bash", "scripts/verify.sh"]
    params: list = field(default_factory=list)
    accepts_business_id: bool = False
    offers_dry_run: bool = False
    destructive: bool = False
    confirm_phrase: str | None = None
    writes_live: bool = False
    notes: str = ""
    tone: str = "safe"         # safe | read | live | danger — the badge shown on its card
    guided_only: bool = False  # True => not listed; only reachable via the guided flow

    def __post_init__(self):
        assert self.group in GROUP_IDS, f"{self.id}: unknown group {self.group!r}"


def _volume_params(with_defaults: bool) -> list:
    out = []
    for key in config.CONFIGURABLE_VOLUME_KEYS:
        flag, _dest = prebuild.FLAG_SPEC[key]
        default = None
        max_ = None
        if with_defaults:
            default = config.VOLUMES[key]
            if key == "coworkers":
                default = min(default, COWORKER_DAILY_LIMIT)
        if key == "coworkers":
            max_ = COWORKER_DAILY_LIMIT
        help_text = f"default {config.VOLUMES[key]}"
        if key == "coworkers":
            help_text = f"Nexudus caps this at ~{COWORKER_DAILY_LIMIT}/day, per account"
        out.append(Param(
            name=key, type="int", label=VOLUME_LABELS[key], flag=flag,
            default=default, min=0, max=max_, help=help_text,
        ))
    return out


REGISTRY = [
    Command(
        id="pipeline",
        label="Seed data to Nexudus",
        group="seed",
        tone="live",
        description="Run the live seed chain. Pick a layer to stop after — earlier layers "
                    "always run first (re-running them is a safe no-op).",
        argv_head=["pipeline.py"],
        params=[Param("layer", "choice", "Through layer", flag=None, default="",
                      choices=[["", "All layers (0–7)"]] + _LAYER_CHOICES,
                      min=0, max=_MAX_LAYER,
                      help="stop after this layer")],
        accepts_business_id=True,
        offers_dry_run=True,
        writes_live=True,
    ),
    Command(
        id="daily_update",
        label="Daily update",
        group="maintain",
        tone="live",
        description="Create today's fresh check-ins, bookings, visitors and deliveries "
                    "(and close yesterday's open ones).",
        argv_head=["generators/daily_update.py"],
        params=[
            Param("date", "date", "Target date", flag="--date", help="default: today"),
            Param("days", "int", "Days to backfill", flag="--days", default=1, min=1,
                  help="N days ending at the target date"),
        ],
        accepts_business_id=True,
        offers_dry_run=True,
        writes_live=True,
    ),
    Command(
        id="refresh_output",
        label="Re-sync output/ CSVs",
        group="maintain",
        tone="read",
        description="Pull in any invoices Nexudus raised on its own and re-export every "
                    "output/*.csv. Read-only against Nexudus. Slow: one list call per invoice.",
        argv_head=["refresh_output.py"],
        accepts_business_id=True,
    ),
    Command(
        id="verify",
        label="Verify tracked vs target",
        group="check",
        tone="safe",
        description="Count tracked records in data/created-ids/*.json against the "
                    "configured targets. Offline, no network, no writes.",
        argv_head=["bash", "scripts/verify.sh"],
    ),
    Command(
        id="wizard",
        label="Guided run (non-interactive)",
        group="seed",
        tone="live",
        guided_only=True,  # driven by the guided flow, not listed as its own card
        description="prebuild + pipeline in one go. Regenerates data/*.json, then seeds.",
        argv_head=["wizard.py"],
        params=[
            Param("layer", "int", "Through layer", flag="--layer", default=_MAX_LAYER,
                  min=0, max=_MAX_LAYER, help=f"0-{_MAX_LAYER}"),
            Param("seed", "int", "Seed", flag="--seed", default=config.RANDOM_SEED,
                  help="reproducible — same seed, same data"),
            Param("export_csv", "bool", "Export CSVs to output/", flag="--export-csv",
                  default=True, help="writes output/*.csv when this run goes live"),
            Param("fresh", "bool", "Start data fresh", flag="--fresh", default=False,
                  help="ignore what's already generated and rebuild every file"),
        ] + _volume_params(with_defaults=True),
        accepts_business_id=True,
        offers_dry_run=True,
        writes_live=True,
        notes="Also regenerates data/*.json (via prebuild) — incrementally by default, so "
              "existing records are kept and only new ones appended.",
    ),
    Command(
        id="teardown",
        label="Teardown",
        group="danger",
        tone="danger",
        description="Delete records this tool created. Dry-run (the default) previews and "
                    "touches nothing; the live delete needs the confirmation phrase typed.",
        argv_head=["teardown.py"],
        params=[Param("mode", "choice", "Mode", flag="--mode", default="tracked",
                      choices=[["tracked", "tracked — only IDs this tool logged"],
                               ["clean", "clean — everything found live (preview only)"]],
                      help="what to consider for deletion")],
        accepts_business_id=True,
        offers_dry_run=True,
        destructive=True,
        confirm_phrase="delete tracked records",
        writes_live=True,
        notes="Clean mode can only be previewed here — a real clean wipe stays terminal-only.",
    ),
]

BY_ID = {c.id: c for c in REGISTRY}


def _as_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _coerce(p: Param, val):
    """Validate + normalise one form value. None / "" => None (flag omitted)."""
    if val is None or (isinstance(val, str) and val.strip() == ""):
        return None
    if p.type == "int":
        try:
            n = int(val)
        except (TypeError, ValueError):
            raise BadRequest(f"{p.name}: expected a whole number, got {val!r}")
        if p.min is not None and n < p.min:
            raise BadRequest(f"{p.name}: must be at least {p.min}")
        if p.max is not None and n > p.max:
            raise BadRequest(f"{p.name}: must be at most {p.max}")
        return n
    if p.type == "date":
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(val)):
            raise BadRequest(f"{p.name}: expected YYYY-MM-DD, got {val!r}")
        return str(val)
    if p.type == "choice":
        allowed = [c[0] for c in (p.choices or [])]
        for a in allowed:
            if str(a) == str(val):
                return a
        raise BadRequest(f"{p.name}: {val!r} is not one of {allowed}")
    if p.type in ("bool", "tribool"):
        return _as_bool(val)
    return str(val)


def _build_wizard_argv(params, business_id, dry_run) -> list:
    """wizard.py has unusual arg semantics (--yes always; --dry-run vs --live;
    every prompt must be pre-answered or it stops on EOF), so it gets its own
    builder. Every configurable field is emitted unconditionally, falling back
    to the card default when the form left it blank."""
    cmd = BY_ID["wizard"]
    argv = list(PY) + ["wizard.py", "--yes", "--dry-run" if dry_run else "--live"]

    by_name = {p.name: p for p in cmd.params}

    layer = _coerce(by_name["layer"], params.get("layer")) or by_name["layer"].default
    argv += ["--layer", str(layer)]

    seed = _coerce(by_name["seed"], params.get("seed"))
    if seed is not None:
        argv += ["--seed", str(seed)]

    export = params.get("export_csv", by_name["export_csv"].default)
    argv.append("--export-csv" if _as_bool(export) else "--no-export-csv")

    if _as_bool(params.get("fresh", False)):
        argv.append("--fresh")

    for key in config.CONFIGURABLE_VOLUME_KEYS:
        p = by_name[key]
        val = _coerce(p, params.get(key))
        if val is None:
            val = p.default
        argv += [p.flag, str(val)]

    if business_id is not None and not dry_run:
        argv += ["--business-id", str(business_id)]
    return argv


def build_argv(command_id, params=None, business_id=None, dry_run=False) -> list:
    """command id + form values -> the exact argv for subprocess.Popen.

    Order: interpreter, script, positionals, value/bool flags, --business-id,
    --dry-run. Raises BadRequest for an unknown command or an invalid value.
    """
    params = params or {}
    cmd = BY_ID.get(command_id)
    if cmd is None:
        raise BadRequest(f"Unknown command: {command_id!r}")

    if command_id == "wizard":
        return _build_wizard_argv(params, business_id, dry_run)

    if command_id == "teardown" and params.get("mode", "tracked") == "clean" and not dry_run:
        raise BadRequest("Clean mode can only be previewed — it never runs live from the panel.")

    if cmd.argv_head and cmd.argv_head[0] == "bash":
        argv = list(cmd.argv_head)
    else:
        argv = list(PY) + list(cmd.argv_head)

    # positionals (only pipeline's optional `layer`)
    for p in cmd.params:
        if p.flag is not None or p.selector:
            continue
        val = _coerce(p, params.get(p.name, p.default))
        if val is not None:
            argv.append(str(val))

    # value / bool / tri-bool flags
    for p in cmd.params:
        if p.flag is None or p.selector:
            continue
        val = _coerce(p, params.get(p.name, p.default))
        if val is None:
            continue
        if p.type == "bool":
            if val:
                argv.append(p.flag)
        elif p.type == "tribool":
            argv.append(p.flag if val else "--no-" + p.flag[2:])
        else:
            argv += [p.flag, str(val)]

    if cmd.accepts_business_id and business_id is not None:
        argv += ["--business-id", str(business_id)]

    if cmd.offers_dry_run and dry_run:
        argv.append("--dry-run")

    return argv


def _command_json(c: Command) -> dict:
    return {
        "id": c.id,
        "label": c.label,
        "group": c.group,
        "description": c.description,
        "destructive": c.destructive,
        "confirm_phrase": c.confirm_phrase,
        "accepts_business_id": c.accepts_business_id,
        "offers_dry_run": c.offers_dry_run,
        "writes_live": c.writes_live,
        "notes": c.notes,
        "tone": c.tone,
        "guided_only": c.guided_only,
        "params": [
            {
                "name": p.name, "type": p.type, "label": p.label, "flag": p.flag,
                "default": p.default, "choices": p.choices, "help": p.help,
                "min": p.min, "max": p.max, "selector": p.selector,
            }
            for p in c.params
        ],
    }


def commands_json() -> dict:
    """JSON-safe view for GET /api/commands: the ordered group taxonomy plus
    every command (including guided_only ones, e.g. `wizard` — the guided
    flow renders itself from that entry's params rather than duplicating
    the volume-field definitions; the frontend excludes guided_only entries
    from the plain command-card list)."""
    return {
        "groups": GROUPS,
        "headline_volume_keys": HEADLINE_VOLUME_KEYS,
        "commands": [_command_json(c) for c in REGISTRY],
    }
