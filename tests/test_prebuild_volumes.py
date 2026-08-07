"""
Tests for prebuild.py's configurable-volume plumbing: rescale_plan() and the
11 CONFIGURABLE_VOLUME_KEYS generate_* functions, at small/edge totals that
would trip the correctness fixes found while wiring this up — e.g.
generate_checkins sampling more items than exist, or generate_helpdesk_
messages' two paired plans (dept/priority) falling out of sync.

Calls the generate_* functions directly, never through generate_all()/
main() — those write to the real data/*.json files, which this session's
earlier live-bug-fix work hand-patched beyond what prebuild.py alone
produces (real content for blog posts, community threads, etc.); running
the full pipeline here would silently revert that content back to its
un-patched form.
"""

import random
import sys
import unittest
from pathlib import Path

from faker import Faker

sys.path.insert(0, str(Path(__file__).parent.parent))

import prebuild


class TestRescalePlan(unittest.TestCase):
    def setUp(self):
        self.plan = [("a", 20), ("b", 8), ("c", 6), ("d", 10),
                     ("e", 4), ("f", 6), ("g", 3), ("h", 3)]

    def test_sums_to_new_total(self):
        for new_total in [0, 1, 5, 10, 60, 100, 200]:
            with self.subTest(new_total=new_total):
                result = prebuild.rescale_plan(self.plan, new_total)
                self.assertEqual(sum(n for _, n in result), new_total)
                self.assertEqual([label for label, _ in result],
                                  [label for label, _ in self.plan])

    def test_zero_total_zeroes_everything(self):
        result = prebuild.rescale_plan([("a", 5), ("b", 5)], 0)
        self.assertEqual([n for _, n in result], [0, 0])

    def test_one_total_goes_to_largest_bucket(self):
        result = prebuild.rescale_plan(prebuild.LIFECYCLE_SCENARIOS, 1)
        self.assertEqual(sum(n for _, n in result), 1)
        winner = max(result, key=lambda t: t[1])
        self.assertEqual(winner[0], "long_term_active")

    def test_no_op_when_total_unchanged(self):
        result = prebuild.rescale_plan(prebuild.LIFECYCLE_SCENARIOS, 60)
        self.assertEqual(result, prebuild.LIFECYCLE_SCENARIOS)

    def test_no_bucket_goes_negative(self):
        for new_total in [0, 1, 3, 7]:
            result = prebuild.rescale_plan(self.plan, new_total)
            self.assertTrue(all(n >= 0 for _, n in result))


class TestConfigurableGenerators(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(1)
        self.fake = Faker("en_GB")
        Faker.seed(1)

    def test_generate_coworkers_small_count(self):
        coworkers = prebuild.generate_coworkers(self.rng, self.fake, count=3)
        self.assertEqual(len(coworkers), 3)

    def test_generate_coworkers_zero(self):
        self.assertEqual(prebuild.generate_coworkers(self.rng, self.fake, count=0), [])

    def test_generate_visitors_small_count(self):
        visitors = prebuild.generate_visitors(self.rng, self.fake, coworker_count=5, count=2)
        self.assertEqual(len(visitors), 2)

    def test_generate_bookings_small_total(self):
        coworkers = prebuild.generate_coworkers(self.rng, self.fake, count=10)
        visitors = prebuild.generate_visitors(self.rng, self.fake, coworker_count=10, count=5)
        bookings = prebuild.generate_bookings(self.rng, coworkers, visitors, total=12)
        self.assertEqual(len(bookings), 12)

    def test_generate_checkins_small_total_no_crash(self):
        coworkers = prebuild.generate_coworkers(self.rng, self.fake, count=10)
        # This used to crash: rng.sample(pool, 5) when the resolved pool has
        # fewer than 5 items.
        for total in [0, 1, 2, 3, 5, 20]:
            with self.subTest(total=total):
                checkins = prebuild.generate_checkins(self.rng, coworkers, total=total)
                self.assertEqual(len(checkins), total)

    def test_generate_crm_opportunities_small_count(self):
        coworkers = prebuild.generate_coworkers(self.rng, self.fake, count=10)
        opps = prebuild.generate_crm_opportunities(self.rng, coworkers, count=4)
        self.assertEqual(len(opps), 4)

    def test_generate_proposals_small_count(self):
        coworkers = prebuild.generate_coworkers(self.rng, self.fake, count=10)
        opps = prebuild.generate_crm_opportunities(self.rng, coworkers, count=4)
        proposals = prebuild.generate_proposals(self.rng, opps, coworkers, count=3)
        self.assertEqual(len(proposals), 3)

    def test_generate_helpdesk_messages_small_count_stays_in_lockstep(self):
        coworkers = prebuild.generate_coworkers(self.rng, self.fake, count=10)
        # This used to silently truncate: dept and priority pools falling
        # out of sync via zip() if only one of the two plans was rescaled.
        for count in [0, 1, 2, 5, 25]:
            with self.subTest(count=count):
                messages = prebuild.generate_helpdesk_messages(self.rng, coworkers, count=count)
                self.assertEqual(len(messages), count)

    def test_generate_community_threads_capped_by_content_pool(self):
        coworkers = prebuild.generate_coworkers(self.rng, self.fake, count=10)
        # Requesting more than the authored subject pool (15) shouldn't
        # crash — it caps per group instead of raising ValueError.
        threads = prebuild.generate_community_threads(self.rng, self.fake, coworkers, count=100)
        self.assertLessEqual(len(threads), 15)

    def test_generate_coworker_tasks_small_count(self):
        coworkers = prebuild.generate_coworkers(self.rng, self.fake, count=10)
        tasks = prebuild.generate_coworker_tasks(self.rng, coworkers, count=3)
        self.assertEqual(len(tasks), 3)

    def test_generate_time_passes_small_count(self):
        coworkers = prebuild.generate_coworkers(self.rng, self.fake, count=10)
        passes = prebuild.generate_time_passes(self.rng, coworkers, count=3)
        self.assertEqual(len(passes), 3)

    def test_generate_coworker_products_small_count(self):
        coworkers = prebuild.generate_coworkers(self.rng, self.fake, count=10)
        products = prebuild.generate_coworker_products(self.rng, coworkers, count=3)
        self.assertEqual(len(products), 3)


if __name__ == "__main__":
    unittest.main()
