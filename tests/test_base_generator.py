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


if __name__ == "__main__":
    unittest.main()
