"""
Base generator with idempotency checks, ID tracking, and Nexudus MCP helpers.

All layer generators inherit from BaseGenerator.

Usage:
    python generators/00_reference.py              # Live mode (creates records)
    python generators/00_reference.py --dry-run     # Logs what would be created
"""

import argparse
import json
import logging
import os
import random
from collections import Counter

from config import (
    CREATED_IDS_DIR,
    RANDOM_SEED,
    TEST_EMAIL_DOMAIN,
    TEST_EMAIL_PREFIX,
    TEST_NAME_PREFIX,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

# Global dry-run flag — set via CLI or DRY_RUN env var
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Log what would be created without making API calls")
    parser.add_argument("--business-id", type=int, default=None,
                        help="Which business/location to seed into, if this login has access to more than one")
    return parser.parse_args()


class _CountingLogger(logging.LoggerAdapter):
    """Wraps a generator's logger so warnings can opt into the run summary.

    Not every warning means a record was skipped — e.g. a helper like
    04_activity.py's _grant_day_pass logs its own warning explaining *why*
    it failed, and the caller then logs a second warning that the check-in
    itself was skipped as a result. Counting every warning would double-book
    that one real skip as two failures. skip=True marks the ones that
    actually correspond to "this record was not created" — the ones
    immediately followed by abandoning the record — leaving purely
    diagnostic/explanatory warnings uncounted.

    entity=/reason= (optional, alongside skip=True) additionally attribute
    the failure to a specific entity's per-entity tally and a short,
    categorized reason — see BaseGenerator.entity_counts. Not required:
    older call sites that only pass skip=True still work, they just aren't
    attributed to a specific entity in the per-entity breakdown.
    """

    def __init__(self, logger, counts, entity_bucket_fn):
        super().__init__(logger, {})
        self._counts = counts
        self._entity_bucket_fn = entity_bucket_fn

    def warning(self, msg, *args, skip=False, entity=None, reason=None, **kwargs):
        if skip:
            self._counts["failed"] += 1
            if entity:
                bucket = self._entity_bucket_fn(entity)
                bucket["failed"] += 1
                bucket["failure_reasons"][reason or "unknown_error"] += 1
        return self.logger.warning(msg, *args, **kwargs)


class BaseGenerator:
    """Base class for all layer generators."""

    entity_name: str = ""  # Override in subclass, e.g. "coworkers"

    def __init__(self, seed: int = RANDOM_SEED, dry_run: bool = False):
        self.rng = random.Random(seed)
        self.dry_run = dry_run or DRY_RUN
        self.counts = {"created": 0, "skipped": 0, "failed": 0}
        # Per-entity breakdown of the same three counts, plus what this run
        # actually planned to create (from the loaded plan data, via
        # set_target — not a config.py default) and *why* anything failed.
        # A single generator often creates several different entity types
        # (e.g. ActivityGenerator: bookings, bookingvisitors, checkins, ...)
        # — self.counts blends all of them into one aggregate, which isn't
        # enough to tell a QA reader which entity fell short or why.
        self.entity_counts: dict[str, dict] = {}
        self._failure_streaks: dict[str, tuple] = {}  # entity -> (last error text, consecutive count)
        self.log = _CountingLogger(logging.getLogger(self.__class__.__name__), self.counts, self._entity_bucket)
        self._ids_file = CREATED_IDS_DIR / f"{self.entity_name}.json"
        self._created_ids: list[dict] = self._load_ids()
        if self.dry_run:
            self.log.info("DRY RUN — no records will be created")

    # ------------------------------------------------------------------
    # ID tracking
    # ------------------------------------------------------------------

    def _load_ids(self) -> list[dict]:
        if self._ids_file.exists():
            return json.loads(self._ids_file.read_text())
        return []

    def _save_ids(self):
        CREATED_IDS_DIR.mkdir(parents=True, exist_ok=True)
        self._ids_file.write_text(json.dumps(self._created_ids, indent=2))

    def track_id(self, record: dict):
        """Append a created record's key fields and persist.

        Guards against tracking the same live record twice: a genuinely
        new record always gets a fresh Id from the API, so a repeat
        entity+Id here means something wrote to this tracking file more
        than once for the same live record, not a real second creation —
        confirmed to actually happen once, from two overlapping process
        runs racing on the same file (each loaded its own now-stale
        in-memory snapshot, so neither saw the other's writes; see
        CLAUDE.md). Re-reads the file fresh right before checking, rather
        than trusting only this instance's in-memory snapshot from
        __init__, to narrow that race window as much as a plain
        read-check-write can (not a full lock — good enough for this
        project's call pattern of sequential API calls, not literal
        multi-threading)."""
        entity = record.get("entity")
        record_id = record.get("Id")
        if entity and record_id is not None:
            self._created_ids = self._load_ids()
            if any(r.get("entity") == entity and r.get("Id") == record_id for r in self._created_ids):
                self.log.warning("track_id: %s %s already tracked elsewhere — skipping duplicate",
                                  entity, record_id)
                return
        self._created_ids.append(record)
        self._save_ids()
        self.counts["created"] += 1
        if entity:
            self._entity_bucket(entity)["created"] += 1

    def log_would_create(self, entity: str, body: dict):
        """In dry-run mode, log the record that would be created."""
        self.log.info("WOULD CREATE %s: %s", entity, json.dumps(body, indent=2))
        self.counts["created"] += 1
        self._entity_bucket(entity)["created"] += 1

    def get_tracked_ids(self) -> list[dict]:
        return self._created_ids

    # ------------------------------------------------------------------
    # Per-entity target/outcome tracking
    # ------------------------------------------------------------------

    def _entity_bucket(self, entity: str) -> dict:
        return self.entity_counts.setdefault(entity, {
            "target": 0, "created": 0, "skipped": 0, "failed": 0,
            "failure_reasons": Counter(),
        })

    def set_target(self, entity: str, target: int):
        """Register how many `entity` records this run's plan data actually
        calls for — computed from the loaded data/*.json plan, not a
        config.VOLUMES default, so it's always exactly what *this* run
        intended (respecting whatever volumes were configured). Call once
        per entity at __init__, before any creation happens."""
        self._entity_bucket(entity)["target"] = target

    def classify_failure(self, entity: str, error: Exception, repeat_threshold: int = 3) -> str:
        """Classify a create/update/run_command failure for `entity` that
        doesn't already have a specific, diagnosed handler at the call
        site. Returns:
        - "skip" — a one-off per-record problem; caller should skip this
          one record and keep looping.
        - "systemic" — this exact error text has now repeated
          `repeat_threshold` times in a row for this entity, suggesting an
          account-wide condition (e.g. an undocumented creation-rate
          limit) rather than a bad record; caller should stop the loop
          entirely instead of continuing to hit the same wall.
        Call sites with a diagnosed, specific error text should keep
        handling it directly — this is the fallback for everything else.
        """
        text = str(error)
        prev_text, prev_count = self._failure_streaks.get(entity, (None, 0))
        count = prev_count + 1 if text == prev_text else 1
        self._failure_streaks[entity] = (text, count)
        return "systemic" if count >= repeat_threshold else "skip"

    def fail_loudly(self, entity: str, error: Exception, reason: str = "foundational_failure"):
        """For small, fixed-size, foundational entities where almost
        everything downstream depends on every single one existing (see
        00_reference.py's tax rates/financial accounts/resource types) —
        unlike classify_failure's skip/systemic, there's no "try the next
        one and see": one failure here would otherwise cascade silently
        into dozens of unrelated-looking "parent_skipped" reasons across
        every later layer instead of pointing at its real cause. Records
        the failure into entity_counts first (skip=True) so it's still
        visible in the run reconciliation report — pipeline.py's existing
        try/finally prints and writes that report even as this exception
        propagates and stops the run — then raises.
        """
        self.log.warning(
            "Stopping this run — '%s' is a foundational entity almost everything "
            "downstream depends on, so a failure here isn't safe to skip past: %s",
            entity, error, skip=True, entity=entity, reason=reason)
        raise RuntimeError(
            f"Foundational entity '{entity}' failed to create — stopping rather than "
            f"cascading a missing dependency silently through everything downstream: {error}"
        ) from error

    # ------------------------------------------------------------------
    # Idempotency helpers
    # ------------------------------------------------------------------

    def already_created(self, key_field: str, key_value: str, entity: str = None) -> bool:
        """Check if a record with the given key was already created.
        entity is optional — pass it to attribute the skip to a specific
        entity's per-entity tally (see entity_counts); omitted, the skip
        still counts toward the generator-wide aggregate as before."""
        found = any(r.get(key_field) == key_value for r in self._created_ids)
        if found:
            self.counts["skipped"] += 1
            if entity:
                self._entity_bucket(entity)["skipped"] += 1
        return found

    # ------------------------------------------------------------------
    # Run summary
    # ------------------------------------------------------------------

    def count_skip(self, n: int = 1, entity: str = None):
        """For skip paths that don't go through already_created() — e.g. a
        live-API name/email lookup finding an existing record."""
        self.counts["skipped"] += n
        if entity:
            self._entity_bucket(entity)["skipped"] += n

    def count_create(self, n: int = 1, entity: str = None):
        """For creation paths that don't go through track_id() — e.g.
        daily_update.py, which doesn't track IDs (see its own docstring)."""
        self.counts["created"] += n
        if entity:
            self._entity_bucket(entity)["created"] += n

    def summary_line(self) -> str:
        c, s, f = self.counts["created"], self.counts["skipped"], self.counts["failed"]
        return f"[{self.entity_name}] Created: {c}  Skipped: {s}  Failed: {f}"

    # ------------------------------------------------------------------
    # Test marker helpers
    # ------------------------------------------------------------------

    @staticmethod
    def test_email(index: int) -> str:
        return f"{TEST_EMAIL_PREFIX}{index:03d}@{TEST_EMAIL_DOMAIN}"

    @staticmethod
    def test_name(name: str) -> str:
        return f"{TEST_NAME_PREFIX}{name}"

    # ------------------------------------------------------------------
    # Subclass interface
    # ------------------------------------------------------------------

    def run(self):
        """Override in subclass to execute the generator."""
        raise NotImplementedError
