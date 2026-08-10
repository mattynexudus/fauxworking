"""
Interactive wizard — the "simpler way to run this" entry point.

Authenticates, prompts for the headline data volumes (config.
CONFIGURABLE_VOLUME_KEYS), regenerates data/*.json via prebuild.generate_all,
then — for a live run — asks which business (location) to seed into if the
login has access to more than one (see pipeline.list_businesses /
pipeline._select_business), before running the pipeline via
pipeline.run_up_to and printing the per-layer/total summary and the
QA-facing "what's in the account now" report as it goes (see
generators/base.py and report_lib.py).

Stdlib only — no new dependency beyond what's already in requirements.txt.

Usage:
    python wizard.py                                    # fully interactive
    python wizard.py --coworkers 10 --bookings 20 --live --yes --layer 4
                                                          # non-interactive
    python wizard.py --dry-run                           # skip the prompts
                                                          # that only matter live
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
import nexudus_auth
import pipeline
import prebuild

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


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=None, help="Random seed (default: prompt)")
    for volume_key in config.CONFIGURABLE_VOLUME_KEYS:
        flag, dest = prebuild.FLAG_SPEC[volume_key]
        parser.add_argument(flag, dest=dest, type=int, default=None,
                             help=f"{VOLUME_LABELS[volume_key]} (default: prompt)")
    parser.add_argument("--live", action="store_true", help="Run live, skipping the dry-run/live prompt")
    parser.add_argument("--dry-run", action="store_true", help="Run dry-run, skipping the dry-run/live prompt")
    parser.add_argument("--layer", type=int, default=None,
                         help=f"How many layers to run, 0-{len(pipeline.LAYERS) - 1} (default: prompt)")
    parser.add_argument("--yes", action="store_true",
                         help="Skip the live-run confirmation prompt (for non-interactive/scripted use)")
    parser.add_argument("--business-id", type=int, default=None,
                         help="Which business/location to seed into, if this login has access to more than one (default: prompt)")
    return parser.parse_args()


def ensure_authenticated():
    print("=== Authentication ===")
    try:
        nexudus_auth.get_access_token()
        print("✓ Already authenticated with Nexudus.\n")
    except SystemExit:
        print("Not yet authenticated with Nexudus — let's get you logged in.\n")
        nexudus_auth.setup()
        print()


def _prompt_int(label, default):
    raw = input(f"{label} [{default}]: ").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print("Please enter a whole number.")
        return _prompt_int(label, default)


def collect_volumes(args):
    print("=== Data volumes ===")
    print("Press Enter to keep the default shown in brackets.\n")
    overrides = {}
    for volume_key in config.CONFIGURABLE_VOLUME_KEYS:
        _, dest = prebuild.FLAG_SPEC[volume_key]
        cli_value = getattr(args, dest)
        if cli_value is not None:
            overrides[volume_key] = cli_value
            continue
        overrides[volume_key] = _prompt_int(VOLUME_LABELS[volume_key], config.VOLUMES[volume_key])
    print()
    return {**config.VOLUMES, **overrides}


def collect_seed(args):
    if args.seed is not None:
        return args.seed
    return _prompt_int("Random seed", config.RANDOM_SEED)


def collect_run_mode(args):
    if args.dry_run:
        return True
    if args.live:
        return False
    answer = input("Run [D]ry-run or [L]ive? [D]: ").strip().lower()
    return answer not in ("l", "live")


def collect_layer_index(args):
    max_layer = len(pipeline.LAYERS) - 1
    if args.layer is not None:
        return max(0, min(max_layer, args.layer))
    names = "\n".join(f"  {i}: {cls}" for i, (_, cls) in enumerate(pipeline.LAYERS))
    print(f"Layers:\n{names}")
    return _prompt_int(f"Run through which layer (0-{max_layer})", max_layer)


def collect_business_id(args):
    """Which business (location) to seed into — only relevant for live runs;
    dry-run doesn't touch the account at all, so callers should skip this
    entirely in that case rather than make a needless API call."""
    if args.business_id is not None:
        return args.business_id

    businesses = pipeline.list_businesses()
    if len(businesses) <= 1:
        return None  # nothing to choose — pipeline._select_business resolves it directly

    print("=== Business / location ===")
    print(f"This login has access to {len(businesses)} businesses — which one should this run use?")
    for b in businesses:
        print(f"  {b['Id']}: {b.get('Name', '?')}")
    valid_ids = {b["Id"] for b in businesses}

    while True:
        raw = input("Business ID: ").strip()
        try:
            chosen = int(raw)
        except ValueError:
            print("Please enter one of the business IDs listed above.")
            continue
        if chosen not in valid_ids:
            print("That ID isn't in the list above — try again.")
            continue
        print()
        return chosen


def confirm_live():
    answer = input("\nThis will create real records in the live Nexudus account. "
                    "Type 'yes' to continue: ").strip()
    return answer == "yes"


def main():
    args = parse_args()

    ensure_authenticated()

    volumes = collect_volumes(args)
    seed = collect_seed(args)

    print("=== Generating data ===")
    prebuild.generate_all(seed, volumes)
    print()

    dry_run = collect_run_mode(args)
    layer_index = collect_layer_index(args)

    business_id = None if dry_run else collect_business_id(args)

    if not dry_run and not args.yes:
        if not confirm_live():
            print("Cancelled — nothing was run live.")
            return

    print(f"\n=== Running layers 0-{layer_index} ({'dry-run' if dry_run else 'LIVE'}) ===")
    pipeline.run_up_to(layer_index, dry_run=dry_run, business_id=business_id)
    print("\nDone.")


if __name__ == "__main__":
    main()
