"""
Tests for generators/base.py's run-summary counting: track_id/already_created/
count_skip/count_create/summary_line, and the skip=True warning convention
used to distinguish a terminal "record not created" warning from a purely
diagnostic one (see generators/base.py::_CountingLogger).

No live API calls — pure logic against a temp created-ids directory.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators import base


class _Probe(base.BaseGenerator):
    entity_name = "probe"


class TestBaseGeneratorCounting(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_dir = base.CREATED_IDS_DIR
        base.CREATED_IDS_DIR = Path(self._tmpdir.name)

    def tearDown(self):
        base.CREATED_IDS_DIR = self._orig_dir
        self._tmpdir.cleanup()

    def test_track_id_counts_created(self):
        gen = _Probe()
        gen.track_id({"entity": "probe", "Id": 1})
        gen.track_id({"entity": "probe", "Id": 2})
        self.assertEqual(gen.counts["created"], 2)

    def test_already_created_counts_skipped_only_when_found(self):
        gen = _Probe()
        gen.track_id({"entity": "probe", "Id": 1, "ProbeIndex": "1"})
        self.assertFalse(gen.already_created("ProbeIndex", "2"))
        self.assertEqual(gen.counts["skipped"], 0)
        self.assertTrue(gen.already_created("ProbeIndex", "1"))
        self.assertEqual(gen.counts["skipped"], 1)

    def test_log_would_create_counts_created(self):
        gen = _Probe(dry_run=True)
        gen.log_would_create("probe", {"Name": "x"})
        self.assertEqual(gen.counts["created"], 1)

    def test_count_skip_and_count_create_helpers(self):
        gen = _Probe()
        gen.count_skip()
        gen.count_skip(2)
        gen.count_create(3)
        self.assertEqual(gen.counts["skipped"], 3)
        self.assertEqual(gen.counts["created"], 3)

    def test_warning_skip_true_counts_failed(self):
        gen = _Probe()
        gen.log.warning("terminal failure", skip=True)
        self.assertEqual(gen.counts["failed"], 1)

    def test_warning_without_skip_does_not_count(self):
        gen = _Probe()
        gen.log.warning("just a diagnostic note")
        self.assertEqual(gen.counts["failed"], 0)

    def test_summary_line_format(self):
        gen = _Probe()
        gen.track_id({"entity": "probe", "Id": 1})
        gen.count_skip()
        gen.log.warning("oops", skip=True)
        self.assertEqual(gen.summary_line(), "[probe] Created: 1  Skipped: 1  Failed: 1")

    def test_tracked_ids_persist_across_instances(self):
        gen1 = _Probe()
        gen1.track_id({"entity": "probe", "Id": 1, "ProbeIndex": "1"})
        gen2 = _Probe()
        self.assertTrue(gen2.already_created("ProbeIndex", "1"))


class TestEntityCounts(unittest.TestCase):
    """Per-entity target/created/skipped/failed tracking — the QA-facing
    reconciliation data (see generators/base.py::BaseGenerator.entity_counts)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_dir = base.CREATED_IDS_DIR
        base.CREATED_IDS_DIR = Path(self._tmpdir.name)

    def tearDown(self):
        base.CREATED_IDS_DIR = self._orig_dir
        self._tmpdir.cleanup()

    def test_set_target_registers_planned_count(self):
        gen = _Probe()
        gen.set_target("widgets", 10)
        self.assertEqual(gen.entity_counts["widgets"]["target"], 10)
        self.assertEqual(gen.entity_counts["widgets"]["created"], 0)

    def test_track_id_increments_entity_bucket_from_entity_field(self):
        gen = _Probe()
        gen.set_target("widgets", 10)
        gen.track_id({"entity": "widgets", "Id": 1})
        gen.track_id({"entity": "widgets", "Id": 2})
        gen.track_id({"entity": "gadgets", "Id": 3})
        self.assertEqual(gen.entity_counts["widgets"]["created"], 2)
        self.assertEqual(gen.entity_counts["gadgets"]["created"], 1)

    def test_already_created_with_entity_increments_that_entitys_skipped(self):
        gen = _Probe()
        gen.track_id({"entity": "widgets", "Id": 1, "WidgetIndex": "1"})
        gen.already_created("WidgetIndex", "1", entity="widgets")
        self.assertEqual(gen.entity_counts["widgets"]["skipped"], 1)

    def test_already_created_without_entity_stays_backward_compatible(self):
        # Existing call sites that don't pass entity= shouldn't crash or
        # attribute to any entity bucket — this is the un-migrated,
        # pre-Workstream-1 call pattern still used throughout most of the
        # codebase.
        gen = _Probe()
        gen.track_id({"entity": "widgets", "Id": 1, "WidgetIndex": "1"})
        self.assertTrue(gen.already_created("WidgetIndex", "1"))
        self.assertEqual(gen.counts["skipped"], 1)
        self.assertEqual(gen.entity_counts, {"widgets": {
            "target": 0, "created": 1, "skipped": 0, "failed": 0, "failure_reasons": base.Counter(),
        }})

    def test_log_would_create_increments_entity_bucket(self):
        gen = _Probe(dry_run=True)
        gen.log_would_create("widgets", {"Name": "x"})
        self.assertEqual(gen.entity_counts["widgets"]["created"], 1)

    def test_warning_skip_with_entity_and_reason_populates_failure_reasons(self):
        gen = _Probe()
        gen.log.warning("no valid pass", skip=True, entity="widgets", reason="validation_rejected")
        self.assertEqual(gen.entity_counts["widgets"]["failed"], 1)
        self.assertEqual(gen.entity_counts["widgets"]["failure_reasons"]["validation_rejected"], 1)

    def test_warning_skip_with_entity_but_no_reason_defaults_to_unknown(self):
        gen = _Probe()
        gen.log.warning("mystery failure", skip=True, entity="widgets")
        self.assertEqual(gen.entity_counts["widgets"]["failure_reasons"]["unknown_error"], 1)

    def test_warning_skip_without_entity_stays_backward_compatible(self):
        gen = _Probe()
        gen.log.warning("terminal failure", skip=True)
        self.assertEqual(gen.counts["failed"], 1)
        self.assertEqual(gen.entity_counts, {})


class TestClassifyFailure(unittest.TestCase):
    """The systemic-vs-one-off resilience classifier (Workstream 1b)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_dir = base.CREATED_IDS_DIR
        base.CREATED_IDS_DIR = Path(self._tmpdir.name)

    def tearDown(self):
        base.CREATED_IDS_DIR = self._orig_dir
        self._tmpdir.cleanup()

    def test_first_occurrence_is_skip(self):
        gen = _Probe()
        self.assertEqual(gen.classify_failure("widgets", ValueError("boom")), "skip")

    def test_same_error_repeating_below_threshold_stays_skip(self):
        gen = _Probe()
        gen.classify_failure("widgets", ValueError("boom"))
        self.assertEqual(gen.classify_failure("widgets", ValueError("boom")), "skip")

    def test_same_error_repeating_at_threshold_becomes_systemic(self):
        gen = _Probe()
        gen.classify_failure("widgets", ValueError("boom"))
        gen.classify_failure("widgets", ValueError("boom"))
        self.assertEqual(gen.classify_failure("widgets", ValueError("boom")), "systemic")

    def test_custom_repeat_threshold(self):
        gen = _Probe()
        self.assertEqual(gen.classify_failure("widgets", ValueError("boom"), repeat_threshold=1), "systemic")

    def test_different_error_text_resets_the_streak(self):
        gen = _Probe()
        gen.classify_failure("widgets", ValueError("boom"))
        gen.classify_failure("widgets", ValueError("boom"))
        # A different error text interrupts the streak — not systemic yet
        self.assertEqual(gen.classify_failure("widgets", ValueError("different problem")), "skip")
        self.assertEqual(gen.classify_failure("widgets", ValueError("different problem")), "skip")
        self.assertEqual(gen.classify_failure("widgets", ValueError("different problem")), "systemic")

    def test_streaks_are_scoped_per_entity(self):
        gen = _Probe()
        gen.classify_failure("widgets", ValueError("boom"))
        gen.classify_failure("widgets", ValueError("boom"))
        # A different entity's failures don't contribute to widgets' streak
        self.assertEqual(gen.classify_failure("gadgets", ValueError("boom")), "skip")
        self.assertEqual(gen.classify_failure("widgets", ValueError("boom")), "systemic")


if __name__ == "__main__":
    unittest.main()
