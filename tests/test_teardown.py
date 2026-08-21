"""
Tests for teardown.py's resilience against partial/incomplete/malformed
tracked data: the malformed-record pooling guard, per-entity batch
isolation, the pure teardown_summary_lines() formatter, survivor
persistence surviving a later entity's failure, and the pre-flight
"last generation run looked incomplete" notice.

No live API calls: nexudus_delete/nexudus_run_command/nexudus_list are
always fakes. Uses tempfile.TemporaryDirectory() + monkeypatched
teardown.CREATED_IDS_DIR, matching tests/test_base_generator.py's own
convention for isolating from the real data/created-ids/ directory.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import report_lib
import teardown


def _write_tracked_file(directory, filename, records):
    path = Path(directory) / filename
    path.write_text(json.dumps(records))
    return path


class _TeardownTestBase(unittest.TestCase):
    """Isolates both data/created-ids/ (via CREATED_IDS_DIR) and the
    pre-flight incomplete-run check (via REPORT_PATH, pointed at a
    scratch path that doesn't exist — _last_generation_run_incomplete()
    correctly returns False for a missing file) from this project's real
    files, so these tests are deterministic regardless of what the real
    last-run-report.txt currently says. TestPreflightIncompleteRunNotice
    below exercises that check's actual content-matching directly."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_dir = teardown.CREATED_IDS_DIR
        teardown.CREATED_IDS_DIR = Path(self._tmpdir.name)
        self._report_patcher = patch.object(
            report_lib, "REPORT_PATH", Path(self._tmpdir.name) / "no-report-here.txt")
        self._report_patcher.start()
        self.addCleanup(self._restore)

    def _restore(self):
        self._report_patcher.stop()
        teardown.CREATED_IDS_DIR = self._orig_dir
        self._tmpdir.cleanup()


class TestMalformedRecordPooling(_TeardownTestBase):
    def test_record_missing_entity_key_is_skipped_not_crashed(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [{"Id": 1}])
        result = teardown.run_teardown(nexudus_delete=lambda *a: None, dry_run=True)
        self.assertEqual(result["malformed"], 1)
        self.assertEqual(result["entity_outcomes"], {})

    def test_record_with_explicit_none_entity_is_skipped(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [{"Id": 1, "entity": None}])
        result = teardown.run_teardown(nexudus_delete=lambda *a: None, dry_run=True)
        self.assertEqual(result["malformed"], 1)

    def test_non_dict_record_is_skipped_not_crashed(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", ["not-a-record", 42])
        result = teardown.run_teardown(nexudus_delete=lambda *a: None, dry_run=True)
        self.assertEqual(result["malformed"], 2)

    def test_malformed_record_alongside_well_formed_one_still_processes_the_good_one(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [
            {"Id": 1},  # malformed — no entity tag
            {"Id": 2, "entity": "taxrates"},
        ])
        result = teardown.run_teardown(nexudus_delete=lambda *a: None, dry_run=True)
        self.assertEqual(result["malformed"], 1)
        self.assertIn("taxrates", result["entity_outcomes"])
        self.assertEqual(result["entity_outcomes"]["taxrates"]["seen"], 1)

    def test_warning_text_printed_for_malformed_records(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [{"Id": 1}])
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            teardown.run_teardown(nexudus_delete=lambda *a: None, dry_run=True)
        self.assertIn("missing an 'entity' tag", buf.getvalue())


class TestTypeErrorRegressionCrashVector(_TeardownTestBase):
    def test_malformed_record_plus_unlisted_entity_does_not_crash(self):
        # Reproduces the exact confirmed crash: a malformed (no-entity)
        # record pooled alongside a second, genuinely-unlisted entity name
        # used to make `sorted(set(by_entity) - set(ENTITY_DELETE_ORDER))`
        # raise TypeError comparing None to str. A single malformed record
        # alone wouldn't trigger it (a one-element {None} set needs no
        # comparison) — this needs both conditions at once.
        unlisted = "zzz_test_unlisted_entity"
        self.assertNotIn(unlisted, teardown.ENTITY_DELETE_ORDER,
                          "test fixture assumption broken — pick a different fake entity name")
        _write_tracked_file(self._tmpdir.name, "gen.json", [
            {"Id": 1},  # malformed
            {"Id": 2, "entity": unlisted},
        ])
        try:
            result = teardown.run_teardown(nexudus_delete=lambda *a: None, dry_run=True)
        except TypeError:
            self.fail("run_teardown raised TypeError on malformed + unlisted-entity records")
        self.assertEqual(result["malformed"], 1)
        self.assertIn(unlisted, result["entity_outcomes"])


class TestEntityLevelIsolation(_TeardownTestBase):
    def test_one_entity_batch_failing_does_not_halt_the_run(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [
            {"Id": 1, "entity": "taxrates"},
            {"Id": 2, "entity": "products"},
        ])

        def fake_batch(entity, items, dry_run, nexudus_delete, nexudus_run_command,
                        nexudus_list, bucket, deleted_keys):
            if entity == "taxrates":
                raise RuntimeError("boom")
            bucket["deleted"] += len(items)

        with patch.object(teardown, "_delete_entity_batch", side_effect=fake_batch):
            result = teardown.run_teardown(
                nexudus_delete=lambda *a: None, dry_run=False,
                nexudus_run_command=lambda *a, **k: None, nexudus_list=lambda *a, **k: [],
            )

        self.assertEqual(result["entity_outcomes"]["taxrates"]["entity_aborted"], "boom")
        # products is a different entity in the same run and still ran,
        # regardless of which side of taxrates it falls on in
        # ENTITY_DELETE_ORDER — the point is one entity's abort doesn't
        # affect any other entity's processing.
        self.assertEqual(result["entity_outcomes"]["products"]["deleted"], 1)
        self.assertIsNone(result["entity_outcomes"]["products"]["entity_aborted"])

    def test_partial_mutation_before_a_batch_failure_is_preserved(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [{"Id": 1, "entity": "taxrates"}])

        def fake_batch(entity, items, dry_run, nexudus_delete, nexudus_run_command,
                        nexudus_list, bucket, deleted_keys):
            path, i, _record = items[0]
            deleted_keys.add((path, i))
            bucket["deleted"] += 1
            raise RuntimeError("boom after partial progress")

        with patch.object(teardown, "_delete_entity_batch", side_effect=fake_batch):
            result = teardown.run_teardown(
                nexudus_delete=lambda *a: None, dry_run=False,
                nexudus_run_command=lambda *a, **k: None, nexudus_list=lambda *a, **k: [],
            )

        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["entity_outcomes"]["taxrates"]["entity_aborted"],
                          "boom after partial progress")
        # And it was actually persisted despite the abort.
        survivors = json.loads((Path(self._tmpdir.name) / "gen.json").read_text())
        self.assertEqual(survivors, [])


class TestSummaryLinesFormatting(unittest.TestCase):
    def test_empty_outcomes_returns_empty_list(self):
        self.assertEqual(teardown.teardown_summary_lines({}), [])

    def test_failure_reasons_line_only_when_failures_exist(self):
        outcomes = {
            "bookings": teardown._entity_bucket({}, "bookings"),
        }
        outcomes["bookings"]["failure_reasons"]["http_400"] = 2
        lines = teardown.teardown_summary_lines(outcomes)
        self.assertTrue(any("reasons: http_400=2" in line for line in lines))

    def test_no_reasons_line_when_clean(self):
        outcomes = {"bookings": teardown._entity_bucket({}, "bookings")}
        outcomes["bookings"]["deleted"] = 5
        lines = teardown.teardown_summary_lines(outcomes)
        self.assertFalse(any("reasons:" in line for line in lines))

    def test_aborted_entity_gets_its_own_lines_and_closing_summary(self):
        outcomes = {"bookings": teardown._entity_bucket({}, "bookings")}
        outcomes["bookings"]["entity_aborted"] = "boom"
        lines = teardown.teardown_summary_lines(outcomes)
        self.assertTrue(any("BATCH ABORTED: boom" in line for line in lines))
        self.assertTrue(any("aborted partway through (1): bookings" in line for line in lines))


class TestSurvivorPersistence(_TeardownTestBase):
    def test_dry_run_never_rewrites_tracked_files(self):
        path = _write_tracked_file(self._tmpdir.name, "gen.json", [{"Id": 1, "entity": "taxrates"}])
        before = path.read_text()
        teardown.run_teardown(nexudus_delete=lambda *a: None, dry_run=True)
        self.assertEqual(path.read_text(), before)

    def test_no_rewrite_when_nothing_was_deleted(self):
        path = _write_tracked_file(self._tmpdir.name, "gen.json", [{"Id": 1, "entity": "taxrates"}])
        before = path.read_text()

        def always_fail(entity, record_id):
            raise RuntimeError("nope")

        teardown.run_teardown(nexudus_delete=always_fail, dry_run=False)
        self.assertEqual(path.read_text(), before)

    def test_successful_entity_persists_even_though_a_later_entity_aborts(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [
            {"Id": 1, "entity": "taxrates"},
            {"Id": 2, "entity": "products"},
        ])
        real_batch = teardown._delete_entity_batch

        def fake_batch(entity, items, dry_run, nexudus_delete, nexudus_run_command,
                        nexudus_list, bucket, deleted_keys):
            if entity == "products":
                raise RuntimeError("boom")
            return real_batch(entity, items, dry_run, nexudus_delete, nexudus_run_command,
                               nexudus_list, bucket, deleted_keys)

        with patch.object(teardown, "_delete_entity_batch", side_effect=fake_batch):
            teardown.run_teardown(
                nexudus_delete=lambda *a: None, dry_run=False,
                nexudus_run_command=lambda *a, **k: None, nexudus_list=lambda *a, **k: [],
            )

        survivors = json.loads((Path(self._tmpdir.name) / "gen.json").read_text())
        surviving_entities = {r["entity"] for r in survivors}
        self.assertNotIn("taxrates", surviving_entities)
        self.assertIn("products", surviving_entities)


class TestPreflightIncompleteRunNotice(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def _patched(self, report_path):
        return patch.object(report_lib, "REPORT_PATH", report_path)

    def test_no_report_file_returns_false(self):
        missing = Path(self._tmpdir.name) / "does-not-exist.txt"
        with self._patched(missing):
            self.assertFalse(teardown._last_generation_run_incomplete())

    def test_layer_failure_marker_returns_true(self):
        path = Path(self._tmpdir.name) / "report.txt"
        path.write_text("=== Layers that failed entirely this run (1) ===\n")
        with self._patched(path):
            self.assertTrue(teardown._last_generation_run_incomplete())

    def test_short_marker_returns_true(self):
        path = Path(self._tmpdir.name) / "report.txt"
        path.write_text("bookings   10   5   0   0  <-- short\n")
        with self._patched(path):
            self.assertTrue(teardown._last_generation_run_incomplete())

    def test_below_target_marker_alone_does_not_trigger(self):
        # "<-- below target" is report_lines()'s CUMULATIVE, lifetime flag
        # (report_lib.py::report_lines) — it can be true for reasons
        # unrelated to whether the *last run* completed (a volume config
        # raised since, a multi-day seeding plan not finished by design),
        # so it's deliberately excluded from _INCOMPLETE_RUN_MARKERS. Only
        # "<-- short" (this run's own reconciliation) and an outright
        # layer failure are run-specific enough to warn about.
        path = Path(self._tmpdir.name) / "report.txt"
        path.write_text("products   15   12  <-- below target\n")
        with self._patched(path):
            self.assertFalse(teardown._last_generation_run_incomplete())

    def test_clean_report_returns_false(self):
        path = Path(self._tmpdir.name) / "report.txt"
        path.write_text("Every entity's target was fully accounted for this run.\n")
        with self._patched(path):
            self.assertFalse(teardown._last_generation_run_incomplete())

    def test_unreadable_path_returns_false_not_raises(self):
        # Point REPORT_PATH at a directory instead of a file, so
        # .read_text() raises a real IsADirectoryError (an OSError
        # subclass) rather than a contrived mock.
        with self._patched(Path(self._tmpdir.name)):
            self.assertFalse(teardown._last_generation_run_incomplete())


if __name__ == "__main__":
    unittest.main()
