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
2. Lists the resulting `coworkerinvoices` and marks ~60% of them paid by
   creating a `CoworkerLedgerEntry` linked via `CoworkerInvoiceId`.
3. Voids ~5 via the `VOID_INVOICE` command and issues a credit note
   against ~10 more via the `COWORKER_INVOICE_CANCEL` command (see below)
   — both real admin actions, not a field flip or a narration record.
4. Refunds ~5 already-paid invoices via the `COWORKER_INVOICE_REFUND`
   command — unlike credit-note, this flips `Refunded`/`RefundedOn` on the
   *same* invoice rather than creating a new one (`RefundedAmount` stays 0
   despite the field existing — confirmed live, unexplained).
5. Creates a handful of supplemental ledger entries (manual adjustments)
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

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.base import BaseGenerator, parse_args
from config import DATA_DIR

# (description, code, debit, credit) — manual ledger adjustments unrelated
# to any invoice. Code is free text; not an API-enforced convention.
LEDGER_SUPPLEMENTS = [
    ("Goodwill credit - service disruption", "ADJU", 0, 25.00),
    ("Referral bonus credit", "ADJU", 0, 50.00),
    ("Manual correction - duplicate charge reversed", "ADJU", 0, 15.00),
    ("Loyalty credit - 12 months membership", "ADJU", 0, 30.00),
    ("Damage charge - meeting room equipment", "DMG", 75.00, 0),
]


class FinancialGenerator(BaseGenerator):
    entity_name = "financial"

    VOID_COUNT = 5
    CREDIT_NOTE_COUNT = 10
    REFUND_COUNT = 5

    def run(self, nexudus_list, nexudus_create, nexudus_update, nexudus_run_command, prev_output):
        biz = prev_output["business_id"]
        contract_defs = prev_output["contract_defs"]
        coworker_ids = prev_output["coworker_ids"]

        billing_coworker_ids = self._billable_coworker_ids(contract_defs, coworker_ids)
        self._raise_invoices(billing_coworker_ids, nexudus_run_command)
        invoices = self._list_invoices(biz, coworker_ids, nexudus_list)

        to_pay, to_void, to_credit, refund_candidates, need_refund = self._select_invoices(invoices)
        self._pay_invoices(biz, to_pay, nexudus_create)
        self._void_and_credit_invoices(to_void, to_credit, nexudus_run_command)
        self._refund_invoices(refund_candidates, need_refund, nexudus_run_command)
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

        to_void = candidates[:need_void]
        to_credit = candidates[need_void:need_void + need_credit]
        to_pay = candidates[need_void + need_credit:]

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

    # One massive COWORKER_BILL_RUN call across every billable coworker at
    # once is slow enough to be a real risk, not a hypothetical one —
    # confirmed live: a 42-coworker batch took 28.8s (once actually
    # exceeded the client's 30s timeout outright), right at the edge of
    # producing exactly the generic, unhelpful failure this was meant to
    # diagnose. Chunking keeps each call comfortably fast and means one bad
    # or slow chunk doesn't block billing for every other coworker.
    BILL_RUN_CHUNK_SIZE = 10

    # ------------------------------------------------------------------
    # Raise invoices
    # ------------------------------------------------------------------
    def _raise_invoices(self, coworker_ids, nexudus_run_command):
        self.log.info("--- Raising invoices for %d coworkers (COWORKER_BILL_RUN) ---", len(coworker_ids))
        if not coworker_ids:
            return

        if self.dry_run:
            preview = coworker_ids[:5] + (["..."] if len(coworker_ids) > 5 else [])
            self.log.info("WOULD RUN COMMAND coworkers.COWORKER_BILL_RUN ids=%s", preview)
            return

        numeric_ids = [i for i in coworker_ids if isinstance(i, int)]
        for start in range(0, len(numeric_ids), self.BILL_RUN_CHUNK_SIZE):
            chunk = numeric_ids[start:start + self.BILL_RUN_CHUNK_SIZE]
            try:
                result = nexudus_run_command("coworkers", "COWORKER_BILL_RUN", chunk)
            except Exception as e:  # noqa: BLE001
                self.log.warning("COWORKER_BILL_RUN failed for chunk %s: %s", chunk, e, skip=True)
                continue
            self.log.info("Ran COWORKER_BILL_RUN for %d coworkers: %s", len(chunk), result)

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
            if not self.already_created("DiscoveredInvoiceId", track_key):
                self.track_id({
                    "entity": "coworkerinvoices", "Id": inv["Id"], "DiscoveredInvoiceId": track_key,
                })

        return invoices

    # ------------------------------------------------------------------
    # Pay invoices
    # ------------------------------------------------------------------
    def _pay_invoices(self, biz, invoices, nexudus_create):
        self.log.info("--- Paying %d invoices ---", len(invoices))

        for inv in invoices:
            track_key = str(inv.get("Id"))
            if self.already_created("PaidInvoiceId", track_key):
                continue

            body = {
                "BusinessId": biz,
                "CoworkerId": inv.get("CoworkerId"),
                "CoworkerInvoiceId": inv.get("Id"),
                "Description": "Payment received",
                "Code": "PAYM",
                "Debit": 0,
                "Credit": inv.get("TotalAmount", 0),
                "Balance": 0,
                "PaymentGatewayName": 11,  # Manual
            }

            if self.dry_run:
                self.log_would_create("coworkerledgerentries", body)
            else:
                result = nexudus_create("coworkerledgerentries", body)
                self.track_id({
                    "entity": "coworkerledgerentries", "Id": result["Id"], "PaidInvoiceId": track_key,
                })
                self.log.info("Paid invoice %s (id=%s)", inv.get("Id"), result["Id"])

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
    def _void_and_credit_invoices(self, to_void, to_credit, nexudus_run_command):
        self.log.info("--- Voiding %d invoices ---", len(to_void))
        for inv in to_void:
            track_key = str(inv.get("Id"))
            if self.already_created("VoidedInvoiceId", track_key):
                continue
            if self.dry_run:
                self.log.info("WOULD RUN COMMAND coworkerinvoices.VOID_INVOICE id=%s", inv.get("Id"))
                continue
            nexudus_run_command("coworkerinvoices", "VOID_INVOICE", [inv["Id"]])
            self.track_id({
                "entity": "coworkerinvoices", "Id": inv["Id"], "VoidedInvoiceId": track_key,
            })
            self.log.info("Voided invoice %s", inv["Id"])

        self.log.info("--- Issuing credit notes for %d invoices ---", len(to_credit))
        for inv in to_credit:
            track_key = str(inv.get("Id"))
            if self.already_created("CreditedInvoiceId", track_key):
                continue
            if self.dry_run:
                self.log.info("WOULD RUN COMMAND coworkerinvoices.COWORKER_INVOICE_CANCEL id=%s", inv.get("Id"))
                continue
            result = nexudus_run_command("coworkerinvoices", "COWORKER_INVOICE_CANCEL", [inv["Id"]], parameters=[
                {"Name": f"Amount{inv['Id']}", "Type": "", "Value": str(inv.get("TotalAmount", 0))},
                {"Name": "Preview", "Type": "", "Value": "false"},
                {"Name": "DoNotApplyCreditAutomatically", "Type": "", "Value": "false"},
            ])
            credit_note_id = result[0]["Id"] if result else None
            self.track_id({
                "entity": "coworkerinvoices", "Id": credit_note_id, "CreditedInvoiceId": track_key,
                "OriginalInvoiceId": inv["Id"],
            })
            self.log.info("Issued credit note for invoice %s (new credit note invoice id=%s)",
                          inv["Id"], credit_note_id)

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
    def _refund_invoices(self, candidates, need_refund, nexudus_run_command):
        self.log.info("--- Refunding up to %d invoices (from %d candidates) ---", need_refund, len(candidates))
        if need_refund <= 0:
            return

        refunded = 0
        for inv in candidates:
            if refunded >= need_refund:
                break
            track_key = str(inv.get("Id"))
            if self.already_created("RefundedInvoiceId", track_key):
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
                self.log.warning("Skipping refund of invoice %s — command failed: %s",
                                  inv["Id"], e, skip=True)
                continue
            self.track_id({
                "entity": "coworkerinvoices", "Id": inv["Id"], "RefundedInvoiceId": track_key,
            })
            self.log.info("Refunded invoice %s", inv["Id"])
            refunded += 1

    # ------------------------------------------------------------------
    # Ledger supplements
    # ------------------------------------------------------------------
    def _create_ledger_supplements(self, biz, coworker_ids, nexudus_create):
        self.log.info("--- Ledger Supplements (%d) ---", len(LEDGER_SUPPLEMENTS))

        for i, (desc, code, debit, credit) in enumerate(LEDGER_SUPPLEMENTS, start=1):
            track_key = str(i)
            if self.already_created("SupplementIndex", track_key):
                continue

            cw_index = ((i - 1) % 60) + 1
            coworker_id = coworker_ids.get(cw_index)
            if coworker_id is None:
                self.log.warning("Skipping ledger supplement #%d — coworker #%d was never created "
                                  "(seat limit?)", i, cw_index, skip=True)
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
            else:
                result = nexudus_create("coworkerledgerentries", body)
                self.track_id({
                    "entity": "coworkerledgerentries", "Id": result["Id"], "SupplementIndex": track_key,
                })
                self.log.info("Created ledger supplement #%d (id=%s)", i, result["Id"])


if __name__ == "__main__":
    args = parse_args()
    gen = FinancialGenerator(dry_run=args.dry_run)

    if gen.dry_run:
        mock_contract_defs = json.loads((DATA_DIR / "contracts.json").read_text())
        mock_coworker_defs = json.loads((DATA_DIR / "coworkers.json").read_text())
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
                # already-paid invoice) has candidates too.
                return [
                    {"Id": 9000 + i, "CoworkerId": f"DRY-CW-{(i % 60) + 1}",
                     "TotalAmount": round(100 + i * 3.5, 2), "Paid": i <= 3}
                    for i in range(1, 21)
                ]
            return []

        gen.run(
            nexudus_list=_mock_list,
            nexudus_create=lambda entity, body: {"Id": f"DRY-{entity}-{body.get('CoworkerInvoiceId', body.get('Description', 'x'))}"},
            nexudus_update=lambda entity, id, body: {"Id": id},
            nexudus_run_command=lambda entity, key, ids, parameters=None: {"Status": "DRY-RUN", "Count": len(ids)},
            prev_output=mock_prev,
        )
    else:
        import pipeline
        pipeline.run_up_to(6, business_id=args.business_id)
