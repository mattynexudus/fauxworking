# Nexudus Test Data — Agent Instructions

## What this repo does

Generates and seeds realistic test data into a Nexudus instance via MCP tools.
The data populates all reports and dashboards defined in the reports repo.

## Key commands

- `python generators/00_reference.py` — Tax rates, financial accounts, resource types
- `python generators/01_structural.py` — Tariffs, products, resources, desks, inventory, discounts, CRM boards
- `python generators/02_people.py` — Coworkers (with engagement fields) and visitors
- `python generators/03_contracts.py` — Contracts (with ContractTerm), deposits, freezes, occupancy
- `python generators/04_activity.py` — Bookings (recurring + products + guests), check-ins, credits, passes
- `python generators/05_community.py` — Deliveries, events, help desk, threads, blogs, tasks
- `python generators/06_financial.py` — Trigger invoices, pay/void/credit, ledger supplements
- `python generators/07_crm_proposals.py` — Opportunities, proposals (accept some → auto contracts)
- `python generators/daily_update.py` — Create today's check-ins, bookings, visitors, deliveries
- `python teardown.py` — Delete every tracked record (agent-run; see rule 6)
- `bash scripts/seed_all.sh` — Run all generators in order
- `bash scripts/daily.sh` — Run daily update (fresh records for today)
- `bash scripts/verify.sh` — Count tracked records against config.py targets
- `bash scripts/teardown.sh` — Confirmation prompt + dry-run preview; live deletion needs an agent (see `teardown.py`)

## Standing rules

1. **All dates are rolling** — derived from `config.TODAY`. Never hardcode dates.
2. **Check idempotency** — before creating, query for existing records with same key.
3. **Respect dependency order** — never create a child before its parent.
4. **Use config.py** for all magic numbers (volumes, dates, business IDs).
5. **Dates must be UTC with Z suffix** — use `config.to_utc_str()`.
6. **Never delete production data** — teardown only deletes IDs logged in `data/created-ids/<entity>.json`, never by name pattern. Note: entities only mutable via commands (e.g. `coworkerinvoices` void/credit-note records — see rule 12) have no plain delete path other than `COWORKER_INVOICE_DELETE`, which `teardown.py` does not currently special-case; it may error or no-op on these tracked entries. Not yet exercised live.
7. **No `[TEST]` name prefixes** — records should look like real data (reports/dashboards are demoed off this). `TEST_NAME_PREFIX` in `config.py` is `""`. The only marker baked into a record is the coworker email (`test-{id}@seeddata.local`); everything else is identified purely by its tracked ID.
8. **No "whoami" MCP tool** — resolve business/currency/country/timezone via `nexudus_list("businesses", {})`, and the admin user (for `IssuedById`/`ResponsibleId`) via `nexudus_list("users", {})` filtered to `IsAdmin=true`.
9. **Log all created IDs** — append to `data/created-ids/<generator>.json` (one file per generator, not per Nexudus entity — each record is tagged with an `"entity"` field; see `teardown.py`).
10. **One MCP call at a time** — do not parallelise Nexudus calls.
11. **Cancelled bookings** — create the booking, then cancel it via `nexudus_run_command("bookings", "CANCEL_BOOKING", [id], parameters=[{"Name": "Cancellation Reason", "Value": <eBookingCancellationReason>}, {"Name": "Cancel without applying cancellation fee rules", "Value": true}])`. Confirmed live: a raw delete leaves `CancellationReason` at 0/Unknown. Reason enum values in use: NoLongerNeeded=1, TooExpensive=2, RebookedForADifferentTime=4, FailedToPayUpfront=5, NotCheckedIn=8 (see `CANCELLATION_REASON_MAP` in `generators/04_activity.py`).
12. **Invoices** — cannot be created directly; raise via `nexudus_run_command("coworkers", "COWORKER_BILL_RUN", [coworkerId, ...])`, which bills that coworker's active contract(s) plus any pending item sales (see rule 26). `coworkerinvoices` *does* support commands — confirmed live, despite not appearing under command discovery (see rule 27):
    - `VOID_INVOICE` (no parameters) — sets `Void=true` on the same record.
    - `COWORKER_INVOICE_CANCEL` (parameters: an `"Amount{invoiceId}"`-named field — the invoice ID is baked into the parameter *name*, not a separate value — plus `Preview` and `DoNotApplyCreditAutomatically`) — creates a *new*, separate negative-amount invoice with `CreditNote=true`, linked back via `OriginalInvoiceGuid`. This is credit-noting, not voiding.
    - `COWORKER_INVOICE_DELETE` — genuinely deletes the invoice (404 on follow-up GET). Not equivalent to void — it erases the audit trail. Don't use it for void/credit-note scenarios.
    - `COWORKER_INVOICE_REFUND` (parameters: `Amount{invoiceId}`, `Preview`, `ePaymentProvider0`) — exists; not yet used by any generator.
    Booking charges use `nexudus_run_command("bookings", "CHARGE_BOOKING", [bookingId])`, which sets `Booking.Invoiced=true` and creates the linked charge — not a standalone `CoworkerExtraService` with a manually-set `BookingId`.
13. **Proposals** — create at status=1 (Draft). Accepting requires commands, not a direct field update: `nexudus_run_command("proposals", "PROPOSAL_SEND", [id])` then `nexudus_run_command("proposals", "PROPOSAL_ACCEPT", [id])`. Confirmed live: a direct `ProposalStatus` update to 3 fails with "Accepted proposals cannot be changed," even on a brand-new Draft. Neither command appears under command discovery (see rule 27).
14. **Financial accounts** — assign to Products, ExtraServices, and Tariffs at creation time.
15. **Tax rates** — assign to Products, ExtraServices, and Tariffs at creation time.
16. **Discount codes** — apply via Proposal.DiscountCodeId or Booking.DiscountCode text field.
17. **Recurring bookings** — `Repeats` is schema-required even for one-offs (send `1`/Daily with `RepeatEvent=false`); set `RepeatEvent=true` + `RepeatEvery` + `RepeatUntil` for real series. `CalendarEvent` uses a different, more granular enum (`eCalendarEventRepeatCycle`) than `Booking`'s — don't assume they match.
18. **Booking products** — `BookingProducts` is a genuine inline child on `Booking.create` (`[{ProductId, Quantity}]`) — no separate call needed.
19. **Booking guests** — create `BookingVisitor` standalone with a real `VisitorId`, not via Booking's inline `BookingVisitors` (that path writes `VisitorFullName`/`VisitorEmail`, which are read-only on the standalone entity — ambiguous whether/how it resolves a Visitor).
20. **Daily update** — run `daily_update.py` for fresh daily records. Unlike the layer generators, it resolves its own context live (no `prev_output` chain) since it's meant to run standalone, repeatedly, long after the one-time seed.
21. **Events** — `CalendarEvent` has no inline children. Create order is `CalendarEvent` → `EventProduct` (a ticket type is **required** before any attendee, not optional) → `EventAttendee`. `OnlyForMembers` events reject `EventAttendee` creation for a coworker whose membership isn't *currently* active — contract cancelled, or in a currently-paused freeze (`CoworkerContract.InPausedPeriod`) — with `"You cannot purchase this product"`, confirmed live. This is enforced against the coworker's actual contract state, not the `Coworker.CanPurchaseEvents` flag (which stays `true` regardless). When generating attendees for such events, only pick from coworkers with a currently-active, unfrozen contract.
22. **Deliveries** — create as pending, then update `Collected=true` + `CollectedOn` (or `Forwarded`/`Recycled`/`ReturnedToSender` + their `*On` field) to mark an outcome.
23. **Community threads/messages** — require `UserId`, a field distinct from `CoworkerId`. Seeded coworkers have no linked User account (`Coworker.UserId` is `updateOnly`, not populated by creation), so the resolved admin user's ID is used as `UserId` throughout, with `CoworkerId` set alongside for attribution.
24. **Engagement fields** — `ChurnProbability`/`EngagementLevel` on `Coworker` are confirmed live: visible on GET, round-trip correctly.
25. **ContractTerm** — set on ~30% of contracts. This is the minimum term end date (start + N months), not a duration field.
26. **Item-sale invoicing** — `Booking`, `CoworkerExtraService`, and `CoworkerProduct` all support `InvoiceThisCoworker` (bill this person, not their team's payer). Set it `True` on all of them so their charges get swept into that coworker's next invoice by `COWORKER_BILL_RUN`, alongside their plan fee — this is the "item sales" invoicing path referenced in the original design, distinct from contract renewal itself.
27. **Command discovery is unreliable** — `nexudus_list_commands` and `GET .../commands?id=X` have both produced false negatives, confirmed live: missing `VOID_INVOICE` for `coworkerinvoices`, missing all of `proposals`' commands. If an entity's obvious field-update path fails with a vague error (e.g. "cannot be changed"), don't conclude the action is unsupported — check whether the entity has a `runcommand` endpoint, and if discovery doesn't surface what's needed, capture the real admin UI's network request instead.
