"""
06_financial.py builds each invoice a real date schedule rather than shifting
whatever Nexudus produced, and dates every payment off its own invoice's due
date instead of leaving the API to stamp "now".

No network — every callable the generator takes is a local stub, and both
track_id and the tracking file are stubbed out so nothing touches
data/created-ids/.
"""

import importlib
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DEFAULT_INVOICE_DUE_DAYS, NOW, to_utc_str

financial = importlib.import_module("generators.06_financial")


def _parse(raw):
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def _invoice(i, period_days=30, paid=False):
    return {
        "Id": 9000 + i,
        "CoworkerId": f"CW-{i}",
        "TotalAmount": 100.0 + i,
        "Paid": paid,
        "CreatedOn": to_utc_str(NOW),
        "SentOn": to_utc_str(NOW),
        "DueDate": to_utc_str(NOW + timedelta(days=3)),
        "InvoiceFromDate": to_utc_str(NOW),
        "InvoiceToDate": to_utc_str(NOW + timedelta(days=period_days)),
    }


class _Harness(unittest.TestCase):
    def make_gen(self, settings=None):
        gen = financial.FinancialGenerator(dry_run=False)
        gen.track_id = lambda record: None
        gen._save_ids = lambda: None
        gen.already_created = lambda *a, **k: False
        self.updates = []
        self.creates = []

        def _list(entity, filters=None):
            if entity == "businesssettings":
                return settings if settings is not None else []
            return []

        def _update(entity, id, body):
            self.updates.append((entity, id, dict(body)))
            return {"Id": id}

        def _create(entity, body):
            self.creates.append((entity, dict(body)))
            return {"Id": f"NEW-{len(self.creates)}"}

        self.list_fn, self.update_fn, self.create_fn = _list, _update, _create
        return gen


class TestDueDateSetting(_Harness):
    def test_reads_the_business_setting(self):
        gen = self.make_gen([{"BusinessId": "B1", "Name": "tariffDefaultDueDate", "Value": "14"}])
        self.assertEqual(gen._default_due_days("B1", self.list_fn), 14)

    def test_matches_a_dotted_key_and_odd_casing(self):
        gen = self.make_gen([{"BusinessId": "B1", "Name": "Billing.TariffDefaultDueDate",
                              "Value": "7"}])
        self.assertEqual(gen._default_due_days("B1", self.list_fn), 7)

    def test_ignores_another_businesss_setting(self):
        gen = self.make_gen([{"BusinessId": "OTHER", "Name": "tariffDefaultDueDate",
                              "Value": "30"}])
        self.assertEqual(gen._default_due_days("B1", self.list_fn), DEFAULT_INVOICE_DUE_DAYS)

    def test_falls_back_when_missing_unparseable_or_unreadable(self):
        for settings in ([], [{"BusinessId": "B1", "Name": "tariffDefaultDueDate",
                               "Value": "not a number"}]):
            gen = self.make_gen(settings)
            self.assertEqual(gen._default_due_days("B1", self.list_fn), DEFAULT_INVOICE_DUE_DAYS)

        gen = self.make_gen()

        def _boom(entity, filters=None):
            raise RuntimeError("no")

        self.assertEqual(gen._default_due_days("B1", _boom), DEFAULT_INVOICE_DUE_DAYS)

    def test_only_read_once_per_run(self):
        calls = []
        gen = self.make_gen([{"BusinessId": "B1", "Name": "tariffDefaultDueDate", "Value": "5"}])

        def _counting(entity, filters=None):
            calls.append(entity)
            return self.list_fn(entity, filters)

        gen._default_due_days("B1", _counting)
        gen._default_due_days("B1", _counting)
        self.assertEqual(len(calls), 1)


class TestInvoiceSchedule(_Harness):
    def setUp(self):
        self.gen = self.make_gen([{"BusinessId": "B1", "Name": "tariffDefaultDueDate",
                                   "Value": "3"}])
        self.invoices = [_invoice(i, period_days=7 if i % 4 == 0 else 30) for i in range(1, 13)]
        self.gen._list_invoices = lambda *a, **k: self.invoices
        self.coworker_ids = {i: f"CW-{i}" for i in range(1, 13)}
        self.schedule = self.gen._schedule_invoice_dates(
            self.invoices, "B1", self.coworker_ids, lambda e, f=None: (
                self.invoices if e == "coworkerinvoices"
                else [{"BusinessId": "B1", "Name": "tariffDefaultDueDate", "Value": "3"}]),
            self.update_fn)

    def _bodies(self):
        return [b for _e, _i, b in self.updates]

    def test_every_invoice_is_scheduled(self):
        self.assertEqual(len(self.schedule), len(self.invoices))
        self.assertEqual(len(self.updates), len(self.invoices))

    def test_due_date_is_the_setting_offset_after_the_invoice_date(self):
        for b in self._bodies():
            self.assertEqual((_parse(b["DueDate"]) - _parse(b["CreatedOn"])).days, 3)

    def test_period_covers_the_month_after_the_invoice_date(self):
        for b in self._bodies():
            issued, start = _parse(b["CreatedOn"]), _parse(b["InvoiceFromDate"])
            self.assertEqual(start.day, 1)
            self.assertEqual(start.month, (issued.month % 12) + 1)
            self.assertEqual(start.year, issued.year + (1 if issued.month == 12 else 0))

    def test_period_length_follows_the_plans_own_cycle(self):
        lengths = {(_parse(b["InvoiceToDate"]) - _parse(b["InvoiceFromDate"])).days
                   for b in self._bodies()}
        self.assertIn(7, lengths, "a weekly plan's period should stay 7 days")
        self.assertTrue(any(28 <= n <= 31 for n in lengths),
                        "a monthly plan's period should stay a calendar month")

    def test_nothing_is_dated_into_the_future(self):
        for b in self._bodies():
            self.assertLessEqual(_parse(b["CreatedOn"]), NOW)

    def test_dates_spread_across_the_window(self):
        months = {_parse(b["CreatedOn"]).strftime("%Y-%m") for b in self._bodies()}
        self.assertGreater(len(months), 1, "invoices should not all land in one month")

    def test_sent_on_tracks_the_invoice_date(self):
        for b in self._bodies():
            self.assertEqual(b["SentOn"], b["CreatedOn"])

    def test_a_missing_sent_on_is_not_invented(self):
        gen = self.make_gen()
        inv = _invoice(1)
        inv["SentOn"] = None
        gen._schedule_invoice_dates([inv], "B1", {1: inv["CoworkerId"]},
                                    lambda e, f=None: [inv] if e == "coworkerinvoices" else [],
                                    self.update_fn)
        self.assertNotIn("SentOn", self.updates[0][2])

    def test_already_scheduled_invoices_are_read_not_rewritten(self):
        gen = self.make_gen()
        gen.already_created = lambda *a, **k: True
        issued = datetime(2025, 3, 28, 9, 0, tzinfo=timezone.utc)
        inv = {**_invoice(1), "CreatedOn": to_utc_str(issued),
               "DueDate": to_utc_str(issued + timedelta(days=3))}
        schedule = gen._schedule_invoice_dates(
            [inv], "B1", {1: inv["CoworkerId"]},
            lambda e, f=None: [inv] if e == "coworkerinvoices" else [],
            self.update_fn)
        self.assertEqual(self.updates, [], "an already-scheduled invoice must not be re-dated")
        self.assertEqual(schedule[inv["Id"]]["invoiced_on"], issued,
                         "its dates still have to reach the payment step")


class TestPaymentDates(_Harness):
    def setUp(self):
        self.gen = self.make_gen()
        issued = datetime(2025, 3, 28, 9, 0, tzinfo=timezone.utc)
        self.dates = {"invoiced_on": issued, "due_date": issued + timedelta(days=3)}

    def test_payments_carry_an_explicit_transaction_date(self):
        invoices = [_invoice(i) for i in range(1, 6)]
        schedule = {inv["Id"]: dict(self.dates) for inv in invoices}
        self.gen._pay_invoices("B1", invoices, schedule, self.create_fn, self.update_fn)

        bodies = [b for e, b in self.creates if e == "coworkerledgerentries"]
        self.assertEqual(len(bodies), len(invoices))
        for b in bodies:
            self.assertIn("TransactionDate", b)
            self.assertGreaterEqual(_parse(b["TransactionDate"]), self.dates["invoiced_on"])

    def test_payment_lands_around_its_own_due_date(self):
        offsets = []
        for _ in range(400):
            paid = self.gen._payment_date(self.dates)
            offsets.append((paid - self.dates["due_date"]).days)
            self.assertGreaterEqual(paid, self.dates["invoiced_on"])
            self.assertLessEqual(paid, NOW)
        self.assertTrue(any(o <= 0 for o in offsets), "some payments should be on time")
        self.assertTrue(any(o > 0 for o in offsets), "some payments should be late")
        self.assertLess(sum(1 for o in offsets if o > 0) / len(offsets), 0.5,
                        "late payers should be the minority")

    def test_payment_is_never_before_its_invoice_or_in_the_future(self):
        recent = {"invoiced_on": NOW - timedelta(days=1), "due_date": NOW + timedelta(days=2)}
        for _ in range(200):
            paid = self.gen._payment_date(recent)
            self.assertGreaterEqual(paid, recent["invoiced_on"])
            self.assertLessEqual(paid, NOW)

    def test_invoice_paid_on_is_corrected_to_the_payment_date(self):
        invoices = [_invoice(1)]
        schedule = {invoices[0]["Id"]: dict(self.dates)}
        self.gen._pay_invoices("B1", invoices, schedule, self.create_fn, self.update_fn)

        patches = [b for _e, _i, b in self.updates if "PaidOn" in b]
        self.assertEqual(len(patches), 1)
        ledger = [b for e, b in self.creates if e == "coworkerledgerentries"][0]
        self.assertEqual(patches[0]["PaidOn"], ledger["TransactionDate"])
        # The schedule rides along so a full-record PUT can't undo it.
        self.assertEqual(patches[0]["CreatedOn"], to_utc_str(self.dates["invoiced_on"]))

    def test_an_invoice_with_no_schedule_is_skipped_not_dated_now(self):
        self.gen._pay_invoices("B1", [_invoice(1)], {}, self.create_fn, self.update_fn)
        self.assertEqual(self.creates, [])

    def test_created_on_is_dropped_after_the_api_rejects_it(self):
        attempts = []

        def _create(entity, body):
            attempts.append(dict(body))
            if "CreatedOn" in body:
                raise RuntimeError("CreatedOn is read-only")
            return {"Id": f"NEW-{len(attempts)}"}

        invoices = [_invoice(i) for i in range(1, 4)]
        schedule = {inv["Id"]: dict(self.dates) for inv in invoices}
        self.gen._pay_invoices("B1", invoices, schedule, _create, self.update_fn)

        self.assertEqual(sum(1 for a in attempts if "CreatedOn" in a), 1,
                         "CreatedOn should be probed once, then dropped for the rest of the run")
        self.assertEqual(sum(1 for a in attempts if "CreatedOn" not in a), 3,
                         "all three payments should still be made")
        for a in attempts:
            self.assertIn("TransactionDate", a)


if __name__ == "__main__":
    unittest.main()
