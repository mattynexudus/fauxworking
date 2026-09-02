"""
Tests for report_lib.real_targets() / target_for() — the fix for targets
silently drifting from config.VOLUMES' static guess (see real_targets()'s
own docstring for the live mismatches that motivated this: extraservices,
products, floorplandesks, communitymessages, and the six join-table/side-
effect entities that had no target at all before).

Uses fake generator classes rather than the real generators/0N_*.py modules
(same approach as test_pipeline_layer_resilience.py) so these tests assert
real_targets()'s own aggregation/caching/fallback behavior, not whatever the
real generators' hand-authored data happens to contain today.
"""

import sys
import types
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import report_lib


def _bucket(target, created=0, skipped=0, failed=0):
    return {"target": target, "created": created, "skipped": skipped,
            "failed": failed, "failure_reasons": Counter()}


def _fake_gen(entity_counts, raises=None):
    """A (module, class) pair report_lib.LAYERS can point at: a class whose
    __init__ either populates entity_counts (mirroring a real generator's
    set_target calls) or raises (mirroring a missing data/*.json plan)."""
    def __init__(self, dry_run=False):
        if raises:
            raise raises
        self.entity_counts = entity_counts
    cls = type("FakeGen", (), {"__init__": __init__})
    return types.SimpleNamespace(FakeGen=cls), "FakeGen"


class TestRealTargets(unittest.TestCase):
    def setUp(self):
        self._orig_layers = report_lib.LAYERS
        self._orig_cache = report_lib._real_targets_cache
        self.addCleanup(self._restore)
        # Cache keyed on a fingerprint every test controls directly, so
        # these never touch the real data/ directory.
        self._fp_patch = patch.object(report_lib, "_data_dir_fingerprint", return_value=1)
        self._fp_patch.start()
        self.addCleanup(self._fp_patch.stop)
        report_lib._real_targets_cache = {"fingerprint": None, "value": {}}

    def _restore(self):
        report_lib.LAYERS = self._orig_layers
        report_lib._real_targets_cache = self._orig_cache

    def _set_layers(self, *modules_and_classes):
        modules = {}
        layers = []
        for i, (module, class_name) in enumerate(modules_and_classes):
            name = f"fake_module_{i}"
            modules[name] = module
            layers.append((name, class_name))
        report_lib.LAYERS = layers
        return patch("report_lib.importlib.import_module", side_effect=lambda m: modules[m])

    def test_overrides_a_stale_volumes_guess(self):
        # "products" has a real VOLUMES entry (15) that a generator's own
        # hand-authored list can disagree with (12) — real_targets() should
        # win, not the static guess.
        mod, cls = _fake_gen({"products": _bucket(12)})
        with self._set_layers((mod, cls)):
            self.assertEqual(report_lib.real_targets(), {"products": 12})
            self.assertEqual(report_lib.target_for("products"), 12)

    def test_sums_contributions_from_more_than_one_generator(self):
        # floorplandesks: the desk catalog (one generator) plus per-contract
        # occupancy assignment (another) both legitimately add to one total.
        mod0, cls0 = _fake_gen({"floorplandesks": _bucket(40)})
        mod1, cls1 = _fake_gen({"floorplandesks": _bucket(28)})
        with self._set_layers((mod0, cls0), (mod1, cls1)):
            self.assertEqual(report_lib.real_targets(), {"floorplandesks": 68})

    def test_a_generator_that_fails_to_construct_is_skipped_not_fatal(self):
        # Mirrors a generator whose data/*.json plan hasn't been prebuilt
        # yet (_load_data raises FileNotFoundError) — real_targets() should
        # still return the other generators' entities rather than raising.
        mod0, cls0 = _fake_gen({"products": _bucket(12)})
        mod1, cls1 = _fake_gen({}, raises=FileNotFoundError("no plan yet"))
        with self._set_layers((mod0, cls0), (mod1, cls1)):
            self.assertEqual(report_lib.real_targets(), {"products": 12})

    def test_falls_back_to_volumes_when_no_generator_covers_the_entity(self):
        # taxrates has a real VOLUMES entry and nothing in LAYERS sets a
        # target for it in this test — target_for() should still answer
        # from VOLUMES rather than returning None.
        with self._set_layers(_fake_gen({})):
            self.assertIsNone(report_lib.real_targets().get("taxrates"))
            self.assertEqual(report_lib.target_for("taxrates"),
                              report_lib.VOLUMES.get("tax_rates"))

    def test_unknown_entity_with_no_target_anywhere_is_none(self):
        with self._set_layers(_fake_gen({})):
            self.assertIsNone(report_lib.target_for("not_a_real_entity"))

    def test_coworkerinvoices_excluded_even_though_a_generator_sets_it(self):
        # Rule 49: invoices are discovered live, not created to a number —
        # real_targets() should never surface one for this entity even
        # though FinancialGenerator does call set_target() for it.
        mod, cls = _fake_gen({"coworkerinvoices": _bucket(220)})
        with self._set_layers((mod, cls)):
            self.assertNotIn("coworkerinvoices", report_lib.real_targets())
            self.assertIsNone(report_lib.target_for("coworkerinvoices"))

    def test_cache_is_reused_until_the_data_dir_fingerprint_changes(self):
        calls = []

        def counting_init(self, dry_run=False):
            calls.append(1)
            self.entity_counts = {"products": _bucket(12)}
        cls = type("FakeGen", (), {"__init__": counting_init})
        module = types.SimpleNamespace(FakeGen=cls)
        report_lib.LAYERS = [("fake_module", "FakeGen")]

        with patch("report_lib.importlib.import_module", return_value=module), \
             patch.object(report_lib, "_data_dir_fingerprint", side_effect=[1, 1, 2]):
            report_lib.real_targets()
            report_lib.real_targets()  # same fingerprint — cached, no re-instantiation
            self.assertEqual(len(calls), 1)
            report_lib.real_targets()  # fingerprint changed — recomputes
            self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
