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
        self._patchers = [
            patch.object(report_lib, "REPORT_PATH",
                         Path(self._tmpdir.name) / "no-report-here.txt"),
            # In a subdir so it stays clear of the tmpdir-root "*.json" globs
            # a couple of these tests use to assert no survivor file was written.
            patch.object(report_lib, "TEARDOWN_REPORT_PATH",
                         Path(self._tmpdir.name) / "reports" / "last-teardown-report.txt"),
        ]
        for p in self._patchers:
            p.start()
        self.addCleanup(self._restore)

    def _restore(self):
        for p in self._patchers:
            p.stop()
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


class TestUntrackedInvoiceDiscovery(_TeardownTestBase):
    """_discover_untracked_coworker_invoices — the pre-flight sync that
    catches invoices Nexudus's recurring billing generated for a known
    seeded coworker after 06_financial.py's own discovery step last ran,
    which financial.json would otherwise have no record of at all."""

    def test_untracked_invoice_for_known_coworker_is_discovered_and_deleted(self):
        _write_tracked_file(self._tmpdir.name, "financial.json", [
            {"Id": 100, "entity": "coworkerinvoices", "CoworkerId": 42},
        ])
        live_invoices = [
            {"Id": 100, "CoworkerId": 42},  # already tracked
            {"Id": 101, "CoworkerId": 42},  # untracked, known coworker — should be picked up
        ]
        commands_run = []

        result = teardown.run_teardown(
            nexudus_delete=lambda *a: None, dry_run=False,
            nexudus_run_command=lambda entity, cmd, ids, **k: commands_run.append((entity, cmd, ids)),
            nexudus_list=lambda entity, filters: live_invoices if entity == "coworkerinvoices" else [],
        )

        self.assertEqual(result["entity_outcomes"]["coworkerinvoices"]["seen"], 2)
        self.assertEqual(result["entity_outcomes"]["coworkerinvoices"]["deleted"], 2)
        self.assertIn(("coworkerinvoices", "COWORKER_INVOICE_DELETE", [101]), commands_run)

    def test_invoice_for_unknown_coworker_is_ignored(self):
        _write_tracked_file(self._tmpdir.name, "financial.json", [
            {"Id": 100, "entity": "coworkerinvoices", "CoworkerId": 42},
        ])
        live_invoices = [
            {"Id": 100, "CoworkerId": 42},
            {"Id": 999, "CoworkerId": 1123713612},  # a real, unrelated customer — never seen before
        ]

        result = teardown.run_teardown(
            nexudus_delete=lambda *a: None, dry_run=False,
            nexudus_run_command=lambda *a, **k: None,
            nexudus_list=lambda entity, filters: live_invoices if entity == "coworkerinvoices" else [],
        )

        self.assertEqual(result["entity_outcomes"]["coworkerinvoices"]["seen"], 1)

    def test_dry_run_never_calls_nexudus_list_for_discovery(self):
        _write_tracked_file(self._tmpdir.name, "financial.json", [
            {"Id": 100, "entity": "coworkerinvoices", "CoworkerId": 42},
        ])

        def boom(*a, **k):
            raise AssertionError("nexudus_list must not be called during a dry run")

        teardown.run_teardown(nexudus_delete=None, dry_run=True, nexudus_list=boom)

    def test_discovered_invoice_persists_to_financial_json_when_deleted(self):
        path = _write_tracked_file(self._tmpdir.name, "financial.json", [])
        live_invoices = [{"Id": 101, "CoworkerId": 42}]
        # A sibling tracked file supplies the "known coworker" signal —
        # discovery isn't limited to coworkers already tracked via invoices.
        _write_tracked_file(self._tmpdir.name, "contracts.json", [
            {"Id": 5, "entity": "coworkercontracts", "CoworkerId": 42},
        ])

        teardown.run_teardown(
            nexudus_delete=lambda *a: None, dry_run=False,
            nexudus_run_command=lambda *a, **k: None,
            nexudus_list=lambda entity, filters: live_invoices if entity == "coworkerinvoices" else [],
        )

        survivors = json.loads(path.read_text())
        self.assertEqual(survivors, [])  # discovered, then deleted, then swept from tracking


class TestLiveDiscoverAll(unittest.TestCase):
    """_live_discover_all — mode='clean's live-listing replacement for
    tracked-file loading. No CREATED_IDS_DIR isolation needed here since
    this function never touches the filesystem."""

    def test_skips_no_list_support_entities(self):
        calls = []

        def fake_list(entity, filters):
            calls.append(entity)
            return []

        teardown._live_discover_all(fake_list)
        self.assertNotIn("coworkerinvoicehistories", calls)

    def test_uses_per_coworker_filter_for_coworkerextraservices(self):
        def fake_list(entity, filters):
            if entity == "coworkers":
                return [{"Id": 1}, {"Id": 2}]
            if entity == "coworkerextraservices":
                cid = filters.get("CoworkerExtraService_Coworker")
                return [{"Id": 100 + cid}]
            return []

        by_entity, pooled = teardown._live_discover_all(fake_list)
        ids = sorted(r["Id"] for _, _, r in by_entity["coworkerextraservices"])
        self.assertEqual(ids, [101, 102])

    def test_dedupes_records_seen_across_multiple_coworkers(self):
        # A shared extra service could come back for more than one
        # coworker filter — must not be double-counted.
        def fake_list(entity, filters):
            if entity == "coworkers":
                return [{"Id": 1}, {"Id": 2}]
            if entity == "coworkerextraservices":
                return [{"Id": 999}]  # same record, every coworker
            return []

        by_entity, pooled = teardown._live_discover_all(fake_list)
        self.assertEqual(len(by_entity["coworkerextraservices"]), 1)

    def test_records_are_tagged_with_synthetic_entity_and_no_path(self):
        def fake_list(entity, filters):
            return [{"Id": 1}] if entity == "taxrates" else []

        by_entity, pooled = teardown._live_discover_all(fake_list)
        path, i, record = by_entity["taxrates"][0]
        self.assertIsNone(path)
        self.assertEqual(record["entity"], "taxrates")
        self.assertEqual(record["Id"], 1)


class TestCleanModeEndToEnd(_TeardownTestBase):
    """mode='clean' wired through run_teardown — no tracked files
    involved at all, deletes whatever nexudus_list finds live."""

    def test_deletes_live_records_with_no_tracking_file_present(self):
        # Deliberately no _write_tracked_file call — clean mode must not
        # need one.
        def fake_list(entity, filters):
            if entity == "taxrates":
                return [{"Id": 7}]
            return []

        deleted = []
        result = teardown.run_teardown(
            nexudus_delete=lambda entity, rid: deleted.append((entity, rid)),
            dry_run=False, nexudus_run_command=lambda *a, **k: None,
            nexudus_list=fake_list, mode="clean",
        )

        self.assertEqual(deleted, [("taxrates", 7)])
        self.assertEqual(result["deleted"], 1)

    def test_mode_clean_without_nexudus_list_raises(self):
        with self.assertRaises(ValueError):
            teardown.run_teardown(nexudus_delete=lambda *a: None, dry_run=False, mode="clean")

    def test_no_survivor_file_written_in_clean_mode(self):
        # Nothing to persist to — there's no backing tracked file for a
        # live-discovered record, so this must not raise trying to write one.
        def fake_list(entity, filters):
            return [{"Id": 1}] if entity == "taxrates" else []

        teardown.run_teardown(
            nexudus_delete=lambda *a: None, dry_run=False,
            nexudus_run_command=lambda *a, **k: None, nexudus_list=fake_list, mode="clean",
        )
        self.assertEqual(list(Path(self._tmpdir.name).glob("*.json")), [])


class _FakeApiError(RuntimeError):
    """Mimics nexudus_client.NexudusApiError closely enough for these
    tests: a message string _delete_entity_batch's exception handling
    checks with `in`, and no .response attribute (so status resolves to
    None, matching a body-level rejection rather than a distinct HTTP
    status)."""


class TestPricePlanLockedSelfHeal(_TeardownTestBase):
    """A CoworkerTimePass/CoworkerExtraService granted by a price plan
    rejects DELETE outright — no delete path exists at all for these (see
    PRICE_PLAN_LOCKED_USE_COMMAND). USE_TIME_PASS/USE_EXTRA_SERVICE are
    the best-available terminal action and must be tracked as their own
    outcome, never silently reported as "deleted"."""

    def test_price_plan_locked_timepass_is_marked_used_not_deleted(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [
            {"Id": 1, "entity": "coworkertimepasses"},
        ])
        commands_run = []

        def fake_delete(entity, rid):
            raise _FakeApiError(
                "This time pass is from a price plan and it cannot be deleted. "
                "You can mark it as used instead.")

        result = teardown.run_teardown(
            nexudus_delete=fake_delete, dry_run=False,
            nexudus_run_command=lambda entity, cmd, ids, **k: commands_run.append((entity, cmd, ids)),
            nexudus_list=lambda *a, **k: [],
        )

        self.assertEqual(result["entity_outcomes"]["coworkertimepasses"]["marked_used"], 1)
        self.assertEqual(result["entity_outcomes"]["coworkertimepasses"]["deleted"], 0)
        self.assertIn(("coworkertimepasses", "USE_TIME_PASS", [1]), commands_run)
        # Resolved either way — nothing left to usefully retry — so it's
        # swept from tracking same as a real delete.
        survivors = json.loads((Path(self._tmpdir.name) / "gen.json").read_text())
        self.assertEqual(survivors, [])

    def test_price_plan_locked_extraservice_uses_its_own_command_and_parameters(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [
            {"Id": 2, "entity": "coworkerextraservices"},
        ])
        commands_run = []

        def fake_delete(entity, rid):
            raise _FakeApiError(
                "This extra service is from a price plan and it cannot be deleted. "
                "You can use it instead.")

        result = teardown.run_teardown(
            nexudus_delete=fake_delete, dry_run=False,
            nexudus_run_command=lambda entity, cmd, ids, **k: commands_run.append((entity, cmd, ids)),
            nexudus_list=lambda *a, **k: [],
        )

        self.assertEqual(result["entity_outcomes"]["coworkerextraservices"]["marked_used"], 1)
        self.assertEqual(commands_run, [("coworkerextraservices", "USE_EXTRA_SERVICE", [2])])

    def test_mark_used_failure_is_counted_as_failed_not_deleted(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [
            {"Id": 1, "entity": "coworkertimepasses"},
        ])

        def fake_delete(entity, rid):
            raise _FakeApiError("This time pass is from a price plan and it cannot be deleted.")

        def fake_command(entity, cmd, ids, **k):
            raise RuntimeError("USE_TIME_PASS also failed")

        result = teardown.run_teardown(
            nexudus_delete=fake_delete, dry_run=False,
            nexudus_run_command=fake_command, nexudus_list=lambda *a, **k: [],
        )

        self.assertEqual(result["entity_outcomes"]["coworkertimepasses"]["marked_used"], 0)
        self.assertEqual(result["entity_outcomes"]["coworkertimepasses"]["failed"], 1)


class TestRevertChargeSelfHeal(_TeardownTestBase):
    """A CoworkerExtraService created via CHARGE_BOOKING rejects a direct
    delete ("Revert the charges of that booking instead") — the fix is
    UNCHARGE_BOOKING on the originating booking, then retry the delete."""

    def test_reverts_booking_charge_then_deletes(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [
            {"Id": 5, "entity": "coworkerextraservices", "BookingId": 999},
        ])
        commands_run = []
        deleted = []
        delete_attempts = []

        def fake_delete(entity, rid):
            delete_attempts.append((entity, rid))
            if len(delete_attempts) == 1:
                raise _FakeApiError(
                    "This charge came from a booking. Revert the charges of that booking "
                    "instead of deleting this charge directly.")
            deleted.append((entity, rid))

        result = teardown.run_teardown(
            nexudus_delete=fake_delete, dry_run=False,
            nexudus_run_command=lambda entity, cmd, ids, **k: commands_run.append((entity, cmd, ids)),
            nexudus_list=lambda *a, **k: [],
        )

        self.assertIn(("bookings", "UNCHARGE_BOOKING", [999]), commands_run)
        self.assertEqual(deleted, [("coworkerextraservices", 5)])
        self.assertEqual(result["entity_outcomes"]["coworkerextraservices"]["deleted"], 1)

    def test_without_booking_id_falls_through_to_ordinary_failure(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [
            {"Id": 5, "entity": "coworkerextraservices"},  # no BookingId
        ])

        def fake_delete(entity, rid):
            raise _FakeApiError("Revert the charges of that booking instead of deleting directly.")

        result = teardown.run_teardown(
            nexudus_delete=fake_delete, dry_run=False,
            nexudus_run_command=lambda *a, **k: None, nexudus_list=lambda *a, **k: [],
        )

        self.assertEqual(result["entity_outcomes"]["coworkerextraservices"]["failed"], 1)
        self.assertEqual(result["entity_outcomes"]["coworkerextraservices"]["deleted"], 0)


class TestBookingUnchargeSelfHeal(unittest.TestCase):
    """_delete_one's own bookings self-heal — the direct-DELETE-failure
    path (already-charged), distinct from the coworkerextraservices side
    tested above."""

    def test_already_charged_booking_reverts_then_retries_cancel_and_delete(self):
        commands_run = []
        delete_calls = []

        def fake_delete(entity, rid):
            delete_calls.append((entity, rid))
            if len(delete_calls) == 1:
                raise _FakeApiError("This booking has already been charged to this customer.")

        def fake_run_command(entity, cmd, ids, **k):
            commands_run.append((entity, cmd, ids))

        teardown._delete_one("bookings", 42, fake_delete, fake_run_command, nexudus_list=lambda *a, **k: [])

        self.assertIn(("bookings", "UNCHARGE_BOOKING", [42]), commands_run)
        # CANCEL_BOOKING then DELETE both ran again after the revert.
        self.assertIn(("bookings", "CANCEL_BOOKING", [42]), commands_run)
        self.assertEqual(delete_calls, [("bookings", 42), ("bookings", 42)])

    def test_unrelated_failure_still_raises(self):
        def fake_delete(entity, rid):
            raise _FakeApiError("some other unrelated rejection")

        with self.assertRaises(_FakeApiError):
            teardown._delete_one("bookings", 1, fake_delete, lambda *a, **k: None,
                                  nexudus_list=lambda *a, **k: [])


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


class TestPostTeardownCleanups(unittest.TestCase):
    """The optional post-teardown offers — deleting data/*.json plan files
    and output/*.csv exports — driven non-interactively by assume_yes so the
    web control panel can run them without a stdin prompt (mirrors how the
    --clear-generated-data / --clear-csv-outputs flags feed in)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)
        self.data = self.root / "data"
        self.output = self.root / "output"
        self.data.mkdir()
        self.output.mkdir()
        for p in (patch.object(teardown, "DATA_DIR", self.data),
                  patch.object(teardown, "OUTPUT_DIR", self.output)):
            p.start()
            self.addCleanup(p.stop)

    def test_assume_yes_deletes_generated_data_without_prompting(self):
        (self.data / "coworkers.json").write_text("[]")
        (self.data / "plan-manifest.json").write_text("{}")
        teardown.maybe_clear_generated_data(assume_yes=True)
        self.assertEqual(list(self.data.glob("*.json")), [])

    def test_assume_yes_deletes_csv_outputs_without_prompting(self):
        (self.output / "coworkers.csv").write_text("Id\n1\n")
        (self.output / "visitors.csv").write_text("Id\n")
        teardown.maybe_clear_csv_outputs(assume_yes=True)
        self.assertEqual(list(self.output.glob("*.csv")), [])

    def test_no_csv_files_is_a_quiet_no_op(self):
        # nothing to delete, and (crucially) no input() call to hang a
        # non-interactive run
        teardown.maybe_clear_csv_outputs(assume_yes=False)

    def test_default_path_still_prompts(self):
        (self.output / "coworkers.csv").write_text("Id\n1\n")
        with patch("builtins.input", return_value="n") as inp:
            teardown.maybe_clear_csv_outputs(assume_yes=False)
        inp.assert_called_once()
        self.assertTrue((self.output / "coworkers.csv").exists())


class TestTeardownReport(_TeardownTestBase):
    """write_teardown_report — the last-teardown-report.txt / .json pair a
    live run leaves behind, mirroring pipeline.run_up_to's last-run-report.txt
    for the web control panel."""

    def _json(self):
        return json.loads(report_lib.TEARDOWN_REPORT_PATH.with_suffix(".json")
                          .read_text(encoding="utf-8"))

    def test_live_run_writes_txt_and_json(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [
            {"Id": 1, "entity": "taxrates"},
            {"Id": 2, "entity": "taxrates"},
        ])
        teardown.run_teardown(nexudus_delete=lambda *a: None, dry_run=False,
                               nexudus_run_command=lambda *a, **k: None,
                               nexudus_list=lambda *a, **k: [])

        self.assertTrue(report_lib.TEARDOWN_REPORT_PATH.exists())
        payload = self._json()
        self.assertEqual(payload["mode"], "tracked")
        self.assertEqual(payload["totals"]["deleted"], 2)
        self.assertEqual(payload["entities"]["taxrates"]["deleted"], 2)
        self.assertIn("Teardown report generated",
                      report_lib.TEARDOWN_REPORT_PATH.read_text(encoding="utf-8"))

    def test_dry_run_writes_no_report(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [{"Id": 1, "entity": "taxrates"}])
        teardown.run_teardown(nexudus_delete=lambda *a: None, dry_run=True)
        self.assertFalse(report_lib.TEARDOWN_REPORT_PATH.exists())
        self.assertFalse(report_lib.TEARDOWN_REPORT_PATH.with_suffix(".json").exists())

    def test_failure_reasons_and_mode_are_recorded(self):
        _write_tracked_file(self._tmpdir.name, "gen.json", [{"Id": 1, "entity": "taxrates"}])

        def always_fail(entity, record_id):
            raise RuntimeError("nope")

        teardown.run_teardown(nexudus_delete=always_fail, dry_run=False,
                               nexudus_run_command=lambda *a, **k: None,
                               nexudus_list=lambda *a, **k: [])
        payload = self._json()
        self.assertEqual(payload["totals"]["failed"], 1)
        self.assertEqual(payload["totals"]["deleted"], 0)
        self.assertTrue(payload["entities"]["taxrates"]["failure_reasons"])

    def test_nothing_to_tear_down_still_writes_a_zeroed_report(self):
        # No tracked files at all — the early-return path must still leave a
        # fresh report so the panel doesn't keep showing a stale teardown.
        teardown.run_teardown(nexudus_delete=lambda *a: None, dry_run=False)
        payload = self._json()
        self.assertEqual(payload["totals"]["deleted"], 0)
        self.assertEqual(payload["entities"], {})

    def test_clean_mode_is_labelled_in_the_report(self):
        teardown.run_teardown(
            nexudus_delete=lambda *a: None, dry_run=False,
            nexudus_run_command=lambda *a, **k: None,
            nexudus_list=lambda entity, filters: [{"Id": 5}] if entity == "taxrates" else [],
            mode="clean",
        )
        self.assertEqual(self._json()["mode"], "clean")


if __name__ == "__main__":
    unittest.main()
