"""
Base generator with idempotency checks, ID tracking, and Nexudus MCP helpers.

All layer generators inherit from BaseGenerator.
"""

import json
import logging
import random
from pathlib import Path

from config import (
    CREATED_IDS_DIR,
    RANDOM_SEED,
    TEST_EMAIL_DOMAIN,
    TEST_EMAIL_PREFIX,
    TEST_NAME_PREFIX,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")


class BaseGenerator:
    """Base class for all layer generators."""

    entity_name: str = ""  # Override in subclass, e.g. "coworkers"

    def __init__(self, seed: int = RANDOM_SEED):
        self.rng = random.Random(seed)
        self.log = logging.getLogger(self.__class__.__name__)
        self._ids_file = CREATED_IDS_DIR / f"{self.entity_name}.json"
        self._created_ids: list[dict] = self._load_ids()

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
