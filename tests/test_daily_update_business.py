"""
daily_update.resolve_context() now honours an explicit business_id and, like
every other entry point (CLAUDE.md rule 8), refuses to silently guess when a
login has more than one business. Previously it always took businesses[0].

No network: a fake nexudus_list returns canned lists.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.daily_update import resolve_context


def _fake_list(businesses):
    def _list(entity, filters=None):
        if entity == "businesses":
            return businesses
        return []  # coworkers / resources / timepasses — empty is fine here
    return _list


class TestResolveContextBusiness(unittest.TestCase):
    def test_explicit_business_id_is_honoured(self):
        businesses = [{"Id": 1, "Name": "HQ"}, {"Id": 2, "Name": "Annexe"}]
        ctx = resolve_context(_fake_list(businesses), business_id=2)
        self.assertEqual(ctx["business_id"], 2)

    def test_single_business_needs_no_id(self):
        ctx = resolve_context(_fake_list([{"Id": 7, "Name": "Only"}]))
        self.assertEqual(ctx["business_id"], 7)

    def test_multi_business_without_id_fails_loudly(self):
        businesses = [{"Id": 1, "Name": "HQ"}, {"Id": 2, "Name": "Annexe"}]
        with self.assertRaises(SystemExit):
            resolve_context(_fake_list(businesses))

    def test_unknown_business_id_fails_loudly(self):
        with self.assertRaises(SystemExit):
            resolve_context(_fake_list([{"Id": 1, "Name": "HQ"}]), business_id=999)


if __name__ == "__main__":
    unittest.main()
