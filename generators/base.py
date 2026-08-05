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
import sys
from pathlib import Path

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
    return parser.parse_args()


class BaseGenerator:
    """Base class for all layer generators."""

    entity_name: str = ""  # Override in subclass, e.g. "coworkers"

    def __init__(self, seed: int = RANDOM_SEED, dry_run: bool = False):
        self.rng = random.Random(seed)
        self.dry_run = dry_run or DRY_RUN
        self.log = logging.getLogger(self.__class__.__name__)
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
        """Append a created record's key fields and persist."""
        self._created_ids.append(record)
        self._save_ids()

    def log_would_create(self, entity: str, body: dict):
        """In dry-run mode, log the record that would be created."""
        self.log.info("WOULD CREATE %s: %s", entity, json.dumps(body, indent=2))

    def get_tracked_ids(self) -> list[dict]:
        return self._created_ids

    # ------------------------------------------------------------------
    # Idempotency helpers
    # ------------------------------------------------------------------

    def already_created(self, key_field: str, key_value: str) -> bool:
        """Check if a record with the given key was already created."""
        return any(r.get(key_field) == key_value for r in self._created_ids)

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
