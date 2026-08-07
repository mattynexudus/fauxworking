"""
Interactive wizard — the "simpler way to run this" entry point.

Authenticates, prompts for the headline data volumes (config.
CONFIGURABLE_VOLUME_KEYS), regenerates data/*.json via prebuild.generate_all,
then runs the live seed pipeline (or a dry run) via pipeline.run_up_to,
printing the per-layer/total summary and the QA-facing "what's in the
account now" report as it goes (see generators/base.py and report_lib.py).

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

    if not dry_run and not args.yes:
        if not confirm_live():
            print("Cancelled — nothing was run live.")
            return

    print(f"\n=== Running layers 0-{layer_index} ({'dry-run' if dry_run else 'LIVE'}) ===")
    pipeline.run_up_to(layer_index, dry_run=dry_run)
    print("\nDone.")


if __name__ == "__main__":
    main()
