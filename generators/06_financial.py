"""
Layer 5 — Financial

Orchestrates invoice generation and payment. Unlike every other generator,
this one has **no data/*.json file** — there's no faker-driven identity data
to precompute here; invoice IDs and amounts don't exist until Nexudus
generates them server-side, so this script discovers them live via
nexudus_list rather than reading a pre-planned definition file.

What it does:
1. Finds coworkers with an active (non-cancelled) contract from Layer 3's
   output and raises their next invoice via the `COWORKER_BILL_RUN` command
   on the `coworkers` entity (there is no create endpoint or command on
   `coworkercontracts`/`coworkerinvoices` themselves — see gotchas below).
   Bookings, CoworkerExtraServices, and CoworkerProducts are all created
   with `InvoiceThisCoworker=True` (04_activity.py) so their charges are
   swept into that coworker's next invoice alongside their plan fee —
   this is the "item sales" invoicing path, distinct from the contract
   renewal itself.
2. Gives every discovered invoice a constructed date schedule: issued in
   the last days of some month inside the 24-month window, due
   `tariffDefaultDueDate` days later (normally 3, read from the account's
   own business settings), for a billing period covering the month that
   follows. This runs before the actions below, because each of them
   dates itself off its invoice's schedule.
3. Lists the resulting `coworkerinvoices` and marks ~60% of them paid by
   creating a `CoworkerLedgerEntry` linked via `CoworkerInvoiceId`, with an
   explicit `TransactionDate` drawn around that invoice's due date — mostly
   on time, with a tail of late payers.
4. Voids ~5 via the `VOID_INVOICE` command and issues a credit note
   against ~10 more via the `COWORKER_INVOICE_CANCEL` command (see below)
   — both real admin actions, not a field flip or a narration record. The
   credit note is itself a new invoice raised "now", so it's re-dated to a
   few days after the invoice it credits.
5. Refunds ~5 already-paid invoices via the `COWORKER_INVOICE_REFUND`
   command — unlike credit-note, this flips `Refunded`/`RefundedOn` on the
   *same* invoice rather than creating a new one (`RefundedAmount` stays 0
   despite the field existing — confirmed live, unexplained).
   `RefundedOn` is then moved to a few days after the payment it reverses.
6. Creates a handful of supplemental ledger entries (manual adjustments)
   unrelated to any invoice.

**Void / credit note — via commands, found by capturing the real admin UI's
network requests, not by discovery.** `Void`/`CreditNote` on
`coworkerinvoices` are read-only and that entity has no direct create, so
there's no way to flip them with a plain update. An earlier version of this
generator tried recording the intent via `CoworkerInvoiceHistory` +
`CoworkerLedgerEntry` (narration + balance impact, mirroring how a ledger
entry reconciles `Paid`) — confirmed live that doesn't work: the ledger
entry does flip `Paid: true` regardless of intent, but `Void`/`CreditNote`
never move, so a "voided" invoice was indistinguishable from a paid one.

The real mechanism is per-entity commands, and — same lesson as
`PROPOSAL_ACCEPT` in 07_crm_proposals.py — **discovery isn't reliable**:
neither `VOID_INVOICE` nor `COWORKER_INVOICE_CANCEL` showed up via
`GET .../coworkerinvoices/commands?id=X` for this account, only by
capturing what the admin UI's browser actually sent:
- `VOID_INVOICE` — no parameters. Sets `Void=true` on the *same* record.
  `COWORKER_INVOICE_DELETE` also exists and does something similar-sounding
  but genuinely deletes the invoice outright — confirmed live via a 404 on
  a follow-up GET. That's not a void, it's erasure; it would make the
  invoice vanish from any report entirely, defeating the point of having a
  "voided" bucket. Deliberately not used here.
- `COWORKER_INVOICE_CANCEL` — creates a *new*, separate invoice with a
  negative `TotalAmount` and `CreditNote=true`, linked back to the
  original via `OriginalInvoiceGuid` — the real double-entry credit-note
  pattern, not a flag on the original invoice. Confirmed live the command
  response is a list containing that new invoice record. Needs an
  `"Amount{invoiceId}"` parameter — the invoice's numeric ID is baked into
  the parameter *name*, not just passed as a normal value — plus `Preview`
  and `DoNotApplyCreditAutomatically` (tested both `true` and `false` for
  the latter; makes no observable difference for a single invoice, kept as
  `false` to match the captured request). The resulting credit-note
  invoice's ID is tracked via `OriginalInvoiceId` alongside it.
- `COWORKER_INVOICE_REFUND` — unlike credit-note, this does NOT create a
  new invoice: it flips `Refunded=true` + `RefundedOn` on the *same*
  record (`Paid` stays `true`) — confirmed live via a follow-up GET.
  `RefundedAmount` stays `0` despite the field existing on the schema —
  confirmed live, unexplained; don't rely on it. Only valid on an
  already-paid invoice. Same
  `"Amount{invoiceId}"` naming convention as credit-note, plus `Preview`
  and `ePaymentProvider0` (kept at `"994"`, the value captured from a real
  admin-UI refund — meaning of that specific code is otherwise undocumented).
  The `nexudus_run_command` MCP tool itself refuses this entity ("does not
  support run-command") even though the real API accepts it — the same
  false-negative pattern as `VOID_INVOICE`, just enforced client-side this
  time instead of only in discovery. Verified working through this
  project's own direct REST client, not through that MCP tool.

**Other notes:**
- Creating a `CoworkerLedgerEntry` with `CoworkerInvoiceId` set does
  reconcile the invoice's (read-only) `Paid`/`PaidAmount` fields —
  confirmed live, this part of the original design was right.
- The ledger `Code` values (`"PAYM"`) are a project convention, not an
  API-enforced enum — `Code` is a free-text field on `CoworkerLedgerEntry`.
- Whether `COWORKER_BILL_RUN` on a coworker with multiple active contracts
  bills all of them or just one is undocumented.

Prerequisites: Layer 0 (business), Layer 3 (contract_defs — to know who has
an active contract).

Usage:
    python generators/06_financial.py              # Live mode
    python generators/06_financial.py --dry-run     # Log only
"""

import calendar
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dateutil.relativedelta import relativedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.base import BaseGenerator, parse_args
from config import (DATA_DIR, DEFAULT_INVOICE_DUE_DAYS, NOW, TODAY, WINDOW_MONTHS,
                    to_utc_str)

# (description, code, debit, credit) — manual ledger adjustments unrelated
# to any invoice. Code is free text; not an API-enforced convention.
LEDGER_SUPPLEMENTS = [
    ("Goodwill credit - service disruption", "ADJU", 0, 25.00),
    ("Referral bonus credit", "ADJU", 0, 50.00),
    ("Manual correction - duplicate charge reversed", "ADJU", 0, 15.00),
    ("Loyalty credit - 12 months membership", "ADJU", 0, 30.00),
    ("Damage charge - meeting room equipment", "DMG", 75.00, 0),
]

# When a payment lands relative to its invoice's due date, as
# (weight, earliest offset, latest offset) in days. Most members pay on
# time — a few days before the due date or on it — with a thinning tail of
# late payers, so aging / days-to-pay / overdue reports have a realistic
# shape instead of every payment sitting on one date. See _payment_date.
PAYMENT_TIMING_BUCKETS = [
    (50, -3, 0),    # on time, in the days running up to the due date
    (22, 0, 0),     # exactly on the due date
    (16, 1, 7),     # a little late
    (9, 8, 21),     # properly late
    (3, 22, 45),    # chased for weeks
]

# Business-setting keys that carry the account's default invoice due-date
# offset in days. Compared case-insensitively and ignoring any dotted
# prefix, so "tariffDefaultDueDate", "Tariffs.DefaultDueDate" and
# "Billing.TariffDefaultDueDate" all match. See _default_due_days.
DUE_DATE_SETTING_KEYS = ("tariffdefaultduedate", "defaultduedate")


class FinancialGenerator(BaseGenerator):
    entity_name = "financial"

    VOID_COUNT = 5
    CREDIT_NOTE_COUNT = 10
    REFUND_COUNT = 5

    # Smallest share of an invoice pool that must be left available to pay.
    # VOID_COUNT/CREDIT_NOTE_COUNT are fixed lifetime counts while paid
    # invoices are the bulk of the design (~205 of INVOICE_TARGET's 220), so
    # taking void and credit off the top first is only harmless while the pool
    # is large. It isn't always: a run whose billing was cut short raised 9
    # invoices, and void (5) plus credit (7 outstanding) consumed all 12 —
    # `--- Paying 0 invoices ---`, no error, and then no refunds either, since
    # refunds need an already-paid invoice. See _select_invoices.
    PAID_MIN_SHARE = 0.6

    # Total coworkerinvoices this project aims to have tracked across all
    # runs — repeated nexudus_raise_invoice calls for the same coworker
    # each raise a genuinely new, distinct invoice rather than refusing
    # once "caught up" (confirmed live: it advances the contract's billing
    # period forward every time, into the future if needed), so reaching
    # a larger total is just a matter of calling it enough times. Set high
    # enough that a realistic-looking account has real invoice volume to
    # report on, not just one per coworker.
    INVOICE_TARGET = 220

    # How far back a raised invoice can be dated — reuses the same
    # WINDOW_MONTHS (24) history every other layer spreads its data across,
    # so invoice volume has the same spread as everything else instead of
    # clustering at seed time. See _schedule_invoice_dates.
    INVOICE_BACKDATE_MAX_MONTHS_AGO = WINDOW_MONTHS - 1

    # Every one of these round-tripped a changed value on a follow-up GET
    # when tested directly against a live tracked invoice (PUT to
    # coworkerinvoices) — including CreatedOn, which on every other entity
    # in this codebase has always turned out to be a read-only, system-set
    # audit timestamp. Confirmed live this is NOT the case here.
    #
    # Split by who writes them: the scheduled set is constructed up front
    # by _schedule_invoice_dates, while PaidOn/RefundedOn only exist once
    # the pay/refund actions have actually run and so are written by those
    # steps instead (see _pay_invoices / _refund_invoices).
    SCHEDULED_INVOICE_DATE_FIELDS = [
        "CreatedOn", "SentOn", "DueDate", "InvoiceFromDate", "InvoiceToDate",
    ]
    ACTION_INVOICE_DATE_FIELDS = ["PaidOn", "RefundedOn"]
    INVOICE_DATE_FIELDS = SCHEDULED_INVOICE_DATE_FIELDS + ACTION_INVOICE_DATE_FIELDS

    # A credit note is raised a few days after the invoice it credits, and
    # a refund lands a few days after the payment it reverses.
    CREDIT_NOTE_LAG_DAYS = (1, 10)
    REFUND_LAG_DAYS = (2, 14)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_target("coworkerinvoices", self.INVOICE_TARGET)
        self._due_days = None
        # Whether CoworkerLedgerEntry accepts an explicit CreatedOn on
        # create — unconfirmed, so _pay_invoices probes it on the first
        # payment and remembers the answer for the rest of the run.
        self._ledger_created_on_writable = None

    def run(self, nexudus_list, nexudus_create, nexudus_update, nexudus_run_command,
            nexudus_raise_invoice, prev_output):
        biz = prev_output["business_id"]
        contract_defs = prev_output["contract_defs"]
        coworker_ids = prev_output["coworker_ids"]

        billing_coworker_ids = self._billable_coworker_ids(contract_defs, coworker_ids)
        self._raise_invoices(biz, billing_coworker_ids, nexudus_raise_invoice)
        invoices = self._list_invoices(biz, coworker_ids, nexudus_list)

        # Dates are constructed BEFORE the pay/void/credit/refund actions,
        # not after: each of those actions needs to know when its invoice
        # was issued and when it fell due in order to date itself sensibly.
        schedule = self._schedule_invoice_dates(invoices, biz, coworker_ids,
                                                nexudus_list, nexudus_update)

        to_pay, to_void, to_credit, refund_candidates, need_refund = self._select_invoices(invoices)
        paid_on = self._pay_invoices(biz, to_pay, schedule, nexudus_create, nexudus_update)
        self._void_and_credit_invoices(to_void, to_credit, schedule,
                                       nexudus_run_command, nexudus_update)
        self._refund_invoices(refund_candidates, need_refund, schedule, paid_on,
                              nexudus_run_command, nexudus_update)
        self._create_ledger_supplements(biz, coworker_ids, nexudus_create)

        self.log.info("Layer 5 Financial complete. Billed coworkers: %d, invoices seen: %d",
                      len(billing_coworker_ids), len(invoices))
        return {**prev_output}

    def _select_invoices(self, invoices):
        """Partition invoices for paid / void / credit-note / refund, based
        on actual current state rather than position in the list.

        The invoice pool keeps growing across runs (Nexudus appears to
        raise some invoices on its own over time, independent of our
        COWORKER_BILL_RUN calls — confirmed live the total climbed from
        157 to 244 to 350 across separate runs with no code changes in
        between). The original design partitioned by *position*
        (invoices[:60%] paid, the rest void/credit candidates), which
        breaks once the pool grows: an invoice paid in an earlier run can
        shift into this run's "remainder" slice simply because new
        invoices got added ahead of it, and VOID_INVOICE/
        COWORKER_INVOICE_CANCEL both reject an already-paid invoice
        ("This command cannot be run for the CoworkerInvoice", confirmed
        live). Selecting from currently-unpaid, not-yet-actioned invoices
        only, and capping void/credit at their total target counts across
        all runs (via tracked history) rather than a fraction of
        whatever's in the pool this run, avoids that entirely.

        Void and credit are additionally capped against the size of the pool
        itself, not just their lifetime targets — see PAID_MIN_SHARE. Taking
        them off the top of a short pool left nothing to pay, which then left
        nothing to refund either.

        Refund is the mirror case: it requires an *already-paid* invoice
        (the opposite precondition from void/credit), so its candidates
        are drawn from the Paid pool instead — which on a fresh account is
        empty until a prior run's _pay_invoices has actually landed. That's
        expected: refund candidates only become available from the second
        live run onward.
        """
        tracked = self.get_tracked_ids()
        already_paid = {r["PaidInvoiceId"] for r in tracked if r.get("PaidInvoiceId")}
        already_voided = {r["VoidedInvoiceId"] for r in tracked if r.get("VoidedInvoiceId")}
        already_credited = {r["CreditedInvoiceId"] for r in tracked if r.get("CreditedInvoiceId")}
        already_refunded = {r["RefundedInvoiceId"] for r in tracked if r.get("RefundedInvoiceId")}
        actioned = already_paid | already_voided | already_credited

        candidates = [
            inv for inv in invoices
            if not inv.get("Paid") and not inv.get("Void") and not inv.get("CreditNote")
            and str(inv.get("Id")) not in actioned
        ]

        need_void = max(0, self.VOID_COUNT - len(already_voided))
        need_credit = max(0, self.CREDIT_NOTE_COUNT - len(already_credited))

        # Reserve PAID_MIN_SHARE of the pool for paying before void and credit
        # take their slices, so a small pool splits across all three instead of
        # void+credit swallowing it whole (which is what produced "Paying 0
        # invoices" from a 12-invoice pool). Whatever's left over is divided
        # between void and credit in proportion to what each still needs, so
        # neither starves the other. On an ample pool both take their full
        # remaining need and this is identical to the old fixed slicing.
        take_void, take_credit = need_void, need_credit
        actionable = len(candidates) - math.ceil(len(candidates) * self.PAID_MIN_SHARE)
        if need_void + need_credit > actionable:
            # Unreachable when both needs are 0 — actionable is never negative.
            take_void = min(need_void, round(actionable * need_void / (need_void + need_credit)))
            take_credit = min(need_credit, actionable - take_void)

        to_void = candidates[:take_void]
        to_credit = candidates[take_void:take_void + take_credit]
        to_pay = candidates[take_void + take_credit:]

        need_refund = max(0, self.REFUND_COUNT - len(already_refunded))
        refund_candidates = [
            inv for inv in invoices
            if inv.get("Paid") and not inv.get("Void") and not inv.get("CreditNote")
            and not inv.get("Refunded") and str(inv.get("Id")) not in already_refunded
        ]
        # Not sliced to need_refund here — _refund_invoices needs the full
        # pool to fall through to the next candidate when one is rejected
        # (e.g. an invoice that includes a Deposit line, see there).

        return to_pay, to_void, to_credit, refund_candidates, need_refund

    # ------------------------------------------------------------------
    # Which coworkers to bill
    # ------------------------------------------------------------------
    @staticmethod
    def _billable_coworker_ids(contract_defs, coworker_ids):
        active_indices = sorted({
            c["CoworkerIndex"] for c in contract_defs
            if c.get("CancellationDayOffset") is None or c["CancellationDayOffset"] > 0
        })
        return [coworker_ids[i] for i in active_indices if i in coworker_ids]

    # ------------------------------------------------------------------
    # Raise invoices
    # ------------------------------------------------------------------
    def _raise_invoices(self, biz, coworker_ids, nexudus_raise_invoice):
        # coworkers.COWORKER_BILL_RUN (a run_command) was the original
        # mechanism here per CLAUDE.md rule 12 — confirmed live it always
        # returns None and silently raises nothing at all, regardless of
        # how much is actually due, for every coworker tested. The real
        # mechanism, found by capturing the admin UI's own network
        # request (same technique as rule 27, a different endpoint this
        # time rather than a command-discovery gap): POST /api/billing/
        # coworkerinvoices/{business}/create/{coworker} — a dedicated
        # REST-style route, not entity CRUD or a runcommand (see
        # nexudus_client.py::nexudus_raise_invoice). Confirmed live twice:
        # once for an individual coworker's own contract fee + a pending
        # product sale, and once for a team's paying member, correctly
        # consolidating the whole team's charges per Team.
        # TransferCreditsToPayingMember (see CLAUDE.md rule 49).
        #
        # Unlike COWORKER_BILL_RUN, this endpoint is inherently one
        # coworker at a time (the id is in the URL path, not a batch
        # parameter) — no chunking to do here; nexudus_client.py's central
        # write-pacing (config.WRITE_PACING_SECONDS) already spaces these
        # calls out the same way it does every other write.
        #
        # A single call per coworker only ever produces one invoice each
        # (~42 total for this account) — nowhere near a realistic invoice
        # history. Confirmed live that calling this again for a coworker
        # who's already been billed doesn't refuse or no-op: it raises a
        # genuinely new invoice for the *next* billing period (advancing
        # the contract's InvoicedPeriod forward each time, into the future
        # if needed — see _schedule_invoice_dates for why raised-in-the-future
        # is fine, it backdates every discovered invoice across the full
        # window regardless of when it was actually raised). So instead of
        # one pass over coworker_ids, this keeps calling — round-robining
        # through every billable coworker rather than piling everything
        # onto the first few — until INVOICE_TARGET tracked invoices exist
        # or a full pass produces nothing but failures.
        #
        # Idempotent like everything else here: if a prior run already
        # reached the target, this is a no-op — re-running doesn't keep
        # padding the count past what was asked for.
        already_tracked = sum(1 for r in self.get_tracked_ids() if r.get("entity") == "coworkerinvoices")
        shortfall = max(0, self.INVOICE_TARGET - already_tracked)
        self.log.info("--- Raising invoices (%d already tracked, target %d, %d to go) ---",
                      already_tracked, self.INVOICE_TARGET, shortfall)
        if not coworker_ids or shortfall == 0:
            return

        if self.dry_run:
            preview = coworker_ids[:5] + (["..."] if len(coworker_ids) > 5 else [])
            self.log.info("WOULD RAISE up to %d invoices, round-robining coworkers=%s", shortfall, preview)
            return

        raised = 0
        attempts = 0
        # Generous cap so persistent per-record failures can't loop
        # forever, while still tolerating a reasonable number of them
        # before giving up — classify_failure's systemic detection below
        # already stops much earlier on a real account-wide condition.
        max_attempts = shortfall * 3 + len(coworker_ids)
        while raised < shortfall and attempts < max_attempts:
            coworker_id = coworker_ids[attempts % len(coworker_ids)]
            attempts += 1
            try:
                result = nexudus_raise_invoice(biz, coworker_id)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("coworkerinvoices:raise_invoice", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping invoice raising — this error has repeated several "
                        "records in a row with none succeeding — skipping the rest of them rather than spending the wall-clock time to fail on each: %s", e,
                        skip=True, entity="coworkerinvoices", reason="systemic_rate_limit")
                    return
                self.log.warning("Failed to raise invoice for coworker %s: %s", coworker_id, e,
                                  skip=True, entity="coworkerinvoices", reason="unknown_error")
                continue
            raised += 1
            self.log.info("Raised invoice %d/%d for coworker %s (id=%s, total=%s)",
                          raised, shortfall, coworker_id, result.get("Id"), result.get("TotalAmount"))
        self.log.info("Raised %d of %d targeted invoices (%d attempts, %d coworkers)",
                      raised, shortfall, attempts, len(coworker_ids))

    # ------------------------------------------------------------------
    # Discover raised invoices
    # ------------------------------------------------------------------
    def _list_invoices(self, biz, coworker_ids, nexudus_list):
        self.log.info("--- Listing generated invoices ---")
        # Single call via the abstracted nexudus_list(entity, filters) signature
        # used throughout this codebase — it does not expose pageSize/page here.
        # If a live run raises more invoices than one page returns, the agent
        # driving this generator should page through coworkerinvoices manually
        # via the real MCP tool before calling _pay_invoices.
        invoices = nexudus_list("coworkerinvoices", {"CoworkerInvoice_Business": biz})
        self.log.info("Found %d invoices", len(invoices))

        # COWORKER_BILL_RUN raises these server-side and returns nothing
        # usable (confirmed live — its response is just None), so this
        # discovery step is the only place any invoice ID is ever seen.
        # Nothing tracked them before now, which meant teardown.py had no
        # way to find (let alone delete) the vast majority of invoices
        # this tool causes to exist — only the small subset later voided/
        # credited/refunded, which get their own explicit tracking further
        # down, were ever recorded. Track every invoice here instead, but
        # only ones belonging to a coworker this tool created — never by
        # bare business-scope alone, matching the "only ever touch our own
        # tracked records" rule this project holds everywhere else (a
        # shared business could have real invoices from real coworkers
        # mixed in, and CoworkerInvoice_Business only filters by business,
        # not by coworker). Skipped entirely in dry-run — nothing here is
        # a real Id to persist, and every other track_id() call site in
        # this codebase is already gated the same way.
        if self.dry_run:
            return invoices

        our_coworker_ids = set(coworker_ids.values())
        for inv in invoices:
            if inv.get("CoworkerId") not in our_coworker_ids:
                continue
            track_key = str(inv["Id"])
            if not self.already_created("DiscoveredInvoiceId", track_key, entity="coworkerinvoices"):
                self.track_id({
                    "entity": "coworkerinvoices", **inv, "DiscoveredInvoiceId": track_key,
                })

        return invoices

    # ------------------------------------------------------------------
    # Pay invoices
    # ------------------------------------------------------------------
    def _payment_date(self, dates):
        """When this invoice gets paid — mostly on or just before its due
        date, with a thinning tail of late payers (PAYMENT_TIMING_BUCKETS).
        Never before the invoice was issued, and never in the future."""
        buckets = PAYMENT_TIMING_BUCKETS
        _weight, earliest, latest = self.rng.choices(
            buckets, weights=[b[0] for b in buckets], k=1)[0]
        paid = dates["due_date"] + timedelta(days=self.rng.randint(earliest, latest))
        paid = paid.replace(hour=self.rng.randint(8, 19), minute=self.rng.randrange(0, 60), second=0)
        return min(max(paid, dates["invoiced_on"]), NOW)

    def _pay_invoices(self, biz, invoices, schedule, nexudus_create, nexudus_update):
        """Records a payment as a CoworkerLedgerEntry, which is what
        reconciles the invoice's read-only Paid/PaidAmount/PaidOn fields.

        The entry carries an explicit TransactionDate drawn around its own
        invoice's due date — without one the API stamps it "now", which put
        every payment this tool has ever made on the seed date regardless of
        when its invoice was issued. Since the ledger create also sets the
        invoice's PaidOn to "now", that gets corrected straight afterwards.

        Returns {invoice id: payment datetime} for the refund step.
        """
        self.log.info("--- Paying %d invoices ---", len(invoices))
        paid_on = {}

        for inv in invoices:
            inv_id = inv.get("Id")
            track_key = str(inv_id)
            if self.already_created("PaidInvoiceId", track_key, entity="coworkerledgerentries"):
                continue

            dates = schedule.get(inv_id)
            if dates is None:
                self.log.warning("Skipping payment of invoice %s — it has no date schedule, so "
                                 "the payment would land at seed time", inv_id,
                                 skip=True, entity="coworkerledgerentries", reason="parent_skipped")
                continue

            paid_at = self._payment_date(dates)
            body = {
                "BusinessId": biz,
                "CoworkerId": inv.get("CoworkerId"),
                "CoworkerInvoiceId": inv_id,
                "Description": "Payment received",
                "Code": "PAYM",
                "Debit": 0,
                "Credit": inv.get("TotalAmount", 0),
                "Balance": 0,
                "PaymentGatewayName": 11,  # Manual
                "TransactionDate": to_utc_str(paid_at),
            }
            # CreatedOn is writable on coworkerinvoices (see
            # SCHEDULED_INVOICE_DATE_FIELDS) but unconfirmed here, so the
            # first payment probes it and the rest of the run follows suit.
            if self._ledger_created_on_writable is not False:
                body["CreatedOn"] = to_utc_str(paid_at)

            if self.dry_run:
                self.log_would_create("coworkerledgerentries", body)
                paid_on[inv_id] = paid_at
                continue

            try:
                result = nexudus_create("coworkerledgerentries", body)
            except Exception as e:  # noqa: BLE001
                if "CreatedOn" in body:
                    self.log.info("Retrying payment of invoice %s without an explicit CreatedOn "
                                  "(first attempt failed: %s)", inv_id, e)
                    self._ledger_created_on_writable = False
                    body.pop("CreatedOn")
                    try:
                        result = nexudus_create("coworkerledgerentries", body)
                    except Exception as e2:  # noqa: BLE001
                        e = e2
                        result = None
                else:
                    result = None

                if result is None:
                    verdict = self.classify_failure("coworkerledgerentries:pay", e)
                    if verdict == "systemic":
                        self.log.warning(
                            "Stopping invoice payment — this error has repeated several "
                            "records in a row with none succeeding — skipping the rest of them rather than spending the wall-clock time to fail on each: %s", e,
                            skip=True, entity="coworkerledgerentries", reason="systemic_rate_limit")
                        break
                    self.log.warning("Skipping payment of invoice %s — create failed: %s", inv_id, e,
                                      skip=True, entity="coworkerledgerentries", reason="unknown_error")
                    continue
            else:
                if self._ledger_created_on_writable is None and "CreatedOn" in body:
                    self._ledger_created_on_writable = True

            self.track_id({
                "entity": "coworkerledgerentries", **result, "PaidInvoiceId": track_key,
            })
            paid_on[inv_id] = paid_at

            # Creating the ledger entry stamps the invoice's PaidOn with the
            # server's "now"; put it back on the payment's own date. The
            # scheduled dates ride along so a full-record PUT can't quietly
            # undo them.
            self._patch_invoice_dates(inv_id, {
                "PaidOn": to_utc_str(paid_at),
                "CreatedOn": to_utc_str(dates["invoiced_on"]),
                "DueDate": to_utc_str(dates["due_date"]),
            }, nexudus_update, "pay")

            self.log.info("Paid invoice %s on %s (ledger id=%s)",
                          inv_id, to_utc_str(paid_at), result["Id"])

        return paid_on

    def _patch_invoice_dates(self, inv_id, body, nexudus_update, context):
        """Best-effort follow-up date correction on an invoice — a failure
        here means one invoice's dates are off, not that the action it
        followed (payment, refund, credit note) didn't happen, so it's
        logged and stepped over rather than raised."""
        if self.dry_run:
            self.log.info("WOULD UPDATE coworkerinvoices %s: %s", inv_id, json.dumps(body))
            return
        try:
            nexudus_update("coworkerinvoices", inv_id, body)
        except Exception as e:  # noqa: BLE001
            self.log.warning("Could not correct dates on invoice %s after %s: %s",
                             inv_id, context, e,
                             skip=True, entity="coworkerinvoices", reason="unknown_error")

    # ------------------------------------------------------------------
    # Void / credit note — via the real admin actions, not a field flip
    # (Void/CreditNote are read-only) and not the CoworkerInvoiceHistory +
    # CoworkerLedgerEntry combination this used to use, which turned out
    # to be pure narration with no actual effect on the invoice's state
    # (confirmed live: neither field ever moved off false that way).
    #
    # Found by capturing the real admin UI's network requests — neither
    # command showed up for this account via GET .../coworkerinvoices/
    # commands?id=X, so nexudus_list_commands-style discovery can't be
    # trusted here either (same lesson as PROPOSAL_ACCEPT):
    #   - VOID_INVOICE: no parameters, sets Void=true on the same record.
    #     Confirmed live — the record itself is preserved (unlike
    #     COWORKER_INVOICE_DELETE, which genuinely deletes it and would
    #     erase the invoice from any report, defeating the point of
    #     having a "voided" bucket at all).
    #   - COWORKER_INVOICE_CANCEL: creates a NEW negative invoice with
    #     CreditNote=true, linked back to the original via
    #     OriginalInvoiceGuid — this is the real credit-note accounting
    #     pattern, not a flag on the original. Needs an "Amount{id}"
    #     parameter (the invoice id is part of the parameter name, not
    #     just its value) plus Preview/DoNotApplyCreditAutomatically.
    # ------------------------------------------------------------------
    def _void_and_credit_invoices(self, to_void, to_credit, schedule,
                                  nexudus_run_command, nexudus_update):
        self.log.info("--- Voiding %d invoices ---", len(to_void))
        for inv in to_void:
            track_key = str(inv.get("Id"))
            if self.already_created("VoidedInvoiceId", track_key, entity="coworkerinvoices"):
                continue
            if self.dry_run:
                self.log.info("WOULD RUN COMMAND coworkerinvoices.VOID_INVOICE id=%s", inv.get("Id"))
                continue

            try:
                nexudus_run_command("coworkerinvoices", "VOID_INVOICE", [inv["Id"]])
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("coworkerinvoices:void", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping invoice voiding — this error has repeated several "
                        "records in a row with none succeeding — skipping the rest of them rather than spending the wall-clock time to fail on each: %s", e,
                        skip=True, entity="coworkerinvoices", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping void of invoice %s — command failed: %s", inv.get("Id"), e,
                                  skip=True, entity="coworkerinvoices", reason="unknown_error")
                continue

            self.track_id({
                "entity": "coworkerinvoices", **inv, "VoidedInvoiceId": track_key,
            })
            self.log.info("Voided invoice %s", inv["Id"])

        self.log.info("--- Issuing credit notes for %d invoices ---", len(to_credit))
        for inv in to_credit:
            track_key = str(inv.get("Id"))
            if self.already_created("CreditedInvoiceId", track_key, entity="coworkerinvoices"):
                continue
            if self.dry_run:
                self.log.info("WOULD RUN COMMAND coworkerinvoices.COWORKER_INVOICE_CANCEL id=%s", inv.get("Id"))
                continue

            try:
                result = nexudus_run_command("coworkerinvoices", "COWORKER_INVOICE_CANCEL", [inv["Id"]], parameters=[
                    {"Name": f"Amount{inv['Id']}", "Type": "", "Value": str(inv.get("TotalAmount", 0))},
                    {"Name": "Preview", "Type": "", "Value": "false"},
                    {"Name": "DoNotApplyCreditAutomatically", "Type": "", "Value": "false"},
                ])
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("coworkerinvoices:credit_note", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping credit note issuance — this error has repeated several "
                        "records in a row with none succeeding — skipping the rest of them rather than spending the wall-clock time to fail on each: %s", e,
                        skip=True, entity="coworkerinvoices", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping credit note for invoice %s — command failed: %s", inv.get("Id"), e,
                                  skip=True, entity="coworkerinvoices", reason="unknown_error")
                continue

            credit_note_id = result[0]["Id"] if result else None
            self.track_id({
                "entity": "coworkerinvoices",
                **(result[0] if result else {"Id": credit_note_id}),
                "CreditedInvoiceId": track_key,
                "OriginalInvoiceId": inv["Id"],
            })
            self.log.info("Issued credit note for invoice %s (new credit note invoice id=%s)",
                          inv["Id"], credit_note_id)

            # The credit note is a brand-new invoice raised "now" — date it
            # a few days after the invoice it credits, or it sits at seed
            # time while its original sits somewhere in the last two years.
            dates = schedule.get(inv["Id"])
            if credit_note_id is not None and dates is not None:
                issued = min(dates["invoiced_on"] + timedelta(
                    days=self.rng.randint(*self.CREDIT_NOTE_LAG_DAYS)), NOW)
                self._patch_invoice_dates(credit_note_id, {
                    "CreatedOn": to_utc_str(issued),
                    "DueDate": to_utc_str(issued),
                }, nexudus_update, "credit note")

    # ------------------------------------------------------------------
    # Refund — via COWORKER_INVOICE_REFUND, only valid on an already-paid
    # invoice. Unlike credit-note, this flips Refunded/RefundedOn on the
    # SAME record rather than creating a new one — confirmed live via a
    # follow-up GET (RefundedAmount stays 0 despite existing on the schema,
    # also confirmed live, unexplained). Same "Amount{id}" parameter-name
    # convention as COWORKER_INVOICE_CANCEL, plus Preview and
    # ePaymentProvider0 (kept at "994", matching the captured admin-UI
    # request). The nexudus_run_command MCP tool refuses this entity
    # outright ("does not support run-command") despite the real API
    # accepting it — verified through this project's own direct REST
    # client instead (see rule 27 in CLAUDE.md).
    #
    # An invoice can't always be refunded this way — confirmed live an
    # invoice that includes a Deposit line rejects the full-amount refund
    # with "You cannot refund X for line Deposit (#id)" (deposits have
    # their own refund path, via cancelling the parent contract — see
    # ContractDeposit.Refundable — not a generic invoice refund). There's
    # no cheap way to detect that up front (InvoiceLines isn't populated
    # on either the list or single-GET response), so this catches any
    # command failure per-invoice and falls through to the next candidate
    # rather than under-delivering on REFUND_COUNT.
    # ------------------------------------------------------------------
    def _refund_invoices(self, candidates, need_refund, schedule, paid_on,
                         nexudus_run_command, nexudus_update):
        self.log.info("--- Refunding up to %d invoices (from %d candidates) ---", need_refund, len(candidates))
        if need_refund <= 0:
            return

        refunded = 0
        for inv in candidates:
            if refunded >= need_refund:
                break
            track_key = str(inv.get("Id"))
            if self.already_created("RefundedInvoiceId", track_key, entity="coworkerinvoices"):
                continue
            if self.dry_run:
                self.log.info("WOULD RUN COMMAND coworkerinvoices.COWORKER_INVOICE_REFUND id=%s", inv.get("Id"))
                refunded += 1
                continue
            try:
                nexudus_run_command("coworkerinvoices", "COWORKER_INVOICE_REFUND", [inv["Id"]], parameters=[
                    {"Name": f"Amount{inv['Id']}", "Type": "", "Value": str(inv.get("TotalAmount", 0))},
                    {"Name": "Preview", "Type": "", "Value": "false"},
                    {"Name": "ePaymentProvider0", "Type": "", "Value": "994"},
                ])
            except Exception as e:  # noqa: BLE001
                # Deliberately no classify_failure/systemic handling here —
                # a Deposit-line rejection (see module docstring) is an
                # expected, common, per-invoice precondition failure, not a
                # signal of an account-wide condition, and this loop is
                # already designed to fall through to the next candidate
                # rather than stop.
                self.log.warning("Skipping refund of invoice %s — command failed: %s",
                                  inv["Id"], e, skip=True, entity="coworkerinvoices", reason="validation_rejected")
                continue
            self.track_id({
                "entity": "coworkerinvoices", **inv, "RefundedInvoiceId": track_key,
            })

            # RefundedOn is stamped "now" by the command — move it to a few
            # days after the payment it reverses. An invoice paid by an
            # earlier run isn't in `paid_on`, so fall back to its own
            # recorded PaidOn, then to its due date.
            refund_base = (paid_on.get(inv["Id"])
                           or self._parse_dt(inv.get("PaidOn"))
                           or (schedule.get(inv["Id"]) or {}).get("due_date"))
            if refund_base is not None:
                refunded_at = min(refund_base + timedelta(
                    days=self.rng.randint(*self.REFUND_LAG_DAYS)), NOW)
                self._patch_invoice_dates(inv["Id"], {
                    "RefundedOn": to_utc_str(refunded_at),
                    "PaidOn": to_utc_str(refund_base),
                }, nexudus_update, "refund")

            self.log.info("Refunded invoice %s", inv["Id"])
            refunded += 1

    # ------------------------------------------------------------------
    # Invoice date schedule — constructed, not shifted
    #
    # nexudus_raise_invoice always raises an invoice dated "now" (the
    # endpoint takes no date), so every invoice this tool creates would
    # otherwise cluster at whatever moment each seed run happened, which is
    # far less meaningful for any report that cares about spread over time
    # (aging, cash flow, month-over-month trends). Testing directly against
    # a live tracked invoice confirmed the whole INVOICE_DATE_FIELDS set
    # round-trips a changed value via a plain update — including CreatedOn,
    # which is a read-only system timestamp on every other entity here.
    #
    # This used to shift every populated date field by one random negative
    # delta, which preserved the invoice's internal structure but never
    # fixed it: Nexudus bills whatever period the contract is next due for,
    # so a real raised invoice came back dated 2026-08-27 for a billing
    # period starting 2025-03-31 — 17 months adrift — and a uniform shift
    # carried that adrift-ness along. Each invoice now gets a genuinely
    # constructed schedule instead:
    #   - issued in the last few days of some month inside the window,
    #   - due `_default_due_days` later (the account's own
    #     tariffDefaultDueDate setting, normally 3),
    #   - covering the month that *follows* the issue date, billed in
    #     advance, keeping whatever cycle length the plan actually has
    #     (a Flex Weekly invoice stays a 7-day period, Quarterly stays 3
    #     months) rather than forcing everything to a calendar month.
    #
    # Runs BEFORE pay/void/credit/refund and hands each of them the
    # resulting {invoice id: {invoiced_on, due_date}} map, so a payment can
    # land around its own invoice's due date rather than at seed time.
    # PaidOn/RefundedOn are therefore written by those steps, not here.
    # ------------------------------------------------------------------
    def _default_due_days(self, biz, nexudus_list):
        """Days between an invoice's date and its due date, read from the
        account's own business setting (Nexudus calls this
        tariffDefaultDueDate; normally 3).

        businesssettings' list endpoint ignores every filter param tried —
        confirmed live, see teardown.py::_fetch_counter_settings — so this
        pulls the table and filters client-side. The exact key casing and
        dotted prefix vary, so DUE_DATE_SETTING_KEYS is matched against the
        last dotted segment, lowercased, rather than compared literally.
        """
        if self._due_days is not None:
            return self._due_days

        self._due_days = DEFAULT_INVOICE_DUE_DAYS
        try:
            settings = nexudus_list("businesssettings", {"size": 200})
        except Exception as e:  # noqa: BLE001
            self.log.warning("Could not read business settings for the default due date "
                             "(%s) — falling back to %d days", e, self._due_days)
            return self._due_days

        for s in settings:
            if s.get("BusinessId") not in (None, biz):
                continue
            name = str(s.get("Name") or "")
            if name.rsplit(".", 1)[-1].lower() not in DUE_DATE_SETTING_KEYS:
                continue
            try:
                self._due_days = int(str(s.get("Value")).strip())
            except (TypeError, ValueError):
                self.log.warning("Business setting '%s' is not a whole number of days (%r) — "
                                 "falling back to %d", name, s.get("Value"), self._due_days)
                return self._due_days
            self.log.info("Invoice due date offset: %d days (from business setting '%s')",
                          self._due_days, name)
            return self._due_days

        self.log.info("No default-due-date business setting found — using %d days",
                      self._due_days)
        return self._due_days

    @staticmethod
    def _parse_dt(raw):
        """Parse an API date, always returning something UTC-aware. Nexudus
        sends a Z suffix, but a naive value coming back would otherwise blow
        up mid-run on the first comparison against NOW rather than being
        handled as the UTC it is."""
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def _period_length(self, current):
        """How long this invoice's billing period runs, taken from what
        Nexudus itself produced so a weekly plan stays weekly and a
        quarterly one stays quarterly. Lengths close to a whole number of
        months are snapped to exactly that, so monthly plans land on clean
        month boundaries instead of drifting a day either way."""
        start = self._parse_dt(current.get("InvoiceFromDate"))
        end = self._parse_dt(current.get("InvoiceToDate"))
        if start is None or end is None or end <= start:
            return relativedelta(months=1)

        days = (end - start).days
        for months, low, high in ((1, 27, 32), (3, 85, 95), (6, 175, 190), (12, 360, 372)):
            if low <= days <= high:
                return relativedelta(months=months)
        return timedelta(days=days)

    def _build_schedule(self, current, due_days):
        """Pick an issue date in the last few days of some month inside the
        window, and derive due date and billing period from it."""
        months_ago = self.rng.randint(1, self.INVOICE_BACKDATE_MAX_MONTHS_AGO)
        anchor = TODAY - relativedelta(months=months_ago)
        last_day = calendar.monthrange(anchor.year, anchor.month)[1]
        day = last_day - self.rng.randint(0, 4)

        invoiced_on = datetime(anchor.year, anchor.month, day,
                               self.rng.randint(8, 18), self.rng.randrange(0, 60), 0,
                               tzinfo=timezone.utc)
        due_date = invoiced_on + timedelta(days=due_days)

        # Billed in advance: the period covers the month that follows.
        period_start_date = date(anchor.year, anchor.month, 1) + relativedelta(months=1)
        period_start = datetime(period_start_date.year, period_start_date.month, 1,
                                tzinfo=timezone.utc)
        period_end = period_start + self._period_length(current)

        return {
            "invoiced_on": invoiced_on,
            "due_date": due_date,
            "period_start": period_start,
            "period_end": period_end,
        }

    def _schedule_invoice_dates(self, invoices, biz, coworker_ids, nexudus_list, nexudus_update):
        due_days = self._default_due_days(biz, nexudus_list)
        self.log.info("--- Scheduling invoice dates (up to %d months back, due +%d days) ---",
                      self.INVOICE_BACKDATE_MAX_MONTHS_AGO, due_days)

        if self.dry_run:
            fresh_by_id = {inv["Id"]: inv for inv in invoices}
        else:
            fresh = nexudus_list("coworkerinvoices", {"CoworkerInvoice_Business": biz})
            our_coworker_ids = set(coworker_ids.values())
            fresh_by_id = {inv["Id"]: inv for inv in fresh
                           if inv.get("CoworkerId") in our_coworker_ids}

        schedule = {}
        for inv in invoices:
            inv_id = inv.get("Id")
            track_key = str(inv_id)
            current = fresh_by_id.get(inv_id)
            if current is None:
                continue

            # Already scheduled by an earlier run — don't re-date it, but do
            # read its dates back into the map, since the pay/refund steps
            # below need a schedule for every invoice, not just this run's.
            if self.already_created("BackdatedInvoiceId", track_key, entity="coworkerinvoices"):
                invoiced_on = self._parse_dt(current.get("CreatedOn"))
                if invoiced_on is not None:
                    schedule[inv_id] = {
                        "invoiced_on": invoiced_on,
                        "due_date": (self._parse_dt(current.get("DueDate"))
                                     or invoiced_on + timedelta(days=due_days)),
                    }
                continue

            if not current.get("CreatedOn"):
                continue

            dates = self._build_schedule(current, due_days)
            body = {
                "CreatedOn": to_utc_str(dates["invoiced_on"]),
                "DueDate": to_utc_str(dates["due_date"]),
                "InvoiceFromDate": to_utc_str(dates["period_start"]),
                "InvoiceToDate": to_utc_str(dates["period_end"]),
            }
            if current.get("SentOn"):
                body["SentOn"] = to_utc_str(dates["invoiced_on"])

            if self.dry_run:
                self.log.info("WOULD UPDATE coworkerinvoices %s: %s", inv_id, json.dumps(body))
                schedule[inv_id] = dates
                continue

            try:
                nexudus_update("coworkerinvoices", inv_id, body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("coworkerinvoices:schedule", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping invoice date scheduling — this error has repeated several "
                        "records in a row with none succeeding — skipping the rest of them rather than spending the wall-clock time to fail on each: %s", e,
                        skip=True, entity="coworkerinvoices", reason="systemic_rate_limit")
                    break
                self.log.warning("Failed to schedule dates for invoice %s: %s", inv_id, e,
                                  skip=True, entity="coworkerinvoices", reason="unknown_error")
                continue

            schedule[inv_id] = dates
            # Track the record as it now is, not the pre-update snapshot —
            # output/coworkerinvoices.csv is re-derived from tracking, so
            # storing `current` unmerged would export the old dates.
            self.track_id({
                "entity": "coworkerinvoices", **current, **body, "BackdatedInvoiceId": track_key,
            })
            self.log.info("Scheduled invoice %s: issued %s, due %s, period %s -> %s",
                          inv_id, body["CreatedOn"], body["DueDate"],
                          body["InvoiceFromDate"], body["InvoiceToDate"])

        return schedule

    # ------------------------------------------------------------------
    # Ledger supplements
    # ------------------------------------------------------------------
    def _create_ledger_supplements(self, biz, coworker_ids, nexudus_create):
        self.log.info("--- Ledger Supplements (%d) ---", len(LEDGER_SUPPLEMENTS))

        for i, (desc, code, debit, credit) in enumerate(LEDGER_SUPPLEMENTS, start=1):
            track_key = str(i)
            if self.already_created("SupplementIndex", track_key, entity="coworkerledgerentries"):
                continue

            cw_index = ((i - 1) % 60) + 1
            coworker_id = coworker_ids.get(cw_index)
            if coworker_id is None:
                self.log.warning("Skipping ledger supplement #%d — coworker #%d was never created "
                                  "(seat limit?)", i, cw_index,
                                  skip=True, entity="coworkerledgerentries", reason="parent_skipped")
                continue

            body = {
                "BusinessId": biz,
                "CoworkerId": coworker_id,
                "Description": desc,
                "Code": code,
                "Debit": debit,
                "Credit": credit,
                "Balance": 0,
            }

            if self.dry_run:
                self.log_would_create("coworkerledgerentries", body)
                continue

            try:
                result = nexudus_create("coworkerledgerentries", body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("coworkerledgerentries:supplement", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping ledger supplement creation — this error has repeated "
                        "several records in a row with none succeeding — skipping the rest of them rather than spending the wall-clock time to fail on each: %s", e,
                        skip=True, entity="coworkerledgerentries", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping ledger supplement #%d — create failed: %s", i, e,
                                  skip=True, entity="coworkerledgerentries", reason="unknown_error")
                continue

            self.track_id({
                "entity": "coworkerledgerentries", **result, "SupplementIndex": track_key,
            })
            self.log.info("Created ledger supplement #%d (id=%s)", i, result["Id"])


if __name__ == "__main__":
    args = parse_args()
    gen = FinancialGenerator(dry_run=args.dry_run)

    if gen.dry_run:
        mock_contract_defs = json.loads((DATA_DIR / "contracts.json").read_text(encoding="utf-8"))
        mock_coworker_defs = json.loads((DATA_DIR / "coworkers.json").read_text(encoding="utf-8"))
        mock_coworker_ids = {c["index"]: f"DRY-CW-{c['index']}" for c in mock_coworker_defs}

        mock_prev = {
            "business_id": "DRY-BIZ-1",
            "contract_defs": mock_contract_defs,
            "coworker_ids": mock_coworker_ids,
        }

        def _mock_list(entity, filters):
            if entity == "coworkerinvoices":
                # Synthetic invoices so the payment-selection logic is
                # actually exercised in dry-run, since live invoice
                # generation can't be simulated offline. A few are
                # pre-marked Paid so the refund path (which needs an
                # already-paid invoice) has candidates too. Each carries the
                # date fields _schedule_invoice_dates reads — CreatedOn to
                # qualify at all, and a billing period whose length it
                # preserves (a weekly one every fourth invoice, so the
                # non-monthly branch gets exercised).
                from config import NOW as _now
                out = []
                for i in range(1, 21):
                    weekly = i % 4 == 0
                    out.append({
                        "Id": 9000 + i, "CoworkerId": f"DRY-CW-{(i % 60) + 1}",
                        "TotalAmount": round(100 + i * 3.5, 2), "Paid": i <= 3,
                        "CreatedOn": to_utc_str(_now),
                        "SentOn": to_utc_str(_now),
                        "DueDate": to_utc_str(_now + timedelta(days=3)),
                        "InvoiceFromDate": to_utc_str(_now),
                        "InvoiceToDate": to_utc_str(
                            _now + (timedelta(days=7) if weekly else timedelta(days=30))),
                    })
                return out
            return []

        gen.run(
            nexudus_list=_mock_list,
            nexudus_create=lambda entity, body: {"Id": f"DRY-{entity}-{body.get('CoworkerInvoiceId', body.get('Description', 'x'))}"},
            nexudus_update=lambda entity, id, body: {"Id": id},
            nexudus_run_command=lambda entity, key, ids, parameters=None: {"Status": "DRY-RUN", "Count": len(ids)},
            nexudus_raise_invoice=lambda business_id, coworker_id, options=None: {
                "Id": f"DRY-invoice-{coworker_id}", "TotalAmount": 0, "CoworkerId": coworker_id,
            },
            prev_output=mock_prev,
        )
    else:
        import pipeline
        pipeline.run_up_to(6, business_id=args.business_id)
