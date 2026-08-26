"""
Refresh output/ — pulls in what Nexudus has generated on its own since the
last seed/teardown run, and adds a coworkerinvoicelines export.

Read-only against Nexudus except for one thing: tracking newly-discovered
invoices into data/created-ids/financial.json, the same way
06_financial.py::_list_invoices already does — so a later teardown run can
still find and delete them. Nothing here creates, updates, voids, or
deletes anything live.

Two gaps this closes:

1. CoworkerInvoice keeps growing outside our control. Nexudus raises
   invoices for a coworker's recurring contract on its own over time (see
   generators/06_financial.py::_select_invoices — confirmed live the
   tracked pool grows run over run with no code change on our end). Every
   other CSV is a pure re-derivation of local tracking (report_lib.
   write_entity_csvs), so output/coworkerinvoices.csv only ever reflects
   what was tracked as of the last time a generator actually ran — it goes
   stale the moment real time passes between runs. This re-runs the exact
   same discovery FinancialGenerator._list_invoices uses (same
   "DiscoveredInvoiceId" tracking convention) against the live account,
   then re-exports every entity's CSV from the now-current tracking.

2. CoworkerInvoiceLine (an invoice's individual line items) was never
   exported at all. output/coworkerinvoices.csv has no line-level detail —
   CLAUDE.md rule 36: InvoiceLines isn't populated on the invoice record
   itself, list or single-GET — and this project has no create/delete path
   of its own for these (they're a side effect of raising an invoice), so
   there's nothing to track for teardown either. Exported fresh, live,
   every run into output/coworkerinvoicelines.csv, scoped to invoices
   already tracked under our own coworkers — never the whole account (see
   CLAUDE.md rule 46 on pre-existing real data mixed into this business).

Usage:
    python refresh_output.py                   # live
    python refresh_output.py --business-id ID  # for logins with access to
                                                 # more than one business
"""

import argparse
import csv
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
import nexudus_client as client
import pipeline
import report_lib

FinancialGenerator = importlib.import_module("generators.06_financial").FinancialGenerator


def _load_tracked(filename, entity):
    path = config.CREATED_IDS_DIR / filename
    if not path.exists():
        return []
    return [r for r in json.loads(path.read_text(encoding="utf-8")) if r.get("entity") == entity]


def _write_invoice_lines_csv(invoices, coworker_ids):
    our_invoice_ids = sorted({inv["Id"] for inv in invoices if inv.get("CoworkerId") in set(coworker_ids.values())})
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.OUTPUT_DIR / "coworkerinvoicelines.csv"

    if not our_invoice_ids:
        print("No tracked invoices to pull line items for — writing an empty coworkerinvoicelines.csv")
        out_path.write_text("Id\n", encoding="utf-8")
        return

    # coworkerinvoicelines has no blanket listing — confirmed live it 400s
    # with "Missing required filter: CoworkerInvoiceLine_CoworkerInvoice",
    # unlike coworkerinvoices' business-scoped listing. So this is
    # inherently one nexudus_list call per invoice, not a single bulk call
    # filtered client-side like _list_invoices does for coworkerinvoices.
    print(f"--- Listing invoice lines for {len(our_invoice_ids)} tracked invoices (one call each) ---")
    ours = []
    for i, invoice_id in enumerate(our_invoice_ids, start=1):
        lines = client.nexudus_list(
            "coworkerinvoicelines", {"CoworkerInvoiceLine_CoworkerInvoice": invoice_id})
        ours.extend(lines)
        if i % 25 == 0 or i == len(our_invoice_ids):
            print(f"  {i}/{len(our_invoice_ids)} invoices checked, {len(ours)} lines so far")
    print(f"Found {len(ours)} invoice lines across {len(our_invoice_ids)} tracked invoices")

    fieldnames = ["Id"] + sorted({k for r in ours for k in r if k != "Id"})
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ours)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--business-id", type=int, default=None,
                        help="Which business/location to refresh, if this login has access to more than one")
    args = parser.parse_args()

    biz = pipeline._select_business(pipeline.list_businesses(), args.business_id)["Id"]
    print(f"Using business id={biz}")

    coworkers = _load_tracked("people.json", "coworkers")
    if not coworkers:
        raise SystemExit("No tracked coworkers found in data/created-ids/people.json — nothing to refresh against.")
    coworker_ids = {r.get("Index"): r["Id"] for r in coworkers}

    print(f"--- Discovering auto-generated invoices ({len(coworker_ids)} tracked coworkers) ---")
    gen = FinancialGenerator(dry_run=False)
    invoices = gen._list_invoices(biz, coworker_ids, client.nexudus_list)

    print("--- Refreshing output/ CSVs from current tracking ---")
    report_lib.write_entity_csvs(config.OUTPUT_DIR)

    _write_invoice_lines_csv(invoices, coworker_ids)

    print(f"\nDone — see {config.OUTPUT_DIR}")


if __name__ == "__main__":
    main()
