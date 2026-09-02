"""
Live execution chain — runs generator layers 0..N in-process against the
real Nexudus API, threading each layer's output into the next.

Every generator's `run()` was built to take a `prev_output` dict from the
previous layer, but standalone `python generators/03_contracts.py` has no
prior layer to hand it one. This module is what makes that work anyway: it
re-runs every earlier layer first (each generator already checks for
existing records before creating — see `already_created`/name-lookups in
`generators/base.py` — so re-running an earlier layer is a fast, safe
no-op for anything already there, not a duplicate-creation risk).

    python pipeline.py                        # run every layer, 0 through 7
    python pipeline.py 3                       # run layers 0-3 (contracts and
                                                 # everything it depends on)
    python pipeline.py --business-id 12345     # pick a business explicitly,
                                                 # for logins with access to
                                                 # more than one (see
                                                 # _select_business below)

Each individual generator's `__main__` live branch calls `run_up_to(N)` for
its own layer index — that's the whole live-mode implementation for all of
them. `seed_all.sh` just calls this file with no argument.
"""

import argparse
import importlib
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
import nexudus_client as client
import report_lib

# (module filename without .py, class name, entity_name from the class) —
# owned by report_lib.py (it needs the same list to compute real_targets()
# without importing pipeline back, which would be circular) and re-exported
# here as a plain attribute so `pipeline.LAYERS = [...]` (test fakes) still
# works exactly as before.
LAYERS = report_lib.LAYERS

# Layers 0-3 (reference, structural, people, contracts) are a hard,
# sequential dependency chain — everything after reads IDs from them
# directly (coworker_ids, tariff_ids, contract_defs, ...), so a failure
# there still halts the whole run, same as always. Layers 4a-7 (activity,
# community, financial, CRM/proposals) were checked directly against each
# of their own run() signatures — none of them reads a prev_output key
# that only another one of these four adds — so one of them failing
# outright doesn't leave the next one working from a genuinely broken
# foundation; prev_output just never gains that layer's own new key(s),
# which none of the others need. A failure there is caught, logged
# clearly, and the run proceeds to the next layer instead of losing
# everything after it over one unrelated failure.
HARD_DEPENDENCY_LAYER_COUNT = 4

# The full pool of live callables a generator's run() might ask for, by
# parameter name. inspect.signature picks out only the ones each one
# actually declares, so every generator's differing run() signature (some
# take nexudus_delete or nexudus_run_command, most don't) just works.
CALLABLE_POOL = {
    "nexudus_list": client.nexudus_list,
    "nexudus_get": client.nexudus_get,
    "nexudus_create": client.nexudus_create,
    "nexudus_update": client.nexudus_update,
    "nexudus_delete": client.nexudus_delete,
    "nexudus_run_command": client.nexudus_run_command,
    "nexudus_raise_invoice": client.nexudus_raise_invoice,
}

# Generic dry-run stand-ins for the whole chain — used by run_up_to(dry_run=True)
# so a multi-layer dry run gets a correctly-sized prev_output threaded between
# layers (unlike each generator's own standalone --dry-run mock, which hardcodes
# fake IDs like range(1, 61) regardless of how many records actually exist).
# One known gap: nexudus_list always returns [] here, so a layer whose own
# mock normally synthesizes richer data to exercise itself (e.g. 06_financial.py
# fabricates sample invoices to drive its pay/void/credit/refund selection)
# will just see empty pools through this path and skip that logic — run that
# generator's own `--dry-run` standalone for a fuller dry-run of it specifically.
_dry_run_create_counter = {"n": 0}


def _dry_run_create(entity, body):
    _dry_run_create_counter["n"] += 1
    return {"Id": f"DRY-{entity}-{_dry_run_create_counter['n']}"}


DRY_RUN_POOL = {
    "nexudus_list": lambda entity, filters=None: [],
    "nexudus_get": lambda entity, id: {"Id": id},
    "nexudus_create": _dry_run_create,
    "nexudus_update": lambda entity, id, body: {"Id": id},
    "nexudus_delete": lambda entity, id: None,
    "nexudus_run_command": lambda entity, key, ids, parameters=None: {"Status": "DRY-RUN"},
    "nexudus_raise_invoice": lambda business_id, coworker_id, options=None: {
        "Id": f"DRY-invoice-{coworker_id}", "TotalAmount": 0, "CoworkerId": coworker_id,
    },
}

MOCK_WHOAMI = {
    "DefaultBusinessId": "DRY-BIZ-1",
    "DefaultCurrencyId": "DRY-CUR-1",
    "DefaultCountryId": "DRY-COUNTRY-1",
    "DefaultSimpleTimeZoneId": "DRY-TZ-1",
    "AdminUserId": "DRY-ADMIN-1",
}

# This run's target-vs-actual reconciliation (see report_lib.merge_entity_
# counts/run_reconciliation_lines), set at the end of run_up_to(). Exposed
# at module level rather than changing run_up_to()'s return value (every
# generator's own __main__ already depends on that being prev_output) —
# a caller that cares (e.g. wizard.py, for its exit code) reads this right
# after calling run_up_to().
LAST_RUN_ENTITY_COUNTS = {}

# Independent-tier layers (see HARD_DEPENDENCY_LAYER_COUNT) that failed
# entirely and were skipped this run, as human-readable strings. Set at the
# end of run_up_to(), same convention as LAST_RUN_ENTITY_COUNTS. A layer
# that dies before creating anything leaves no trace in entity_counts (there's
# nothing to compare a target against), so this is the only signal for that
# case — entity_counts' own shortfall check alone isn't sufficient.
LAST_RUN_LAYER_FAILURES = []


def list_businesses():
    """Every business (location) this logged-in account can access."""
    return client.nexudus_list("businesses", {})


def _select_business(businesses, business_id=None):
    """Pick which business (location) to seed into.

    Never prompts — this module's callers are mostly non-interactive (every
    generator's own __main__, pipeline.py's own CLI, tests). The interactive
    picker lives in wizard.py, which resolves a business_id up front (via
    list_businesses()) and passes it down through here. If a login has
    access to more than one business and none was specified, fail loudly
    with the options rather than silently guessing which one to use.
    """
    if not businesses:
        raise SystemExit("No business found on this Nexudus account.")

    if business_id is not None:
        match = next((b for b in businesses if b["Id"] == business_id), None)
        if match is None:
            available = ", ".join(f"{b['Id']} ({b.get('Name', '?')})" for b in businesses)
            raise SystemExit(
                f"Business id {business_id} isn't one this account can access. "
                f"Available: {available}"
            )
        return match

    if len(businesses) == 1:
        return businesses[0]

    listing = "\n".join(f"  {b['Id']}: {b.get('Name', '?')}" for b in businesses)
    raise SystemExit(
        f"This login has access to {len(businesses)} businesses — pick one:\n"
        f"{listing}\n"
        f"Pass --business-id <id> (or use wizard.py, which prompts you for this)."
    )


def _whoami(business_id=None):
    """Layer 0's bootstrap input — there's no prev_output before it."""
    biz = _select_business(list_businesses(), business_id)
    print(f"Using business: {biz.get('Name', '?')} (id={biz['Id']})")

    users = client.nexudus_list("users", {})
    admin = next((u for u in users if u.get("IsAdmin")), None)
    if admin is None:
        raise SystemExit("No admin user found on this Nexudus account.")

    return {
        "DefaultBusinessId": biz["Id"],
        "DefaultCurrencyId": biz.get("CurrencyId"),
        "DefaultCountryId": biz.get("CountryId"),
        "DefaultSimpleTimeZoneId": biz.get("SimpleTimeZoneId"),
        "AdminUserId": admin["Id"],
    }


class SkipLayerError(ValueError):
    """A skip_layers set that names a layer the pipeline can't actually skip."""


def _validate_skip_layers(skip_layers, layer_index):
    """Normalise skip_layers to a set and reject anything unskippable.

    Only the independent tier (>= HARD_DEPENDENCY_LAYER_COUNT) can be skipped:
    those layers were checked to not read a prev_output key any other one of
    them adds, which is the same property that makes a *failure* there
    survivable (see HARD_DEPENDENCY_LAYER_COUNT). Layers 0-3 are a hard chain —
    skipping one leaves every later layer working from a genuinely broken
    foundation (skip people, and activity has no coworker_ids at all), so it's
    rejected loudly here rather than producing a confusing cascade of failures
    several layers later.
    """
    skip = {int(n) for n in (skip_layers or ())}
    hard = sorted(n for n in skip if n < HARD_DEPENDENCY_LAYER_COUNT)
    if hard:
        raise SkipLayerError(
            f"Layers 0-{HARD_DEPENDENCY_LAYER_COUNT - 1} are a hard dependency chain and "
            f"cannot be skipped (got {hard}). Every later layer reads their IDs — "
            f"use the layer ceiling to stop early instead.")
    out_of_range = sorted(n for n in skip if not 0 <= n < len(LAYERS))
    if out_of_range:
        raise SkipLayerError(
            f"No such layer(s): {out_of_range}. Valid layers are 0-{len(LAYERS) - 1}.")
    return {n for n in skip if n <= layer_index}


def run_up_to(layer_index, dry_run=False, business_id=None, write_csvs=True,
              skip_layers=None):
    """Run layers 0..layer_index (inclusive), return the final prev_output.

    skip_layers omits individual layers from that range — only ones at or past
    HARD_DEPENDENCY_LAYER_COUNT; anything earlier raises SkipLayerError (see
    _validate_skip_layers). A skipped layer never constructs its generator, so
    it contributes nothing to the totals or the reconciliation, and
    prev_output passes through it untouched — exactly as if it had been
    caught failing.

    business_id picks which business (location) to seed into, for accounts
    with access to more than one — see _select_business(). Ignored in
    dry-run mode (MOCK_WHOAMI is used instead, nothing live is queried).

    write_csvs controls the output/ folder (per-entity CSVs + a copy of the
    run report) — on by default for every non-wizard caller (standalone
    generators, `python3 pipeline.py`, scripts/seed_all.sh), but wizard.py
    exposes it as an interactive/CLI choice since not everyone running the
    guided flow wants it. Always skipped for a dry run regardless — there's
    nothing real to export.

    Prints each layer's created/skipped/failed summary (see
    generators/base.py::BaseGenerator.summary_line) as it finishes, and the
    cross-layer total when the whole call returns.

    A layer at or past HARD_DEPENDENCY_LAYER_COUNT (activity, community,
    financial, CRM/proposals) that raises is caught, logged clearly, and
    recorded into LAST_RUN_LAYER_FAILURES — the run proceeds to the next
    layer rather than losing everything after it (see
    HARD_DEPENDENCY_LAYER_COUNT's comment for why that's safe). A layer
    before it (reference, structural, people, contracts) that raises still
    propagates out of this function and halts the run immediately, exactly
    as before — via try/finally, so its partial counts are still reported
    before the exception continues propagating.
    """
    global LAST_RUN_ENTITY_COUNTS, LAST_RUN_LAYER_FAILURES
    skip = _validate_skip_layers(skip_layers, layer_index)
    prev_output = None
    totals = {"created": 0, "skipped": 0, "failed": 0}
    reconciliation = {}
    layer_failures = []
    pool = DRY_RUN_POOL if dry_run else CALLABLE_POOL

    try:
        for i, (module_name, class_name) in enumerate(LAYERS[:layer_index + 1]):
            if i in skip:
                print(f"\n--- Layer {i}: {class_name} — skipped by request ---")
                continue
            module = importlib.import_module(module_name)
            gen = getattr(module, class_name)(dry_run=dry_run)

            print(f"\n--- Layer {i}: {class_name} ---")

            sig = inspect.signature(gen.run)
            kwargs = {name: fn for name, fn in pool.items() if name in sig.parameters}

            # Each generator names its context argument differently
            # (whoami_data / layer0_output / prev_output) — it's always the one
            # remaining parameter that isn't a known callable.
            context_param = next(p for p in sig.parameters if p not in CALLABLE_POOL)
            if i == 0:
                kwargs[context_param] = MOCK_WHOAMI if dry_run else _whoami(business_id)
            else:
                kwargs[context_param] = prev_output

            try:
                prev_output = gen.run(**kwargs)
            except Exception as e:
                if i < HARD_DEPENDENCY_LAYER_COUNT:
                    raise
                print(f"\n!!! Layer {i} ({class_name}) failed entirely and was skipped — "
                      f"nothing in a later layer reads its output, so the run continues: {e}")
                layer_failures.append(f"Layer {i} ({class_name}): {e}")
                # prev_output stays whatever the last successful layer
                # returned — this failed layer never reassigns it, so the
                # next layer gets the same input it would have gotten
                # anyway (see HARD_DEPENDENCY_LAYER_COUNT).
            finally:
                print(gen.summary_line())
                for key in totals:
                    totals[key] += gen.counts[key]
                report_lib.merge_entity_counts(reconciliation, gen.entity_counts)
                if not dry_run and write_csvs:
                    # Once this layer's entities are done, their CSVs are
                    # complete — write them now rather than waiting for the
                    # whole run to finish. Re-derives the full picture from
                    # all created-ids files every time (cheap at this record
                    # volume), so it's also correct standalone (a single
                    # `python3 generators/03_contracts.py` run still gets
                    # every entity's CSV refreshed, not just its own).
                    report_lib.write_entity_csvs(config.OUTPUT_DIR)
    finally:
        print(f"\nTotal — Created: {totals['created']}  Skipped: {totals['skipped']}  Failed: {totals['failed']}")
        if layer_failures:
            print(f"Layer failures ({len(layer_failures)}) — see above for full errors:")
            for lf in layer_failures:
                print(f"  - {lf}")
        LAST_RUN_ENTITY_COUNTS = reconciliation
        LAST_RUN_LAYER_FAILURES = layer_failures
        if not dry_run:
            # This run's target-vs-actual, then the cumulative "what's
            # actually in the account now" — a dry run has no real records
            # to report on, so both are skipped there.
            print("\n=== This run: target vs. actual ===")
            print("\n".join(report_lib.run_reconciliation_lines(reconciliation)))
            print("\n=== What's in the account now (cumulative, all runs) ===")
            print("\n".join(report_lib.report_lines()))
            report_lib.write_report(report_lib.REPORT_PATH, reconciliation_entity_counts=reconciliation,
                                     layer_failures=layer_failures)
            if write_csvs:
                config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
                report_lib.write_report(config.OUTPUT_DIR / "run-report.txt",
                                         reconciliation_entity_counts=reconciliation,
                                         layer_failures=layer_failures)
                print(f"\n(saved to {report_lib.REPORT_PATH} and {config.OUTPUT_DIR})")
            else:
                print(f"\n(saved to {report_lib.REPORT_PATH})")

    return prev_output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the seed pipeline through a given layer")
    parser.add_argument("layer", type=int, nargs="?", default=len(LAYERS) - 1,
                         help=f"Run layers 0 through this one (default: {len(LAYERS) - 1}, all layers)")
    parser.add_argument("--business-id", type=int, default=None,
                         help="Which business/location to seed into, if this login has access to more than one")
    parser.add_argument("--dry-run", action="store_true",
                         help="Preview the whole chain without creating anything (see run_up_to)")
    parser.add_argument("--skip-layer", type=int, action="append", default=None,
                         dest="skip_layers", metavar="N",
                         help=f"Omit layer N from the run; repeatable. Only layers "
                              f"{HARD_DEPENDENCY_LAYER_COUNT}-{len(LAYERS) - 1} can be skipped — "
                              f"0-{HARD_DEPENDENCY_LAYER_COUNT - 1} are a hard dependency chain")
    args = parser.parse_args()

    try:
        run_up_to(args.layer, dry_run=args.dry_run, business_id=args.business_id,
                  skip_layers=args.skip_layers)
    except SkipLayerError as e:
        parser.error(str(e))
    print("\nDone.")

    # Exit non-zero when the run wasn't fully successful, so a caller (the web
    # control panel, CI, a script) can tell — mirrors wizard.py::run_live.
    # An independent-tier layer (4-7) failing is *caught* by run_up_to so the
    # run continues; without this the process would still exit 0 and look clean.
    if not args.dry_run:
        if LAST_RUN_LAYER_FAILURES:
            print(f"Note: {len(LAST_RUN_LAYER_FAILURES)} layer(s) failed entirely and were "
                  f"skipped — see above, or {report_lib.REPORT_PATH.name}.")
            sys.exit(1)
        if report_lib.has_shortfall(LAST_RUN_ENTITY_COUNTS):
            print(f"Note: one or more entities fell short of this run's target — see the "
                  f"reconciliation above, or {report_lib.REPORT_PATH.name}.")
            sys.exit(1)
