"""
Interactive wizard — the "simpler way to run this" entry point.

Authenticates (retrying in place if login fails, rather than exiting),
prompts for the headline data volumes (config.CONFIGURABLE_VOLUME_KEYS),
regenerates data/*.json via prebuild.generate_all, offers an optional
preview (dry run) that flows straight into a real run in the same session
if you confirm — no re-entering your answers, no restarting the script —
then asks which business (location) to seed into if the login has access
to more than one (see pipeline.list_businesses / pipeline._select_business),
runs the pipeline via pipeline.run_up_to, and prints the per-layer/total
summary and the QA-facing "what's in the account now" report as it goes
(see generators/base.py and report_lib.py).

Stdlib only — no new dependency beyond what's already in requirements.txt.

Usage:
    python wizard.py                                    # fully interactive
    python wizard.py --coworkers 10 --bookings 20 --live --yes --layer 4
                                                          # non-interactive
    python wizard.py --dry-run                           # preview only, no
                                                          # live follow-up offered
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
    parser.add_argument("--seed", type=int, default=None,
                         help="Random seed (default: fixed, not prompted — this is an internal detail most people don't need to think about)")
    for volume_key in config.CONFIGURABLE_VOLUME_KEYS:
        flag, dest = prebuild.FLAG_SPEC[volume_key]
        parser.add_argument(flag, dest=dest, type=int, default=None,
                             help=f"{VOLUME_LABELS[volume_key]} (default: prompt)")
    parser.add_argument("--live", action="store_true",
                         help="Skip the preview and go straight to a live run")
    parser.add_argument("--dry-run", action="store_true",
                         help="Preview only — don't offer to run live afterward")
    parser.add_argument("--layer", type=int, default=None,
                         help=f"How many layers to run, 0-{len(pipeline.LAYERS) - 1} (default: all of them)")
    parser.add_argument("--yes", action="store_true",
                         help="Skip the live-run confirmation prompt (for non-interactive/scripted use)")
    parser.add_argument("--business-id", type=int, default=None,
                         help="Which business/location to seed into, if this login has access to more than one (default: prompt)")
    return parser.parse_args()


def _confirm(question, default):
    """Y/n style confirmation for flow decisions — lighter-weight than
    confirm_live()'s "type yes", which is reserved for the one moment that
    actually creates real records."""
    hint = "[Y/n]" if default else "[y/N]"
    answer = input(f"{question} {hint}: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def ensure_authenticated():
    """Logs in if needed. Retries in place on failure (wrong password, a
    dropped connection, etc.) instead of letting the whole wizard exit —
    previously any login hiccup meant starting over from scratch."""
    print("=== Authentication ===")
    try:
        nexudus_auth.get_access_token()
        print("✓ Already authenticated with Nexudus.\n")
        return
    except SystemExit:
        pass

    print("Not yet authenticated with Nexudus — let's get you logged in.\n")
    while True:
        try:
            nexudus_auth.setup()
            print()
            return
        except SystemExit as e:
            print(f"\nLogin didn't work: {e}\n")
            if not _confirm("Try again?", default=True):
                raise SystemExit("Can't continue without logging in to Nexudus.")
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


def collect_layer_index(args):
    """How many layers to run. Defaults to "everything" on a single Enter
    press — the layer-by-layer breakdown is only shown to people who
    actively want to stop partway through (e.g. for testing one area)."""
    max_layer = len(pipeline.LAYERS) - 1
    if args.layer is not None:
        return max(0, min(max_layer, args.layer))

    if _confirm("Generate everything (recommended)?", default=True):
        return max_layer

    names = "\n".join(f"  {i}: {cls}" for i, (_, cls) in enumerate(pipeline.LAYERS))
    print(f"\nLayers:\n{names}")
    return _prompt_int(f"Run through which layer (0-{max_layer})", max_layer)


def collect_business_id(args):
    """Which business (location) to seed into — only relevant once we're
    actually about to touch the live account."""
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


def run_live(layer_index, args):
    """Runs the live pipeline, retrying in place on failure rather than
    letting the whole wizard die and forcing a restart from scratch.

    Retrying is safe and fast, not a repeat of everything: every record
    this tool creates is tracked locally as it goes (see generators/base.py),
    and each generator checks that tracking before creating anything — so a
    retry picks back up right where the failure happened instead of
    recreating what already succeeded. Nothing here hides or swallows the
    actual error; it's always shown in full before asking whether to retry.
    """
    business_id = collect_business_id(args)

    if not args.yes and not confirm_live():
        print("Cancelled — nothing was run live.")
        return

    while True:
        print(f"\n=== Running layers 0-{layer_index} (LIVE) ===")
        try:
            pipeline.run_up_to(layer_index, dry_run=False, business_id=business_id)
            print("\nDone.")
            return
        except Exception as e:
            print(f"\n--- Something went wrong partway through ---\n{e}\n")
            print("Records already created are safely tracked, so retrying "
                  "won't recreate them or start over from the beginning.")
            if not _confirm("Try again?", default=True):
                print("\nStopped. Run `python3 wizard.py` again anytime — "
                      "it'll pick back up from where this left off.")
                return


def main():
    args = parse_args()

    ensure_authenticated()

    volumes = collect_volumes(args)
    seed = args.seed if args.seed is not None else config.RANDOM_SEED

    print("=== Generating data ===")
    prebuild.generate_all(seed, volumes)
    print()

    layer_index = collect_layer_index(args)

    if args.dry_run:
        # Explicit --dry-run: preview only, no live follow-up offered.
        print(f"\n=== Previewing layers 0-{layer_index} (nothing will be created) ===")
        pipeline.run_up_to(layer_index, dry_run=True)
        print("\nDone — that was a preview, nothing was created.")
        return

    if not args.live:
        # Interactive default: offer a preview, then — without restarting
        # anything or re-asking for volumes — offer to go live right away.
        if _confirm("\nPreview first before creating real records (recommended)?", default=True):
            print(f"\n=== Previewing layers 0-{layer_index} (nothing will be created) ===")
            pipeline.run_up_to(layer_index, dry_run=True)
            if not _confirm("\nThat was a preview. Run this for real now?", default=False):
                print("\nDone — no real records were created. Run `python3 wizard.py` again anytime.")
                return

    run_live(layer_index, args)


if __name__ == "__main__":
    try:
        main()
    except EOFError:
        # No more input to read (e.g. stdin closed/piped dry) — a clean
        # message beats a raw traceback here.
        print("\n\nNo more input to read — stopping. Run `python3 wizard.py` again anytime.")
