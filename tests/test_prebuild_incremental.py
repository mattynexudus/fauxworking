"""
prebuild.generate_all is incremental by default: with a plan-manifest.json at
a matching seed it keeps every record already on disk and only appends the
newly-requested tail; it never shrinks a file, and a seed change or --fresh
forces a full regenerate.

No network. DATA_DIR and MANIFEST_PATH are redirected to a temp dir so the
real data/*.json are never touched (unlike a bare generate_all() call).
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import prebuild
from config import VOLUMES


def _vol(**over):
    # keep the non-varied volumes modest so the full generate_all is quick
    base = {**VOLUMES, "visitors": 8, "bookings_total": 15, "check_ins": 15,
            "crm_opportunities": 6, "proposals": 4, "help_desk_messages": 6,
            "community_threads": 6, "coworker_tasks": 5, "coworker_time_passes": 5,
            "coworker_products": 5}
    return {**base, **over}


class TestPrebuildIncremental(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)
        for p in (patch.object(prebuild, "DATA_DIR", self.data),
                  patch.object(prebuild, "MANIFEST_PATH", self.data / "plan-manifest.json")):
            p.start()
            self.addCleanup(p.stop)

    def _read(self, name):
        return json.loads((self.data / name).read_text(encoding="utf-8"))

    def test_append_keeps_head_and_grows(self):
        prebuild.generate_all(42, _vol(coworkers=6))
        head = self._read("coworkers.json")
        self.assertEqual(len(head), 6)
        self.assertTrue((self.data / "plan-manifest.json").exists())

        prebuild.generate_all(42, _vol(coworkers=9))
        grown = self._read("coworkers.json")
        self.assertEqual(len(grown), 9)
        self.assertEqual(grown[:6], head)  # the on-disk head is untouched
        # appended tail continues the index sequence, no collision
        self.assertEqual([c["index"] for c in grown], list(range(1, 10)))

        manifest = self._read("plan-manifest.json")
        self.assertEqual(manifest["seed"], 42)
        self.assertEqual(manifest["counts"]["coworkers"], 9)

    def test_lowering_a_count_is_a_noop(self):
        prebuild.generate_all(42, _vol(coworkers=9))
        prebuild.generate_all(42, _vol(coworkers=3))
        self.assertEqual(len(self._read("coworkers.json")), 9)

    def test_seed_change_regenerates_fresh(self):
        prebuild.generate_all(42, _vol(coworkers=6))
        head = self._read("coworkers.json")
        prebuild.generate_all(99, _vol(coworkers=6))
        self.assertNotEqual(self._read("coworkers.json"), head)
        self.assertEqual(self._read("plan-manifest.json")["seed"], 99)

    def test_fresh_flag_regenerates_despite_matching_manifest(self):
        prebuild.generate_all(42, _vol(coworkers=9))
        prebuild.generate_all(42, _vol(coworkers=3), fresh=True)
        self.assertEqual(len(self._read("coworkers.json")), 3)


if __name__ == "__main__":
    unittest.main()
