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
2. Lists the resulting `coworkerinvoices` and marks ~60% of them paid by
   creating a `CoworkerLedgerEntry` linked via `CoworkerInvoiceId`.
3. Creates a handful of supplemental ledger entries (manual adjustments)
   unrelated to any invoice.

**Known gap — Void / CreditNote invoice states are NOT implemented.**
`coworkerinvoices` supports list/get/update only (no create, no commands),
and `Void`, `CreditNote`, `OriginalInvoiceGuid`, `Paid`, `PaidAmount`,
`TotalAmount` are all read-only on that entity. No API path was found for
voiding an invoice or issuing a credit note through this MCP surface. The
original plan's ~5 void + ~10 credit-note invoices (§4c) are skipped rather
than approximated with something misleading.

**Unverified assumptions, flagged for a live spot-check:**
- That creating a `CoworkerLedgerEntry` with `CoworkerInvoiceId` set actually
  reconciles the invoice's (read-only) `Paid`/`PaidAmount` fields. This is
  inferred from the field design (writable ledger + FK link, but read-only
  invoice fields), not confirmed in the entity guide text.
- The ledger `Code` value `"PAYM"` is a project convention, not an
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

    PAID_FRACTION = 0.6  # ~90 of ~150 target from §4c

    def run(self, nexudus_list, nexudus_create, nexudus_update, nexudus_run_command, prev_output):
        biz = prev_output["business_id"]
        contract_defs = prev_output["contract_defs"]
        coworker_ids = prev_output["coworker_ids"]

        self.log.info(
            "NOTE: Void/CreditNote invoice states are not implemented — no supported "
            "API path was found (see module docstring). Skipping those targets."
        )

        billing_coworker_ids = self._billable_coworker_ids(contract_defs, coworker_ids)
        self._raise_invoices(billing_coworker_ids, nexudus_run_command)
        invoices = self._list_invoices(biz, nexudus_list)
        self._pay_invoices(biz, invoices, nexudus_create)
        self._create_ledger_supplements(biz, coworker_ids, nexudus_create)

        self.log.info("Layer 5 Financial complete. Billed coworkers: %d, invoices seen: %d",
                      len(billing_coworker_ids), len(invoices))
        return {**prev_output}

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
    def _raise_invoices(self, coworker_ids, nexudus_run_command):
        self.log.info("--- Raising invoices for %d coworkers (COWORKER_BILL_RUN) ---", len(coworker_ids))
        if not coworker_ids:
            return

        if self.dry_run:
            preview = coworker_ids[:5] + (["..."] if len(coworker_ids) > 5 else [])
            self.log.info("WOULD RUN COMMAND coworkers.COWORKER_BILL_RUN ids=%s", preview)
            return

        numeric_ids = [i for i in coworker_ids if isinstance(i, int)]
        result = nexudus_run_command("coworkers", "COWORKER_BILL_RUN", numeric_ids)
        self.log.info("Ran COWORKER_BILL_RUN: %s", result)

    # ------------------------------------------------------------------
    # Discover raised invoices
    # ------------------------------------------------------------------
    def _list_invoices(self, biz, nexudus_list):
        self.log.info("--- Listing generated invoices ---")
        # Single call via the abstracted nexudus_list(entity, filters) signature
        # used throughout this codebase — it does not expose pageSize/page here.
        # If a live run raises more invoices than one page returns, the agent
        # driving this generator should page through coworkerinvoices manually
        # via the real MCP tool before calling _pay_invoices.
        invoices = nexudus_list("coworkerinvoices", {"CoworkerInvoice_Business": biz})
        self.log.info("Found %d invoices", len(invoices))
        return invoices

    # ------------------------------------------------------------------
    # Pay a fraction of them
    # ------------------------------------------------------------------
    def _pay_invoices(self, biz, invoices, nexudus_create):
        target_count = round(len(invoices) * self.PAID_FRACTION)
        self.log.info("--- Paying %d of %d invoices (~%.0f%%) ---",
                      target_count, len(invoices), self.PAID_FRACTION * 100)

        for inv in invoices[:target_count]:
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
    # Ledger supplements
    # ------------------------------------------------------------------
    def _create_ledger_supplements(self, biz, coworker_ids, nexudus_create):
        self.log.info("--- Ledger Supplements (%d) ---", len(LEDGER_SUPPLEMENTS))

        for i, (desc, code, debit, credit) in enumerate(LEDGER_SUPPLEMENTS, start=1):
            track_key = str(i)
            if self.already_created("SupplementIndex", track_key):
                continue

            cw_index = ((i - 1) % 60) + 1
            body = {
                "BusinessId": biz,
                "CoworkerId": coworker_ids.get(cw_index),
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
                # generation can't be simulated offline.
                return [
                    {"Id": 9000 + i, "CoworkerId": f"DRY-CW-{(i % 60) + 1}",
                     "TotalAmount": round(100 + i * 3.5, 2)}
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
        print("Live mode requires MCP context. Run via agent or use --dry-run.")
        sys.exit(1)
