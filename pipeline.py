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

    python pipeline.py           # run every layer, 0 through 7
    python pipeline.py 3         # run layers 0-3 (contracts and everything
                                  # it depends on), return that prev_output

Each individual generator's `__main__` live branch calls `run_up_to(N)` for
its own layer index — that's the whole live-mode implementation for all of
them. `seed_all.sh` just calls this file with no argument.
"""

import importlib
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import nexudus_client as client

# (module filename without .py, class name, entity_name from the class)
LAYERS = [
    ("generators.00_reference", "ReferenceGenerator"),
    ("generators.01_structural", "StructuralGenerator"),
    ("generators.02_people", "PeopleGenerator"),
    ("generators.03_contracts", "ContractsGenerator"),
    ("generators.04_activity", "ActivityGenerator"),
    ("generators.05_community", "CommunityGenerator"),
    ("generators.06_financial", "FinancialGenerator"),
    ("generators.07_crm_proposals", "CrmProposalsGenerator"),
]

# The full pool of live callables a generator's run() might ask for, by
# parameter name. inspect.signature picks out only the ones each one
# actually declares, so every generator's differing run() signature (some
# take nexudus_delete or nexudus_run_command, most don't) just works.
CALLABLE_POOL = {
    "nexudus_list": client.nexudus_list,
    "nexudus_create": client.nexudus_create,
    "nexudus_update": client.nexudus_update,
    "nexudus_delete": client.nexudus_delete,
    "nexudus_run_command": client.nexudus_run_command,
}


def _whoami():
    """Layer 0's bootstrap input — there's no prev_output before it."""
    businesses = client.nexudus_list("businesses", {})
    if not businesses:
        raise SystemExit("No business found on this Nexudus account.")
    biz = businesses[0]

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


def run_up_to(layer_index):
    """Run layers 0..layer_index (inclusive) live, return the final prev_output."""
    prev_output = None

    for i, (module_name, class_name) in enumerate(LAYERS[:layer_index + 1]):
        module = importlib.import_module(module_name)
        gen = getattr(module, class_name)(dry_run=False)

        print(f"\n--- Layer {i}: {class_name} ---")

        sig = inspect.signature(gen.run)
        kwargs = {name: fn for name, fn in CALLABLE_POOL.items() if name in sig.parameters}

        # Each generator names its context argument differently
        # (whoami_data / layer0_output / prev_output) — it's always the one
        # remaining parameter that isn't a known callable.
        context_param = next(p for p in sig.parameters if p not in CALLABLE_POOL)
        kwargs[context_param] = _whoami() if i == 0 else prev_output

        prev_output = gen.run(**kwargs)

    return prev_output


if __name__ == "__main__":
    target = int(sys.argv[1]) if len(sys.argv) > 1 else len(LAYERS) - 1
    run_up_to(target)
    print("\nDone.")
