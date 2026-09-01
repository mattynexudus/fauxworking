"""
Run one project command at a time as a child process, stream its merged
stdout+stderr, and keep a durable per-run log.

Everything the control panel runs goes through `JobManager.start()`:

  * one run at a time — a second start while one is active raises
    `RunInProgress` (HTTP 409), no queue;
  * `subprocess.Popen([...], stdin=DEVNULL, stdout=PIPE, stderr=STDOUT)` with
    no shell, so the server process never imports generator code (and never
    inherits `generators/base.py`'s process-wide `logging.basicConfig`), a
    child `SystemExit` is just a non-zero exit, and a child crash can't take
    the server down;
  * a daemon reader thread appends each line to an in-memory buffer *and*
    `logs/run-<id>.log`, so a browser refresh or a server restart can still
    show the whole run;
  * `Job.sse_events()` turns the buffer into Server-Sent-Events frames,
    resumable from a line index (EventSource `Last-Event-ID`).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone

import config
from webui import registry
from webui.registry import BadRequest

_HEARTBEAT_SECS = 15
_CANCEL_GRACE_SECS = 10
_MAX_LOG_FILES = 200   # keep the newest N run-*.log; prune the rest on each start


class RunInProgress(Exception):
    """Raised by start() when a run is already active. Carries the active Job."""

    def __init__(self, active):
        super().__init__("A run is already in progress.")
        self.active = active


class SpawnFailed(BadRequest):
    """The child process could not be started at all (bad argv, missing
    interpreter, ...). A BadRequest so the handler answers 400."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _frame(event_id, event, data) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {data}\n\n"


class Job:
    def __init__(self, run_id, command, argv, log_path):
        self.run_id = run_id
        self.command = command
        self.argv = argv
        self.log_path = log_path
        self.status = "running"        # running|succeeded|failed|cancelled|error
        self.started_at = time.time()
        self.ended_at = None
        self.exit_code = None
        self.proc = None
        self._lines = []
        self._cond = threading.Condition()
        self._done = threading.Event()
        self._cancelled = False

    # -- views -------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "command": self.command,
            "argv": self.argv,
            "argv_display": " ".join(registry.pretty_argv(self.argv)) if self.argv else None,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "exit_code": self.exit_code,
            "log_file": str(self.log_path),
        }

    def tail(self, n=200) -> list:
        with self._cond:
            return list(self._lines[-n:])

    def line_count(self) -> int:
        with self._cond:
            return len(self._lines)

    # -- streaming -------------------------------------------------------------
    def sse_events(self, from_index=0):
        """Yield SSE frames from line `from_index` onward: one `event: line`
        per output line (`id:` = 1-based line index), a `: heartbeat` at least
        every ~15s while idle, then a final `event: end` with the outcome.
        A generator — the caller writes each yielded string to the socket and
        stops when it returns (or the socket breaks)."""
        try:
            i = max(0, int(from_index))
        except (TypeError, ValueError):
            i = 0
        next_heartbeat = time.monotonic() + _HEARTBEAT_SECS

        while True:
            with self._cond:
                while len(self._lines) <= i and not self._done.is_set():
                    remaining = next_heartbeat - time.monotonic()
                    if remaining <= 0:
                        break
                    self._cond.wait(timeout=remaining)
                pending = self._lines[i:]
                done = self._done.is_set()
                total = len(self._lines)

            for line in pending:
                i += 1
                yield _frame(i, "line", line)

            if pending:
                next_heartbeat = time.monotonic() + _HEARTBEAT_SECS
            elif time.monotonic() >= next_heartbeat:
                yield ": heartbeat\n\n"
                next_heartbeat = time.monotonic() + _HEARTBEAT_SECS

            if done and i >= total:
                break

        yield _frame(i + 1, "end",
                     json.dumps({"status": self.status, "exit_code": self.exit_code}))


class JobManager:
    def __init__(self, logs_dir=None):
        self._logs_dir = logs_dir or config.LOGS_DIR
        self._lock = threading.Lock()
        self._active = None
        self._history = []

    # -- lifecycle ----------------------------------------------------------
    def start(self, command_id, params=None, business_id=None, dry_run=False,
              confirm=None, businesses_mode="single") -> Job:
        cmd = registry.BY_ID.get(command_id)
        if cmd is None:
            raise BadRequest(f"Unknown command: {command_id!r}")

        if cmd.destructive and not dry_run and (confirm or "").strip() != cmd.confirm_phrase:
            raise BadRequest(
                f'This command needs the exact phrase "{cmd.confirm_phrase}" to run live.')

        if cmd.accepts_business_id and businesses_mode == "multi" and business_id is None:
            raise BadRequest(
                "This login can access more than one business — pick one before running "
                "a command that touches the live account.")

        argv = registry.build_argv(command_id, params, business_id, dry_run)

        with self._lock:
            if self._active is not None and self._active.status == "running":
                raise RunInProgress(self._active)

            run_id = self._next_run_id(command_id)
            self._logs_dir.mkdir(parents=True, exist_ok=True)
            self._prune_logs()
            log_path = self._logs_dir / f"run-{run_id}.log"
            log_fh = open(log_path, "w", encoding="utf-8")
            log_fh.write(f"# {_now_iso()}  cwd={config.PROJECT_ROOT}  argv={argv}\n")
            log_fh.flush()

            try:
                proc = subprocess.Popen(
                    argv,
                    cwd=str(config.PROJECT_ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                    env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )
            except OSError as e:
                log_fh.write(f"# spawn failed: {e}\n")
                log_fh.close()
                job = Job(run_id, command_id, argv, log_path)
                job.status = "error"
                job.ended_at = time.time()
                job._done.set()
                self._history.append(job)
                raise SpawnFailed(f"Could not start {command_id}: {e}") from e

            job = Job(run_id, command_id, argv, log_path)
            job.proc = proc
            self._active = job
            self._history.append(job)

        threading.Thread(target=self._pump, args=(job, log_fh), daemon=True).start()
        return job

    def cancel(self, run_id) -> None:
        job = self.get(run_id)
        if job is None or job.status != "running":
            raise BadRequest("That run is not currently active.")
        job._cancelled = True
        try:
            job.proc.terminate()
        except (OSError, AttributeError):
            pass
        threading.Thread(target=self._watchdog, args=(job,), daemon=True).start()

    # -- queries ----------------------------------------------------------
    def active(self) -> Job | None:
        with self._lock:
            if self._active is not None and self._active.status == "running":
                return self._active
        return None

    def get(self, run_id) -> Job | None:
        with self._lock:
            for j in reversed(self._history):
                if j.run_id == run_id:
                    return j
        return None

    def recent(self, limit=50) -> list:
        rows = {}
        with self._lock:
            for j in self._history:
                rows[j.run_id] = j.to_dict()
        if self._logs_dir.exists():
            for p in sorted(self._logs_dir.glob("run-*.log")):
                rid = p.stem[len("run-"):]
                if rid not in rows:
                    rows[rid] = _row_from_log(p, rid)
        ordered = sorted(rows.values(), key=lambda r: r.get("started_at") or 0, reverse=True)
        return ordered[:limit]

    # -- internals ----------------------------------------------------------
    def _prune_logs(self) -> None:
        """Keep only the newest _MAX_LOG_FILES run-*.log — the Runs view shows a
        capped list and nothing else reads old logs, so they just accumulate."""
        try:
            files = sorted(self._logs_dir.glob("run-*.log"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return
        for p in files[_MAX_LOG_FILES:]:
            try:
                p.unlink()
            except OSError:
                pass

    def _next_run_id(self, command_id) -> str:
        base = f"{time.strftime('%Y%m%d-%H%M%S')}-{command_id}"
        taken = {j.run_id for j in self._history}
        if base not in taken:
            return base
        n = 2
        while f"{base}-{n}" in taken:
            n += 1
        return f"{base}-{n}"

    def _pump(self, job, log_fh) -> None:
        try:
            for raw in iter(job.proc.stdout.readline, ""):
                line = raw.rstrip("\r\n")
                with job._cond:
                    job._lines.append(line)
                    job._cond.notify_all()
                try:
                    log_fh.write(line + "\n")
                    log_fh.flush()
                except OSError:
                    pass
            code = job.proc.wait()
        except Exception as e:  # noqa: BLE001 — never leave the job hung
            code = job.proc.poll()
            if code is None:
                try:
                    job.proc.kill()
                    code = job.proc.wait()
                except OSError:
                    code = -1
            with job._cond:
                job._lines.append(f"# reader error: {e}")
                job._cond.notify_all()

        job.exit_code = code
        job.ended_at = time.time()
        if job._cancelled:
            job.status = "cancelled"
        elif code == 0:
            job.status = "succeeded"
        else:
            job.status = "failed"

        try:
            log_fh.write(f"# exited status={job.status} code={code} at {_now_iso()}\n")
            log_fh.close()
        except OSError:
            pass

        with job._cond:
            job._done.set()
            job._cond.notify_all()
        with self._lock:
            if self._active is job:
                self._active = None

    def _watchdog(self, job) -> None:
        try:
            job.proc.wait(timeout=_CANCEL_GRACE_SECS)
        except subprocess.TimeoutExpired:
            try:
                job.proc.kill()
            except OSError:
                pass


def _row_from_log(path, rid) -> dict:
    """A history row reconstructed from a log file left by an earlier server
    process — enough for the Runs table to list and open it."""
    command = re.sub(r"-\d+$", "", rid.split("-", 2)[-1])
    argv, status, exit_code = None, "unknown", None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    if lines and lines[0].startswith("# ") and "argv=" in lines[0]:
        argv = lines[0].split("argv=", 1)[1]
    for line in reversed(lines):
        if line.startswith("# exited "):
            m = re.search(r"status=(\S+) code=(-?\d+)", line)
            if m:
                status, exit_code = m.group(1), int(m.group(2))
            break
    try:
        started = path.stat().st_mtime
    except OSError:
        started = None
    return {
        "run_id": rid, "command": command, "argv": argv, "status": status,
        "started_at": started, "ended_at": None, "exit_code": exit_code,
        "log_file": str(path),
    }
