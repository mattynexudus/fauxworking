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
import report_lib

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

# Nexudus apiPath -> the layer that creates it, so the control panel can group
# its entity table the way the pipeline actually runs. Derived by hand from
# each generator's own create calls (pipeline.LAYERS is layer -> module, and
# LAYER_DESCRIPTIONS is layer -> prose; neither maps entities). This is a
# strict superset of report_lib.TARGET_KEY_BY_ENTITY — the extras below are
# tracked and so need a group in the table, but have no target to compare
# against: invoices are discovered live rather than created to a number
# (CLAUDE.md rule 49), ledger entries are supplements, and the rest are
# join tables / non-configurable side effects of another entity's own target
# (tariff<->timepass/extraservice links, booking guests/cancellations/credit-
# use history, event ticket types — see each's own set_target call for the
# real driving count). These six had no target key and so slipped past the
# original coverage test (which only checked TARGET_KEY_BY_ENTITY) despite
# being tracked and shown live — test_webui.py now also checks every
# entity= tag actually used across generators/*.py against this map.
LAYER_BY_ENTITY = {
    # 0 — reference
    "taxrates": 0, "financialaccounts": 0, "resourcetypes": 0,
    # 1 — structural
    "teams": 1, "tariffs": 1, "products": 1, "extraservices": 1, "timepasses": 1,
    "resources": 1, "floorplans": 1, "floorplandesks": 1, "inventoryassets": 1,
    "discountcodes": 1, "crmboards": 1, "crmboardcolumns": 1, "businesstimeslots": 1,
    "helpdeskdepartments": 1, "communitygroups": 1, "calendareventcategories": 1,
    "tarifftimepasses": 1, "tariffextraservices": 1,
    # 2 — people
    "coworkers": 2, "visitors": 2,
    # 3 — contracts
    "coworkercontracts": 3, "contractproducts": 3, "contractpausedperiods": 3,
    "contractdeposits": 3, "coworkerinventoryassets": 3,
    # 4 — activity
    "bookings": 4, "checkins": 4, "coworkerextraservices": 4,
    "coworkerbookingcredits": 4, "coworkertimepasses": 4, "coworkerproducts": 4,
    "bookingvisitors": 4, "cancelledbookings": 4, "coworkerbookingcreditusehistories": 4,
    # 5 — community
    "coworkerdeliveries": 5, "calendarevents": 5, "eventattendees": 5,
    "helpdeskmessages": 5, "communitythreads": 5, "communitymessages": 5,
    "blogposts": 5, "coworkertasks": 5, "coworkerdatafiles": 5, "eventproducts": 5,
    # 6 — financial
    "coworkerledgerentries": 6, "coworkerinvoices": 6,
    # 7 — CRM & proposals
    "crmopportunities": 7, "crmopportunityhistories": 7, "proposals": 7,
}

# Layers below this are a hard sequential dependency chain and can never be
# skipped individually — mirrored from pipeline so the UI can lock them
# without importing the constant into the frontend.
HARD_DEPENDENCY_LAYER_COUNT = pipeline.HARD_DEPENDENCY_LAYER_COUNT

# The tracked entities with no config.VOLUMES key to derive a label from
# (see LAYER_BY_ENTITY's note on why they have no target).
_EXTRA_ENTITY_LABELS = {
    "coworkerinvoices": "Invoices",
    "coworkerledgerentries": "Ledger entries",
    "bookingvisitors": "Booking guests",
    "cancelledbookings": "Cancelled bookings",
    "coworkerbookingcreditusehistories": "Booking credit use history",
    "eventproducts": "Event ticket types",
    "tarifftimepasses": "Tariff time passes",
    "tariffextraservices": "Tariff extra services",
}
# Acronyms the generic title-casing below would otherwise mangle.
_LABEL_FIXUPS = {"Crm ": "CRM ", "Crm": "CRM"}


def _entity_label(entity: str) -> str:
    """A readable name for one apiPath.

    An apiPath is unspaced ("financialaccounts"), so splitting it correctly
    means knowing the words. That knowledge already exists: every tracked
    entity maps to a snake_case config.VOLUMES key ("financial_accounts"),
    which is the same words already separated. So the label comes from the
    key rather than from guessing at the apiPath, and the 11 configurable
    ones use their existing hand-written VOLUME_LABELS verbatim.
    """
    key = report_lib.TARGET_KEY_BY_ENTITY.get(entity)
    if key is None:
        return _EXTRA_ENTITY_LABELS.get(entity, entity)
    if key in VOLUME_LABELS:
        return VOLUME_LABELS[key]
    label = key.replace("_", " ").capitalize()
    for wrong, right in _LABEL_FIXUPS.items():
        if label.startswith(wrong):
            label = right + label[len(wrong):]
    return label


ENTITY_LABELS = {e: _entity_label(e) for e in LAYER_BY_ENTITY}

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
    max: object = None         # hard cap — rejected by _coerce
    soft_max: object = None    # advisory — the UI warns "large / long run" past this
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
    guided_only: bool = False  # True => the guided flow renders itself from this entry
    hidden: bool = False       # True => runnable via /api/run and the CLI, but not shown as a card

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
        # advisory ceiling: well above the default, but past it a run gets slow
        soft = None if key == "coworkers" else max(400, config.VOLUMES[key] * 5)
        out.append(Param(
            name=key, type="int", label=VOLUME_LABELS[key], flag=flag,
            default=default, min=0, max=max_, soft_max=soft, help=help_text,
        ))
    return out


REGISTRY = [
    Command(
        # Not shown as a card: the guided "Set up demo data" flow now skips the
        # regenerate step when nothing changed, so it covers "seed the current
        # plan" too. Kept here for CLI/API parity (python pipeline.py, or a
        # direct POST /api/run).
        id="pipeline",
        label="Seed data to Nexudus",
        group="seed",
        tone="live",
        hidden=True,
        description="Seed data/*.json into the live account without regenerating it first.",
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
                  soft_max=14, help="N days ending at the target date — one run per day, so a big number is a long run"),
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
            Param("layer", "choice", "Through layer", flag="--layer", default="",
                  choices=[["", "All layers (0–7)"]] + _LAYER_CHOICES,
                  min=0, max=_MAX_LAYER, help="stop after this layer; earlier layers run first"),
            Param("export_csv", "bool", "Export CSVs to output/", flag="--export-csv",
                  default=True, help="writes output/*.csv when this run goes live"),
            Param("fresh", "bool", "Start data fresh", flag="--fresh", default=False,
                  help="ignore what's already generated and rebuild every file"),
            Param("skip_layers", "layers", "Layers to skip", flag="--skip-layer", default=None,
                  min=HARD_DEPENDENCY_LAYER_COUNT, max=_MAX_LAYER,
                  help=f"omit individual layers; only {HARD_DEPENDENCY_LAYER_COUNT}-{_MAX_LAYER} "
                       f"can be skipped"),
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
        params=[
            Param("mode", "choice", "Mode", flag="--mode", default="tracked",
                  choices=[["tracked", "tracked — only IDs this tool logged"],
                           ["clean", "clean — everything found live"]],
                  help="what to consider for deletion"),
            # Post-teardown cleanups — no-ops on a dry run (build_argv drops
            # them), so the dialog only shows them for a real run.
            Param("clear_data", "bool", "Also delete data/*.json plan files",
                  flag="--clear-generated-data", default=False,
                  help="prebuild's plan files; safe to keep — regenerated on the next setup"),
            Param("clear_csv", "bool", "Also delete output/*.csv exports",
                  flag="--clear-csv-outputs", default=False,
                  help="the exported per-entity CSVs; rebuilt on the next seed + export"),
            Param("reset_counters", "bool", "Reset this location's billing counters to 0",
                  flag="--reset-counters", default=False,
                  help="Booking/Invoice/Draft/CreditNote numbers Nexudus keeps incrementing"),
        ],
        accepts_business_id=True,
        offers_dry_run=True,
        destructive=True,
        confirm_phrase="delete tracked records",
        writes_live=True,
        notes="Clean mode wipes every record found live, tracked or not — it needs a "
              "stronger typed phrase than a tracked teardown.",
    ),
]

# Clean mode (a full live wipe, not just tracked IDs) demands a distinct,
# stronger typed phrase than a tracked teardown's confirm_phrase.
TEARDOWN_CLEAN_CONFIRM_PHRASE = "delete everything"

# teardown params that drive a post-teardown cleanup rather than the delete
# itself — meaningless on a dry run (see build_argv).
_TEARDOWN_CLEANUP_PARAMS = {"clear_data", "clear_csv", "reset_counters"}

BY_ID = {c.id: c for c in REGISTRY}


def confirm_phrase_for(command_id, params=None):
    """The exact phrase a caller must type back to run this command live.
    Static per command (Command.confirm_phrase) except teardown, where
    `--mode clean` — a full live wipe, not just the IDs this tool tracked —
    requires the stronger TEARDOWN_CLEAN_CONFIRM_PHRASE. jobs.JobManager
    and the frontend both resolve the required phrase through here so they
    can't disagree about which one applies."""
    cmd = BY_ID.get(command_id)
    if cmd is None:
        return None
    if command_id == "teardown" and (params or {}).get("mode") == "clean":
        return TEARDOWN_CLEAN_CONFIRM_PHRASE
    return cmd.confirm_phrase


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
    if p.type == "layers":
        # A set of layer indices, emitted as a repeated flag. Accepts a list or
        # a comma-separated string. Bounds are enforced here so a bad value is
        # a clean 400 rather than the child process dying on argparse — but
        # pipeline._validate_skip_layers checks again on the other side, since
        # the CLI is reachable without going through this at all.
        raw = val.split(",") if isinstance(val, str) else val
        if not isinstance(raw, (list, tuple, set)):
            raise BadRequest(f"{p.name}: expected a list of layer numbers, got {val!r}")
        out = []
        for item in raw:
            if isinstance(item, str) and not item.strip():
                continue
            try:
                n = int(item)
            except (TypeError, ValueError):
                raise BadRequest(f"{p.name}: expected whole numbers, got {item!r}")
            if p.min is not None and n < p.min:
                raise BadRequest(
                    f"{p.name}: layer {n} can't be skipped — layers 0-{p.min - 1} are a hard "
                    f"dependency chain every later layer reads from")
            if p.max is not None and n > p.max:
                raise BadRequest(f"{p.name}: no such layer {n} (valid: 0-{p.max})")
            out.append(n)
        return sorted(set(out)) or None
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

    layer = _coerce(by_name["layer"], params.get("layer"))
    if layer in (None, ""):
        layer = _MAX_LAYER          # blank / "all layers" => through the last one

    # Skipping a trailing layer is the same thing as lowering the ceiling, and
    # the ceiling says it in one token instead of several — so walk the ceiling
    # down past anything skipped at the top before emitting either. Doing this
    # here (rather than expecting the caller to send a matching `layer`) keeps
    # "layer N is deselected" a single unambiguous instruction: skip_layers is
    # the whole truth, and --layer 7 --skip-layer 7 can't disagree with itself.
    skip = _coerce(by_name["skip_layers"], params.get("skip_layers")) or []
    while layer >= 0 and layer in skip:
        layer -= 1
    if layer < 0:
        raise BadRequest("Every layer is deselected — there's nothing to run.")
    argv += ["--layer", str(layer)]

    export = params.get("export_csv", by_name["export_csv"].default)
    argv.append("--export-csv" if _as_bool(export) else "--no-export-csv")

    if _as_bool(params.get("fresh", False)):
        argv.append("--fresh")

    # Whatever's left below the (possibly lowered) ceiling is a genuine gap.
    for n in skip:
        if n < layer:
            argv += ["--skip-layer", str(n)]

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

    if command_id == "teardown":
        # The post-teardown cleanups (delete plan files / CSVs, reset billing
        # counters) only happen on a real run — teardown.py never consults
        # them under --dry-run. Drop them from a preview argv so the CLI echo
        # doesn't imply otherwise.
        if dry_run:
            params = {k: v for k, v in params.items() if k not in _TEARDOWN_CLEANUP_PARAMS}

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

    # A live clean teardown is gated by a typed phrase at the panel/server
    # layer (confirm_phrase_for), so teardown.py's own interactive
    # "delete everything" prompt would just stall a non-interactive run —
    # --yes tells it that confirmation already happened.
    if command_id == "teardown" and params.get("mode") == "clean" and not dry_run:
        argv.append("--yes")

    return argv


def pretty_argv(argv) -> list:
    """argv with the interpreter path collapsed to `python` and `-u` dropped —
    for display only, never for execution."""
    out = list(argv)
    if out and out[0] == PY[0]:
        out[0] = "python"
        if len(out) > 1 and out[1] == "-u":
            del out[1]
    return out


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
        "hidden": c.hidden,
        "params": [
            {
                "name": p.name, "type": p.type, "label": p.label, "flag": p.flag,
                "default": p.default, "choices": p.choices, "help": p.help,
                "min": p.min, "max": p.max, "soft_max": p.soft_max, "selector": p.selector,
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
        # Layer taxonomy for the entity table: which layer creates each entity,
        # each layer's human label, and how many leading layers can't be
        # deselected (see pipeline.HARD_DEPENDENCY_LAYER_COUNT).
        "layers": [{"index": i, "label": LAYER_DESCRIPTIONS.get(i, cls), "class": cls}
                   for i, (_mod, cls) in enumerate(pipeline.LAYERS)],
        "layer_by_entity": LAYER_BY_ENTITY,
        "entity_labels": ENTITY_LABELS,
        "hard_dependency_layer_count": HARD_DEPENDENCY_LAYER_COUNT,
        # Which entity row each editable volume knob controls. The frontend
        # builds its table from layer_by_entity (every entity, tracked or not)
        # and needs this to hang the 11 number inputs on the right rows —
        # report.py's per-row "key" only exists once something is tracked.
        "entity_by_volume_key": {
            key: entity
            for entity, key in report_lib.TARGET_KEY_BY_ENTITY.items()
            if key in config.CONFIGURABLE_VOLUME_KEYS
        },
    }
