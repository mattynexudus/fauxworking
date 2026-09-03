"""
Tests for the browser control panel (webui/).

No network, no real subprocess: `subprocess.Popen` is replaced with `FakePopen`
(a canned line iterator that can block until released, so the one-run-at-a-time
lock is testable), and every Nexudus-touching call in `webui.auth` is patched
or simply never reached. `report_lib`/`config` paths are redirected to temp
dirs the same way `tests/test_teardown.py` does.
"""

import contextlib
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
import report_lib
from webui import registry, report
from webui.jobs import JobManager, RunInProgress
from webui.registry import BadRequest, build_argv


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------
class FakePopen:
    """Stand-in for subprocess.Popen. stdout is self; readline() yields the
    canned lines then either EOF (default) or blocks until finish()/terminate()
    when block=True."""

    def __init__(self, lines=None, exit_code=0, block=False):
        self._lines = list(lines or [])
        self._exit_code = exit_code
        self._idx = 0
        self._terminated = False
        self._release = threading.Event()
        if not block:
            self._release.set()
        self.returncode = None
        self.stdout = self

    def readline(self):
        if self._terminated:
            return ""
        if self._idx < len(self._lines):
            line = self._lines[self._idx]
            self._idx += 1
            return line if line.endswith("\n") else line + "\n"
        self._release.wait()
        return ""

    def wait(self, timeout=None):
        self.returncode = -15 if self._terminated else self._exit_code
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self._terminated = True
        self._release.set()

    def kill(self):
        self.terminate()

    # test controls
    def finish(self, exit_code=None):
        if exit_code is not None:
            self._exit_code = exit_code
        self._release.set()


class PopenSpy:
    """Records the argv of every call, hands back a FakePopen."""

    def __init__(self, factory=None):
        self.calls = []
        self._factory = factory or (lambda: FakePopen([], exit_code=0))

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        return self._factory()


def _wait(job, timeout=3):
    assert job._done.wait(timeout), "job did not finish in time"


# --------------------------------------------------------------------------
# build_argv
# --------------------------------------------------------------------------
class TestCommandRegistry(unittest.TestCase):
    def test_pipeline_plain(self):
        self.assertEqual(build_argv("pipeline")[-1], "pipeline.py")

    def test_pipeline_positional_then_flags(self):
        argv = build_argv("pipeline", {"layer": 3}, business_id=222)
        self.assertEqual(argv[-3:], ["3", "--business-id", "222"])

    def test_pipeline_dry_run(self):
        self.assertIn("--dry-run", build_argv("pipeline", {}, dry_run=True))

    def test_pipeline_layer_is_a_descriptive_choice(self):
        argv = build_argv("pipeline", {"layer": 3}, dry_run=True)
        self.assertEqual(argv[argv.index("pipeline.py") + 1], "3")
        self.assertIn("--dry-run", argv)
        # blank / omitted => all layers, no positional
        self.assertEqual(build_argv("pipeline")[-1], "pipeline.py")

    def test_daily_update_now_takes_business_id(self):
        # inverted from the panel's first cut: daily_update.py gained a
        # --business-id flag so the panel's global location selector is honest.
        argv = build_argv("daily_update", {"days": 7, "date": "2026-08-01"}, business_id=9)
        self.assertEqual(argv[argv.index("--date") + 1], "2026-08-01")
        self.assertEqual(argv[argv.index("--days") + 1], "7")
        self.assertEqual(argv[argv.index("--business-id") + 1], "9")

    def test_daily_update_omits_business_id_when_none(self):
        argv = build_argv("daily_update", {"days": 1})
        self.assertNotIn("--business-id", argv)

    def test_teardown_one_card_dry_and_live(self):
        # dry-run + tracked: no confirmation phrase needed at the argv layer
        self.assertEqual(
            build_argv("teardown", {"mode": "tracked"}, dry_run=True)[-3:],
            ["--mode", "tracked", "--dry-run"])
        # dry-run + clean: allowed (preview)
        self.assertEqual(
            build_argv("teardown", {"mode": "clean"}, dry_run=True)[-3:],
            ["--mode", "clean", "--dry-run"])
        # live + tracked: fine
        self.assertEqual(
            build_argv("teardown", {"mode": "tracked"}, dry_run=False)[-2:],
            ["--mode", "tracked"])
        # live + clean: now buildable — teardown.py's own typed prompt is
        # bypassed with --yes (the panel/server gate it behind a phrase).
        self.assertEqual(
            build_argv("teardown", {"mode": "clean"}, dry_run=False)[-3:],
            ["--mode", "clean", "--yes"])

    def test_teardown_cleanup_flags_only_on_a_real_run(self):
        live = build_argv("teardown",
                          {"mode": "tracked", "clear_data": True, "clear_csv": True,
                           "reset_counters": True}, dry_run=False)
        self.assertIn("--clear-generated-data", live)
        self.assertIn("--clear-csv-outputs", live)
        self.assertIn("--reset-counters", live)
        # a preview never runs them, so build_argv drops them
        preview = build_argv("teardown",
                             {"mode": "tracked", "clear_data": True, "clear_csv": True,
                              "reset_counters": True}, dry_run=True)
        for flag in ("--clear-generated-data", "--clear-csv-outputs", "--reset-counters"):
            self.assertNotIn(flag, preview)

    def test_confirm_phrase_for_teardown_depends_on_mode(self):
        self.assertEqual(registry.confirm_phrase_for("teardown", {"mode": "tracked"}),
                         "delete tracked records")
        self.assertEqual(registry.confirm_phrase_for("teardown", {"mode": "clean"}),
                         registry.TEARDOWN_CLEAN_CONFIRM_PHRASE)
        self.assertEqual(registry.confirm_phrase_for("teardown", None),
                         "delete tracked records")

    def test_no_command_teardown_defaults_to_tracked_no_yes(self):
        argv = build_argv("teardown")
        self.assertEqual(argv[argv.index("--mode") + 1], "tracked")
        self.assertNotIn("--yes", argv)

    def test_wizard_always_complete_and_non_interactive(self):
        import prebuild
        argv = build_argv("wizard", {"coworkers": 4}, business_id=7)
        self.assertIn("--yes", argv)
        self.assertIn("--live", argv)
        self.assertIn("--layer", argv)
        self.assertTrue("--export-csv" in argv or "--no-export-csv" in argv)
        for key in config.CONFIGURABLE_VOLUME_KEYS:
            self.assertIn(prebuild.FLAG_SPEC[key][0], argv)
        self.assertEqual(argv[argv.index("--coworkers") + 1], "4")
        self.assertEqual(argv[-2:], ["--business-id", "7"])

    def test_wizard_dry_run_uses_dry_not_live(self):
        argv = build_argv("wizard", {}, dry_run=True)
        self.assertIn("--dry-run", argv)
        self.assertNotIn("--live", argv)

    def test_wizard_fresh_flag(self):
        self.assertIn("--fresh", build_argv("wizard", {"fresh": True}))
        self.assertNotIn("--fresh", build_argv("wizard", {}))

    def test_wizard_layer_is_a_choice_with_all_option(self):
        layer = next(p for p in registry.BY_ID["wizard"].params if p.name == "layer")
        self.assertEqual(layer.type, "choice")
        self.assertEqual(layer.choices[0][0], "")  # blank == "All layers"
        # blank resolves to the last layer; a pick is passed straight through
        self.assertEqual(build_argv("wizard", {})[build_argv("wizard", {}).index("--layer") + 1],
                         str(len(__import__("pipeline").LAYERS) - 1))
        argv = build_argv("wizard", {"layer": 3})
        self.assertEqual(argv[argv.index("--layer") + 1], "3")

    # -- skip_layers -------------------------------------------------------
    def _layer_tokens(self, params):
        """Just the --layer / --skip-layer part of a wizard argv."""
        argv = build_argv("wizard", params)
        out = []
        for i, tok in enumerate(argv):
            if tok in ("--layer", "--skip-layer"):
                out += [tok, argv[i + 1]]
        return out

    def test_skip_layer_emits_a_repeated_flag_for_gaps(self):
        self.assertEqual(
            self._layer_tokens({"layer": 7, "skip_layers": [5, 6]}),
            ["--layer", "7", "--skip-layer", "5", "--skip-layer", "6"])

    def test_trailing_skips_collapse_into_the_ceiling(self):
        # Skipping the top layers is the same instruction as a lower ceiling,
        # and says it in fewer tokens — so no --skip-layer is emitted at all.
        self.assertEqual(self._layer_tokens({"layer": 7, "skip_layers": [6, 7]}),
                         ["--layer", "5"])
        # ...and a gap below a collapsed ceiling still gets its explicit flag.
        self.assertEqual(self._layer_tokens({"layer": 7, "skip_layers": [5, 7]}),
                         ["--layer", "6", "--skip-layer", "5"])

    def test_skip_layer_accepts_a_comma_string(self):
        self.assertEqual(self._layer_tokens({"layer": 7, "skip_layers": "5,6"}),
                         self._layer_tokens({"layer": 7, "skip_layers": [5, 6]}))

    def test_skip_layer_rejects_the_hard_dependency_tier(self):
        with self.assertRaises(BadRequest) as ctx:
            build_argv("wizard", {"layer": 7, "skip_layers": [2]})
        self.assertIn("hard dependency chain", str(ctx.exception))

    def test_skip_layer_rejects_nonsense(self):
        for bad in ([99], ["x"], {"a": 1}):
            with self.assertRaises(BadRequest):
                build_argv("wizard", {"layer": 7, "skip_layers": bad})

    def test_skipping_everything_is_rejected(self):
        with self.assertRaises(BadRequest) as ctx:
            build_argv("wizard", {"layer": 3, "skip_layers": [0, 1, 2, 3]})
        # The hard-tier guard fires first — either message is a clean 400.
        self.assertIsInstance(ctx.exception, BadRequest)

    def test_layer_taxonomy_is_exposed_and_covers_every_tracked_entity(self):
        blob = registry.commands_json()
        self.assertEqual(len(blob["layers"]), len(__import__("pipeline").LAYERS))
        self.assertEqual(blob["hard_dependency_layer_count"],
                         __import__("pipeline").HARD_DEPENDENCY_LAYER_COUNT)
        # Every entity that can appear in the Results table has a layer to be
        # grouped under, or it would silently vanish from the entity table.
        missing = set(report_lib.TARGET_KEY_BY_ENTITY) - set(blob["layer_by_entity"])
        self.assertEqual(missing, set(), f"ungrouped entities: {sorted(missing)}")
        for entity, layer in blob["layer_by_entity"].items():
            self.assertIn(layer, range(len(blob["layers"])), entity)

    def test_layer_by_entity_covers_every_entity_a_generator_can_track(self):
        # TARGET_KEY_BY_ENTITY alone missed six real entities (join tables and
        # other targetless side effects — see LAYER_BY_ENTITY's comment) that
        # generators do tag and track, so they're seeded and shown live but
        # silently fell out of the Results table's grouping. Scanning every
        # generator's own entity="..." call sites is the actual superset this
        # map needs to cover, static and independent of what's tracked live
        # in any one account.
        generators_dir = Path(__file__).parent.parent / "generators"
        tagged = set()
        for path in generators_dir.glob("*.py"):
            tagged |= set(re.findall(r'entity="([a-z_]+)"', path.read_text(encoding="utf-8")))
        self.assertTrue(tagged, "no entity= tags found — the scan itself is broken")
        missing = tagged - set(registry.LAYER_BY_ENTITY)
        self.assertEqual(missing, set(), f"tracked but ungrouped: {sorted(missing)}")

    def test_entity_labels_cover_every_grouped_entity(self):
        blob = registry.commands_json()
        labels = blob["entity_labels"]
        self.assertEqual(set(labels), set(blob["layer_by_entity"]))
        # An apiPath is unspaced, so a label that still equals it means the
        # volume-key lookup found nothing and nobody added a fallback.
        unlabelled = [e for e, l in labels.items() if l == e]
        self.assertEqual(unlabelled, [], f"no readable label for: {unlabelled}")
        # The 11 configurable knobs keep their hand-written labels verbatim.
        self.assertEqual(labels["bookings"], registry.VOLUME_LABELS["bookings_total"])
        self.assertEqual(labels["checkins"], registry.VOLUME_LABELS["check_ins"])
        # Derived ones read as words, with the CRM acronym preserved.
        self.assertEqual(labels["financialaccounts"], "Financial accounts")
        self.assertEqual(labels["crmboardcolumns"], "CRM board columns")

    def test_pretty_argv(self):
        raw = build_argv("wizard", {})
        pretty = registry.pretty_argv(raw)
        self.assertEqual(pretty[0], "python")
        self.assertNotIn("-u", pretty)
        self.assertIn("wizard.py", pretty)
        self.assertEqual(raw, build_argv("wizard", {}))  # pretty_argv didn't mutate

    def test_soft_max_present_and_advisory_only(self):
        blob = registry.commands_json()
        wp = {p["name"]: p for p in next(c for c in blob["commands"] if c["id"] == "wizard")["params"]}
        self.assertIsNotNone(wp["bookings_total"]["soft_max"])
        # advisory: a value over soft_max (but no hard max) still builds fine
        build_argv("wizard", {"bookings_total": wp["bookings_total"]["soft_max"] + 5000})
        days = next(p for p in registry.BY_ID["daily_update"].params if p.name == "days")
        self.assertEqual(days.soft_max, 14)
        build_argv("daily_update", {"days": 90})  # no exception

    def test_unknown_command(self):
        with self.assertRaises(BadRequest):
            build_argv("nope")

    def test_bad_int_value(self):
        with self.assertRaises(BadRequest):
            build_argv("pipeline", {"layer": "seven"})

    def test_coworkers_cap_enforced(self):
        # coworkers has max=COWORKER_DAILY_LIMIT; over it is a BadRequest,
        # not something discovered only after the run 401s partway through.
        with self.assertRaises(BadRequest):
            build_argv("wizard", {"coworkers": registry.COWORKER_DAILY_LIMIT + 1})
        build_argv("wizard", {"coworkers": registry.COWORKER_DAILY_LIMIT})  # ok

    def test_layer_out_of_range(self):
        with self.assertRaises(BadRequest):
            build_argv("pipeline", {"layer": 99})


class TestRegistryShape(unittest.TestCase):
    def test_commands_json_shape(self):
        blob = registry.commands_json()
        json.dumps(blob)  # serialisable
        self.assertIn("groups", blob)
        self.assertIn("commands", blob)
        for c in blob["commands"]:
            for field in ("id", "label", "group", "params", "accepts_business_id",
                          "offers_dry_run", "destructive", "tone", "guided_only"):
                self.assertIn(field, c)

    def test_every_command_group_is_a_real_group(self):
        ids = {g["id"] for g in registry.GROUPS}
        for c in registry.REGISTRY:
            self.assertIn(c.group, ids)

    def test_group_order_is_stable(self):
        self.assertEqual([g["id"] for g in registry.GROUPS],
                         ["seed", "maintain", "check", "danger"])

    def test_wizard_is_the_only_guided_only_command(self):
        guided = [c.id for c in registry.REGISTRY if c.guided_only]
        self.assertEqual(guided, ["wizard"])

    def test_pipeline_is_hidden_but_still_runnable(self):
        # not shown as a card (guided flow covers "seed the current plan"),
        # but kept for CLI/API parity.
        self.assertTrue(registry.BY_ID["pipeline"].hidden)
        self.assertIn("pipeline.py", build_argv("pipeline"))
        blob = {c["id"]: c for c in registry.commands_json()["commands"]}
        self.assertTrue(blob["pipeline"]["hidden"])
        self.assertFalse(blob["verify"]["hidden"])

    def test_wizard_has_no_seed_param(self):
        # the control panel dropped the Seed box (--seed stays a CLI-only flag);
        # a stray seed value in the payload must not become a --seed token.
        wizard = registry.BY_ID["wizard"]
        self.assertNotIn("seed", [p.name for p in wizard.params])
        self.assertNotIn("--seed", build_argv("wizard", {"seed": 99}))

    def test_wizard_volume_params_all_have_defaults(self):
        # regression: they used to render as 11 blank boxes. The guided flow
        # drives itself from wizard's param defs, so this is where it matters.
        wizard = registry.BY_ID["wizard"]
        vol = {p.name: p for p in wizard.params if p.name in config.CONFIGURABLE_VOLUME_KEYS}
        self.assertEqual(len(vol), len(config.CONFIGURABLE_VOLUME_KEYS))
        for p in vol.values():
            self.assertIsNotNone(p.default)
        self.assertLessEqual(vol["coworkers"].default, registry.COWORKER_DAILY_LIMIT)

    def test_pipeline_layer_choices_carry_human_descriptions(self):
        layer = next(p for p in registry.BY_ID["pipeline"].params if p.name == "layer")
        labels = " ".join(lbl for _v, lbl in layer.choices)
        self.assertIn("Reference", labels)
        self.assertIn("People", labels)
        self.assertIn("All layers", labels)
        self.assertNotIn("Generator", labels)  # no raw class names

    def test_bounds_survive_commands_json(self):
        blob = registry.commands_json()
        by_id = {c["id"]: c for c in blob["commands"]}
        layer_p = by_id["pipeline"]["params"][0]
        self.assertEqual((layer_p["min"], layer_p["max"]), (0, len(__import__("pipeline").LAYERS) - 1))
        cw = next(p for p in by_id["wizard"]["params"] if p["name"] == "coworkers")
        self.assertEqual(cw["max"], registry.COWORKER_DAILY_LIMIT)


# --------------------------------------------------------------------------
# JobManager
# --------------------------------------------------------------------------
class _MgrCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.logs = Path(self._tmp.name) / "logs"
        self.mgr = JobManager(logs_dir=self.logs)


class TestBusinessInjection(_MgrCase):
    def test_multi_without_business_is_rejected_before_spawn(self):
        spy = PopenSpy()
        with patch("webui.jobs.subprocess.Popen", spy):
            with self.assertRaises(BadRequest):
                self.mgr.start("pipeline", businesses_mode="multi", business_id=None)
        self.assertEqual(spy.calls, [])

    def test_multi_with_business_injects_flag(self):
        spy = PopenSpy()
        with patch("webui.jobs.subprocess.Popen", spy):
            job = self.mgr.start("pipeline", businesses_mode="multi", business_id=222)
            _wait(job)
        self.assertIn("--business-id", spy.calls[0])
        self.assertEqual(spy.calls[0][-1], "222")

    def test_single_without_business_starts(self):
        spy = PopenSpy()
        with patch("webui.jobs.subprocess.Popen", spy):
            job = self.mgr.start("pipeline", businesses_mode="single", business_id=None)
            _wait(job)
        self.assertNotIn("--business-id", spy.calls[0])

    def test_daily_update_now_guarded_on_multi(self):
        # daily_update gained accepts_business_id=True, so the multi guard now
        # applies to it too (was: allowed through, silently hit businesses[0]).
        spy = PopenSpy()
        with patch("webui.jobs.subprocess.Popen", spy):
            with self.assertRaises(BadRequest):
                self.mgr.start("daily_update", businesses_mode="multi", business_id=None)
            job = self.mgr.start("daily_update", businesses_mode="multi", business_id=42)
            _wait(job)
        self.assertEqual(spy.calls[0][-1], "42")


class TestJobLock(_MgrCase):
    def test_second_start_is_rejected_then_allowed_after_finish(self):
        blocker = FakePopen(block=True)
        with patch("webui.jobs.subprocess.Popen", PopenSpy(lambda: blocker)):
            job = self.mgr.start("verify")
            self.assertEqual(job.status, "running")
            self.assertIs(self.mgr.active(), job)
            with self.assertRaises(RunInProgress) as ctx:
                self.mgr.start("verify")
            self.assertIs(ctx.exception.active, job)

            blocker.finish(0)
            _wait(job)
            self.assertEqual(job.status, "succeeded")
            self.assertIsNone(self.mgr.active())

        with patch("webui.jobs.subprocess.Popen", PopenSpy()):
            job2 = self.mgr.start("verify")
            _wait(job2)
            self.assertNotEqual(job.run_id, job2.run_id)

    def test_non_zero_exit_marks_failed_and_releases_lock(self):
        with patch("webui.jobs.subprocess.Popen", PopenSpy(lambda: FakePopen([], exit_code=1))):
            job = self.mgr.start("verify")
            _wait(job)
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.exit_code, 1)
        self.assertIsNone(self.mgr.active())

    def test_cancel_terminates_and_marks_cancelled(self):
        blocker = FakePopen(block=True)
        with patch("webui.jobs.subprocess.Popen", PopenSpy(lambda: blocker)):
            job = self.mgr.start("verify")
            self.mgr.cancel(job.run_id)
            _wait(job)
        self.assertTrue(blocker._terminated)
        self.assertEqual(job.status, "cancelled")

    def test_old_logs_are_pruned(self):
        self.logs.mkdir(parents=True, exist_ok=True)
        for i in range(210):
            (self.logs / f"run-old-{i:04d}.log").write_text("x")
        with patch("webui.jobs._MAX_LOG_FILES", 200), \
             patch("webui.jobs.subprocess.Popen", PopenSpy()):
            job = self.mgr.start("verify")
            _wait(job)
        remaining = list(self.logs.glob("run-*.log"))
        self.assertLessEqual(len(remaining), 201)  # 200 kept + the one just written

    def test_to_dict_has_pretty_argv(self):
        with patch("webui.jobs.subprocess.Popen", PopenSpy()):
            job = self.mgr.start("verify")
            _wait(job)
        self.assertIn("argv_display", job.to_dict())


class TestLogAndSSE(_MgrCase):
    def _run_three_lines(self):
        with patch("webui.jobs.subprocess.Popen",
                   PopenSpy(lambda: FakePopen(["line 1", "line 2", "line 3"], exit_code=0))):
            job = self.mgr.start("verify")
            _wait(job)
        return job

    def test_log_file_written(self):
        job = self._run_three_lines()
        text = job.log_path.read_text()
        self.assertIn("argv=", text.splitlines()[0])
        for expected in ("line 1", "line 2", "line 3", "# exited status=succeeded code=0"):
            self.assertIn(expected, text)

    def test_sse_from_zero(self):
        job = self._run_three_lines()
        frames = list(job.sse_events(0))
        self.assertEqual(len(frames), 4)
        self.assertIn("id: 1", frames[0])
        self.assertIn("event: line", frames[0])
        self.assertIn("data: line 1", frames[0])
        self.assertIn("event: end", frames[-1])
        payload = json.loads(frames[-1].split("data: ", 1)[1].strip())
        self.assertEqual(payload["exit_code"], 0)
        self.assertEqual(payload["status"], "succeeded")

    def test_sse_resume_from_index(self):
        job = self._run_three_lines()
        frames = list(job.sse_events(2))
        self.assertEqual(len(frames), 2)
        self.assertIn("data: line 3", frames[0])
        self.assertIn("event: end", frames[1])

    def test_run_id_shape_and_uniqueness(self):
        job1 = self._run_three_lines()
        job2 = self._run_three_lines()
        rx = re.compile(r"^\d{8}-\d{6}-[a-z_]+(-\d+)?$")
        self.assertRegex(job1.run_id, rx)
        self.assertRegex(job2.run_id, rx)
        self.assertNotEqual(job1.run_id, job2.run_id)

    def test_recent_lists_the_run(self):
        job = self._run_three_lines()
        rows = self.mgr.recent()
        self.assertTrue(any(r["run_id"] == job.run_id for r in rows))


class TestDestructiveGating(_MgrCase):
    def test_teardown_live_needs_exact_phrase(self):
        with patch("webui.jobs.subprocess.Popen", PopenSpy()):
            with self.assertRaises(BadRequest):
                self.mgr.start("teardown", dry_run=False, confirm=None)
            with self.assertRaises(BadRequest):
                self.mgr.start("teardown", dry_run=False, confirm="wrong")
            job = self.mgr.start("teardown", dry_run=False, confirm="delete tracked records")
            _wait(job)
        self.assertEqual(job.command, "teardown")

    def test_teardown_dry_run_needs_no_confirm(self):
        with patch("webui.jobs.subprocess.Popen", PopenSpy()):
            job = self.mgr.start("teardown", dry_run=True, confirm=None)
            _wait(job)
        self.assertEqual(job.status, "succeeded")

    def test_teardown_clean_live_needs_the_stronger_phrase(self):
        with patch("webui.jobs.subprocess.Popen", PopenSpy()) as spy:
            # the tracked phrase is not enough for a clean wipe
            with self.assertRaises(BadRequest):
                self.mgr.start("teardown", params={"mode": "clean"}, dry_run=False,
                               confirm="delete tracked records")
            self.assertEqual(spy.calls, [])
            # the clean phrase runs it, and --yes rides past teardown.py's prompt
            job = self.mgr.start("teardown", params={"mode": "clean"}, dry_run=False,
                                 confirm="delete everything")
            _wait(job)
        self.assertIn("--yes", spy.calls[0])
        self.assertEqual(spy.calls[0][spy.calls[0].index("--mode") + 1], "clean")


# --------------------------------------------------------------------------
# nexudus_auth.is_authenticated
# --------------------------------------------------------------------------
class TestIsAuthenticatedHasNoSideEffects(unittest.TestCase):
    """The control panel polls /api/status (and so is_authenticated) every 3
    seconds from an open browser tab. When this performed a refresh_token
    grant, each poll rotated the token pair in .env underneath any running
    seed, and that run's writes started failing 401 "Access Denied." at
    random — see nexudus_auth.is_authenticated."""

    def _env(self, d, expires_at):
        env = Path(d) / ".env"
        env.write_text(f"NEXUDUS_ACCESS_TOKEN=a\nNEXUDUS_REFRESH_TOKEN=r\n"
                       f"NEXUDUS_TOKEN_EXPIRES_AT={expires_at}\n")
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in nexudus_auth_mod().TOKEN_ENV_KEYS])
        return env

    def test_does_not_contact_the_server_even_when_a_refresh_is_due(self):
        nexudus_auth = nexudus_auth_mod()
        with tempfile.TemporaryDirectory() as d:
            # Expiry in the past — the old implementation would refresh here.
            env = self._env(d, "1")
            with patch.object(nexudus_auth, "ENV_PATH", env), \
                 patch.object(nexudus_auth.requests, "post") as post:
                self.assertTrue(nexudus_auth.is_authenticated())
                post.assert_not_called()

    def test_does_not_rewrite_the_env_file(self):
        nexudus_auth = nexudus_auth_mod()
        with tempfile.TemporaryDirectory() as d:
            env = self._env(d, "1")
            before = env.read_text()
            with patch.object(nexudus_auth, "ENV_PATH", env), \
                 patch.object(nexudus_auth.requests, "post") as post:
                nexudus_auth.is_authenticated()
                post.assert_not_called()
            self.assertEqual(env.read_text(), before)

    def test_false_when_tokens_are_missing(self):
        nexudus_auth = nexudus_auth_mod()
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("")
            for k in nexudus_auth.TOKEN_ENV_KEYS:
                os.environ.pop(k, None)
            with patch.object(nexudus_auth, "ENV_PATH", env):
                self.assertFalse(nexudus_auth.is_authenticated())


def nexudus_auth_mod():
    import nexudus_auth
    return nexudus_auth


# --------------------------------------------------------------------------
# nexudus_auth.logout
# --------------------------------------------------------------------------
class TestLogout(unittest.TestCase):
    def test_clears_file_and_environ(self):
        import nexudus_auth
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text("NEXUDUS_ACCESS_TOKEN=a\nNEXUDUS_REFRESH_TOKEN=r\n"
                           "NEXUDUS_TOKEN_EXPIRES_AT=9999999999\n")
            self.addCleanup(lambda: [os.environ.pop(k, None) for k in nexudus_auth.TOKEN_ENV_KEYS])
            for k, v in (("NEXUDUS_ACCESS_TOKEN", "a"), ("NEXUDUS_REFRESH_TOKEN", "r"),
                         ("NEXUDUS_TOKEN_EXPIRES_AT", "9999999999")):
                os.environ[k] = v
            with patch.object(nexudus_auth, "ENV_PATH", env):
                nexudus_auth.logout()
                text = env.read_text()
                for k in nexudus_auth.TOKEN_ENV_KEYS:
                    self.assertNotIn(k, text)
                    self.assertNotIn(k, os.environ)
                self.assertFalse(nexudus_auth.is_authenticated())


# --------------------------------------------------------------------------
# report surface
# --------------------------------------------------------------------------
class TestReportSurface(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.created = self.root / "created-ids"
        self.created.mkdir()
        self.output = self.root / "output"
        self.output.mkdir()
        self.report_path = self.root / "last-run-report.txt"
        self.teardown_report_path = self.root / "last-teardown-report.txt"

    @contextlib.contextmanager
    def _patched(self):
        patches = (
            patch.object(report_lib, "CREATED_IDS_DIR", self.created),
            patch.object(report_lib, "REPORT_PATH", self.report_path),
            patch.object(report_lib, "TEARDOWN_REPORT_PATH", self.teardown_report_path),
            patch.object(config, "OUTPUT_DIR", self.output),
            # Without this, target_for() -> real_targets() would instantiate
            # the *real* generators/0N_*.py classes against this repo's
            # actual data/ — real_targets() itself has its own dedicated
            # tests (test_report_lib.py); these tests only care about
            # gather_report()'s shape, so it's forced empty here.
            patch.object(report_lib, "LAYERS", []),
            patch.object(report_lib, "_real_targets_cache", {"fingerprint": None, "value": {}}),
        )
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            yield

    def test_populated(self):
        (self.created / "reference.json").write_text(json.dumps([
            {"entity": "taxrates", "Id": 1}, {"entity": "taxrates", "Id": 2},
            {"entity": "resourcetypes", "Id": 9},  # tracked, but the run below never touched it
            {"Id": 3},  # no "entity" tag — a stray/malformed record
        ]))
        self.report_path.write_text(
            "Report generated 2026-08-27T09:08:24+00:00\n\n=== This run ===\nx\n"
            "=== What's in the account now (cumulative, all runs) ===\ncut me\n")
        # The JSON sibling report_lib.write_run_json actually writes alongside it.
        self.report_path.with_suffix(".json").write_text(json.dumps({
            "generated_at": "2026-08-27T09:08:24+00:00",
            "layer_failures": ["Layer 5 (CommunityGenerator): boom"],
            "entities": {
                "taxrates": {"target": 2, "created": 2, "skipped": 0, "failed": 0,
                             "failure_reasons": {}},
                "visitors": {"target": 5, "created": 3, "skipped": 0, "failed": 2,
                             "failure_reasons": {"Access Denied": 2}},
            },
        }))
        (self.output / "coworkers.csv").write_text("Id,Name\n1,a\n2,b\n3,c\n")
        (self.output / "visitors.csv").write_text("Id,Name\n")  # header only
        with self._patched():
            out = report.gather_report()

        # structured cumulative rows
        tax = next(r for r in out["report"] if r["entity"] == "taxrates")
        self.assertEqual(tax["created"], 2)
        self.assertIn("total", out["summary"])
        self.assertEqual(out["summary"]["malformed"], 1)

        # this run's per-entity delta joined onto the same rows — present
        # for an entity the last run touched, None for one it didn't.
        self.assertEqual(tax["last_run"], {"created": 2, "failed": 0})
        rt = next(r for r in out["report"] if r["entity"] == "resourcetypes")
        self.assertIsNone(rt["last_run"])

        # run-level summary for the status strip
        rs = out["run_summary"]
        self.assertEqual(rs["generated_at"], "2026-08-27T09:08:24+00:00")
        self.assertEqual(rs["layer_failures"], ["Layer 5 (CommunityGenerator): boom"])
        self.assertEqual(rs["total_created"], 5)
        self.assertEqual(rs["total_failed"], 2)
        self.assertEqual(rs["entities_failed"], 1)
        self.assertEqual(rs["top_failure_reasons"], [("Access Denied", 2)])

        # the raw file, shown verbatim (no more trimming the cumulative half out)
        self.assertEqual(out["last_run"]["generated_at"], "2026-08-27T09:08:24+00:00")
        self.assertIn("cut me", out["last_run"]["text"])

        # per-CSV row counts + summary
        by_name = {o["name"]: o for o in out["outputs"]}
        self.assertEqual(by_name["coworkers.csv"]["rows"], 3)
        self.assertEqual(by_name["visitors.csv"]["rows"], 0)
        self.assertEqual(out["outputs_summary"], {"files": 2, "with_rows": 1})

    def test_no_run_json_falls_back_to_text_timestamp_only(self):
        # An older last-run-report.txt from before write_run_json existed:
        # still gives a timestamp (scraped from the text), just no per-entity
        # deltas or run_summary to join onto the table.
        self.report_path.write_text("Report generated 2026-08-27T09:08:24+00:00\n\nx\n")
        with self._patched():
            out = report.gather_report()
        self.assertEqual(out["last_run"]["generated_at"], "2026-08-27T09:08:24+00:00")
        self.assertIsNone(out["run_summary"])

    def test_all_absent(self):
        with self._patched():
            out = report.gather_report()
        self.assertIsNone(out["last_run"]["text"])
        self.assertIsNone(out["run_summary"])
        self.assertIsNone(out["teardown_summary"])
        self.assertIsNone(out["teardown_report"]["text"])
        self.assertIsNone(out["latest_action"])
        self.assertEqual(out["outputs"], [])
        self.assertEqual(out["report"], [])
        self.assertEqual(out["outputs_summary"], {"files": 0, "with_rows": 0})
        self.assertEqual(out["summary"]["malformed"], 0)

    def test_teardown_surfaced_and_wins_latest_when_newer(self):
        (self.created / "reference.json").write_text(json.dumps([
            {"entity": "taxrates", "Id": 1}, {"entity": "taxrates", "Id": 2},
        ]))
        # A seeding run, then a later teardown of it.
        self.report_path.write_text("Report generated 2026-08-27T09:08:24+00:00\n\nx\n")
        self.report_path.with_suffix(".json").write_text(json.dumps({
            "generated_at": "2026-08-27T09:08:24+00:00", "layer_failures": [],
            "entities": {"taxrates": {"target": 2, "created": 2, "skipped": 0,
                                       "failed": 0, "failure_reasons": {}}},
        }))
        self.teardown_report_path.write_text(
            "Teardown report generated 2026-08-27T11:00:00+00:00\nMode: tracked\n")
        self.teardown_report_path.with_suffix(".json").write_text(json.dumps({
            "generated_at": "2026-08-27T11:00:00+00:00", "mode": "tracked",
            "totals": {"seen": 2, "deleted": 2, "marked_used": 0,
                        "skipped_no_support": 0, "failed": 0, "malformed": 0},
            "entities": {"taxrates": {"seen": 2, "deleted": 2, "marked_used": 0,
                                       "skipped_no_support": 0, "failed": 0,
                                       "failure_reasons": {}, "aborted": None}},
        }))
        with self._patched():
            out = report.gather_report()

        self.assertEqual(out["latest_action"], "teardown")
        ts = out["teardown_summary"]
        self.assertEqual(ts["total_deleted"], 2)
        self.assertEqual(ts["mode"], "tracked")
        tax = next(r for r in out["report"] if r["entity"] == "taxrates")
        self.assertEqual(tax["last_teardown"], {"deleted": 2, "failed": 0, "marked_used": 0})
        self.assertEqual(tax["last_run"], {"created": 2, "failed": 0})
        self.assertIn("Teardown report generated", out["teardown_report"]["text"])

    def test_older_teardown_does_not_win_latest_action(self):
        self.report_path.write_text("Report generated 2026-08-27T12:00:00+00:00\n\nx\n")
        self.teardown_report_path.write_text(
            "Teardown report generated 2026-08-27T09:00:00+00:00\nMode: tracked\n")
        self.teardown_report_path.with_suffix(".json").write_text(json.dumps({
            "generated_at": "2026-08-27T09:00:00+00:00", "mode": "tracked",
            "totals": {"deleted": 1, "failed": 0, "marked_used": 0,
                        "skipped_no_support": 0, "seen": 1, "malformed": 0},
            "entities": {},
        }))
        with self._patched():
            out = report.gather_report()
        self.assertEqual(out["latest_action"], "run")
        self.assertIsNotNone(out["teardown_summary"])  # still surfaced, just not latest


# --------------------------------------------------------------------------
# routing
# --------------------------------------------------------------------------
class _ServerCase(unittest.TestCase):
    def setUp(self):
        from webui.server import build_server
        self._patches = [
            patch("webui.auth.is_authenticated", return_value=False),
            patch("webui.auth.businesses",
                  return_value={"authenticated": False, "mode": "none", "businesses": []}),
            patch("webui.auth.logout"),  # never touch the real .env from a test
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        self.httpd = build_server("127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        self._t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._t.start()
        self.addCleanup(self._teardown)

    def _teardown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self._t.join(timeout=3)

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data, method=method,
            headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())


class TestRouting(_ServerCase):
    def test_commands_endpoint(self):
        status, body = self._request("GET", "/api/commands")
        self.assertEqual(status, 200)
        self.assertIn("groups", body)
        self.assertTrue(any(c["id"] == "pipeline" for c in body["commands"]))

    def test_status_endpoint(self):
        status, body = self._request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertFalse(body["authenticated"])
        self.assertIn("port", body)

    def test_plan_endpoint_shape(self):
        status, body = self._request("GET", "/api/plan")
        self.assertEqual(status, 200)
        for k in ("seed", "generated_at", "counts", "seeded"):
            self.assertIn(k, body)

    def test_argv_endpoint_previews_without_spawning(self):
        spy = PopenSpy()
        with patch("webui.jobs.subprocess.Popen", spy):
            status, body = self._request(
                "POST", "/api/argv",
                {"command": "pipeline", "params": {"layer": 3}, "business_id": 7})
        self.assertEqual(status, 200)
        self.assertIn("pipeline.py", body["argv"])
        self.assertIn("--business-id", body["argv"])
        self.assertIn("pipeline.py", body["display"])
        self.assertEqual(spy.calls, [])  # nothing spawned

    def test_argv_endpoint_validation_error(self):
        status, body = self._request(
            "POST", "/api/argv", {"command": "wizard", "params": {"coworkers": 9999}})
        self.assertEqual(status, 400)
        self.assertIn("coworkers", body["error"])

    def test_logout_endpoint(self):
        status, body = self._request("POST", "/api/auth/logout")
        self.assertEqual(status, 200)
        self.assertFalse(body["authenticated"])

    def test_output_zip(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "a.csv").write_text("Id\n1\n")
            (out / "b.csv").write_text("Id\n")
            with patch.object(config, "OUTPUT_DIR", out):
                url = f"http://127.0.0.1:{self.port}/api/output.zip"
                with urllib.request.urlopen(url, timeout=3) as r:
                    self.assertEqual(r.headers["Content-Type"], "application/zip")
                    data = r.read()
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual(sorted(z.namelist()), ["a.csv", "b.csv"])

    def test_output_zip_404_when_empty(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(config, "OUTPUT_DIR", Path(d)):
                status, _ = self._request("GET", "/api/output.zip")
        self.assertEqual(status, 404)

    def test_logout_blocked_while_run_active(self):
        blocker = FakePopen(block=True)
        with patch("webui.jobs.subprocess.Popen", PopenSpy(lambda: blocker)):
            start_status, _ = self._request(
                "POST", "/api/run", {"command": "verify"})
            self.assertEqual(start_status, 201)
            status, body = self._request("POST", "/api/auth/logout")
            self.assertEqual(status, 409)
            blocker.finish(0)


if __name__ == "__main__":
    unittest.main()
