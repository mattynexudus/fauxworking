"""
Tests for the browser control panel (webui/).

No network, no real subprocess: `subprocess.Popen` is replaced with `FakePopen`
(a canned line iterator that can block until released, so the one-run-at-a-time
lock is testable), and every Nexudus-touching call in `webui.auth` is patched
or simply never reached. `report_lib`/`config` paths are redirected to temp
dirs the same way `tests/test_teardown.py` does.
"""

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
        # live + clean: refused, never buildable
        with self.assertRaises(BadRequest):
            build_argv("teardown", {"mode": "clean"}, dry_run=False)

    def test_no_command_yields_clean_mode_live(self):
        for cmd_id in registry.BY_ID:
            try:
                argv = build_argv(cmd_id)
            except BadRequest:
                continue
            if "teardown.py" in argv and "--mode" in argv:
                mode = argv[argv.index("--mode") + 1]
                if mode == "clean":
                    self.assertIn("--dry-run", argv)

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

    def test_teardown_clean_live_refused(self):
        with patch("webui.jobs.subprocess.Popen", PopenSpy()) as spy:
            with self.assertRaises(BadRequest):
                self.mgr.start("teardown", params={"mode": "clean"}, dry_run=False,
                               confirm="delete tracked records")
        self.assertEqual(spy.calls, [])


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

    def _patches(self):
        return (
            patch.object(report_lib, "CREATED_IDS_DIR", self.created),
            patch.object(report_lib, "REPORT_PATH", self.report_path),
            patch.object(config, "OUTPUT_DIR", self.output),
        )

    def test_populated(self):
        (self.created / "reference.json").write_text(
            json.dumps([{"entity": "taxrates", "Id": 1}, {"entity": "taxrates", "Id": 2}]))
        self.report_path.write_text("RUN REPORT TEXT")
        (self.output / "coworkers.csv").write_text("Id\n1\n")
        p1, p2, p3 = self._patches()
        with p1, p2, p3:
            out = report.gather_report()
        self.assertTrue(out["report_lines"])
        self.assertEqual(out["last_run_report"], "RUN REPORT TEXT")
        self.assertEqual([o["name"] for o in out["outputs"]], ["coworkers.csv"])

    def test_all_absent(self):
        p1, p2, p3 = self._patches()
        with p1, p2, p3:
            out = report.gather_report()
        self.assertIsNone(out["last_run_report"])
        self.assertEqual(out["outputs"], [])
        self.assertIsInstance(out["report_lines"], list)


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
