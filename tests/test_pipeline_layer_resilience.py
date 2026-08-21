"""
Tests for pipeline.run_up_to()'s layer-failure tiering (Workstream 3): a
hard-tier layer (index < HARD_DEPENDENCY_LAYER_COUNT) that raises still
halts the whole run, exactly as before; an independent-tier layer that
raises is caught, recorded into LAST_RUN_LAYER_FAILURES, and the run
proceeds to the next layer instead.

Builds fake, minimal generator classes rather than importing the real
generators/0N_*.py modules — this is testing run_up_to()'s own loop
structure, not any particular generator's behavior (already covered
elsewhere). No live API calls: CALLABLE_POOL's real client functions are
never invoked because the fake run() signatures don't declare any
parameter named after one, so run_up_to()'s own kwargs-filtering leaves
them all unused. report_lib.REPORT_PATH is redirected to a scratch file so
a live test run never overwrites the project's real last-run-report.txt.
"""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipeline
import report_lib


class _FakeGen:
    """Minimal stand-in for a BaseGenerator subclass — just enough surface
    for run_up_to()'s loop: a run(prev_output) that either succeeds or
    raises, plus the summary/counts/entity_counts it always reads in
    `finally` regardless of outcome."""

    should_fail = False

    def __init__(self, dry_run=False):
        self.counts = {"created": 1, "skipped": 0, "failed": 0}
        self.entity_counts = {}

    def run(self, prev_output):
        if self.should_fail:
            raise RuntimeError(f"{self.__class__.__name__} boom")
        return {**(prev_output or {}), self.__class__.__name__: True}

    def summary_line(self):
        return f"[{self.__class__.__name__}] ok"


def _make_gen_class(name, fail):
    return type(name, (_FakeGen,), {"should_fail": fail})


class TestLayerFailureIsolation(unittest.TestCase):
    def setUp(self):
        self._orig_layers = pipeline.LAYERS
        self._orig_hard_count = pipeline.HARD_DEPENDENCY_LAYER_COUNT
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.addCleanup(self._restore)

    def _restore(self):
        pipeline.LAYERS = self._orig_layers
        pipeline.HARD_DEPENDENCY_LAYER_COUNT = self._orig_hard_count

    def _run_with_layers(self, layer_specs, hard_count):
        """layer_specs: list of (name, should_fail) pairs, run in order as
        fake layers 0..N. Returns (prev_output_or_None, exception_or_None)."""
        layers = []
        modules = {}
        for name, fail in layer_specs:
            module_name = f"fake_module_{name}"
            cls = _make_gen_class(name, fail)
            modules[module_name] = types.SimpleNamespace(**{name: cls})
            layers.append((module_name, name))

        pipeline.LAYERS = layers
        pipeline.HARD_DEPENDENCY_LAYER_COUNT = hard_count
        scratch_report = Path(self._tmpdir.name) / "last-run-report.txt"

        with patch("pipeline.importlib.import_module", side_effect=lambda m: modules[m]), \
             patch("pipeline._whoami", return_value={}), \
             patch.object(report_lib, "REPORT_PATH", scratch_report):
            try:
                result = pipeline.run_up_to(len(layers) - 1, dry_run=False, write_csvs=False)
                return result, None
            except Exception as e:  # noqa: BLE001
                return None, e

    def test_independent_tier_failure_does_not_halt_run(self):
        # hard_count=1: only layer 0 (LayerA) is hard-tier; LayerB (index 1)
        # and LayerC (index 2) are independent-tier.
        result, exc = self._run_with_layers(
            [("LayerA", False), ("LayerB", True), ("LayerC", False)],
            hard_count=1,
        )
        self.assertIsNone(exc)
        self.assertEqual(len(pipeline.LAST_RUN_LAYER_FAILURES), 1)
        self.assertIn("LayerB", pipeline.LAST_RUN_LAYER_FAILURES[0])
        self.assertIn("boom", pipeline.LAST_RUN_LAYER_FAILURES[0])
        # LayerC still ran, and still saw LayerA's earlier contribution —
        # LayerB's own key is simply absent since it never returned.
        self.assertTrue(result["LayerA"])
        self.assertTrue(result["LayerC"])
        self.assertNotIn("LayerB", result)

    def test_hard_tier_failure_halts_run(self):
        result, exc = self._run_with_layers(
            [("LayerA", True), ("LayerB", False)],
            hard_count=2,
        )
        self.assertIsNotNone(exc)
        self.assertIn("LayerA boom", str(exc))
        # Never reached LayerB, and the failure list stays specific to
        # what actually happened — a hard-tier halt is a raised exception,
        # not an entry in the independent-tier failure list.
        self.assertEqual(pipeline.LAST_RUN_LAYER_FAILURES, [])

    def test_happy_path_no_failures(self):
        result, exc = self._run_with_layers(
            [("LayerA", False), ("LayerB", False)],
            hard_count=1,
        )
        self.assertIsNone(exc)
        self.assertEqual(pipeline.LAST_RUN_LAYER_FAILURES, [])
        self.assertTrue(result["LayerA"])
        self.assertTrue(result["LayerB"])

    def test_multiple_independent_failures_all_recorded(self):
        result, exc = self._run_with_layers(
            [("LayerA", False), ("LayerB", True), ("LayerC", True)],
            hard_count=1,
        )
        self.assertIsNone(exc)
        self.assertEqual(len(pipeline.LAST_RUN_LAYER_FAILURES), 2)


if __name__ == "__main__":
    unittest.main()
