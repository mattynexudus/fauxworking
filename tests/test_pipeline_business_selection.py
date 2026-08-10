"""
Tests for pipeline._select_business — the pure logic behind picking which
business (location) to seed into, for logins with access to more than one.
No live API calls: pipeline.list_businesses()/_whoami() hit the network,
but _select_business() itself just operates on a list already in hand.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipeline


class TestSelectBusiness(unittest.TestCase):
    def setUp(self):
        self.one = [{"Id": 111, "Name": "Only Business"}]
        self.many = [
            {"Id": 111, "Name": "First Business"},
            {"Id": 222, "Name": "Second Business"},
        ]

    def test_empty_list_raises(self):
        with self.assertRaises(SystemExit):
            pipeline._select_business([])

    def test_single_business_no_id_given_returns_it(self):
        result = pipeline._select_business(self.one)
        self.assertEqual(result["Id"], 111)

    def test_single_business_matching_id_given_returns_it(self):
        result = pipeline._select_business(self.one, business_id=111)
        self.assertEqual(result["Id"], 111)

    def test_single_business_wrong_id_given_raises(self):
        # Catches a typo'd --business-id rather than silently ignoring it.
        with self.assertRaises(SystemExit):
            pipeline._select_business(self.one, business_id=999)

    def test_multiple_businesses_no_id_given_raises(self):
        # Never silently guesses which one to use.
        with self.assertRaises(SystemExit):
            pipeline._select_business(self.many)

    def test_multiple_businesses_valid_id_given_returns_it(self):
        result = pipeline._select_business(self.many, business_id=222)
        self.assertEqual(result["Id"], 222)
        self.assertEqual(result["Name"], "Second Business")

    def test_multiple_businesses_invalid_id_given_raises(self):
        with self.assertRaises(SystemExit):
            pipeline._select_business(self.many, business_id=999)


if __name__ == "__main__":
    unittest.main()
