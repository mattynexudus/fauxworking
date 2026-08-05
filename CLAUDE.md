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
6. **Never delete production data** — teardown only deletes IDs logged in `data/created-ids/<entity>.json`, never by name pattern.
7. **No `[TEST]` name prefixes** — records should look like real data (reports/dashboards are demoed off this). `TEST_NAME_PREFIX` in `config.py` is `""`. The only marker baked into a record is the coworker email (`test-{id}@seeddata.local`); everything else is identified purely by its tracked ID.
8. **No "whoami" MCP tool** — resolve business/currency/country/timezone via `nexudus_list("businesses", {})`, and the admin user (for `IssuedById`/`ResponsibleId`) via `nexudus_list("users", {})` filtered to `IsAdmin=true`.
9. **Log all created IDs** — append to `data/created-ids/<generator>.json` (one file per generator, not per Nexudus entity — each record is tagged with an `"entity"` field; see `teardown.py`).
10. **One MCP call at a time** — do not parallelise Nexudus calls.
11. **Cancelled bookings** — create booking first, then delete it. System is asserted to create a CancelledBooking snapshot on delete — this convention isn't explicitly confirmed in the `bookings` entity guide text, worth a spot-check on first live run.
12. **Invoices** — cannot be created directly, and there is no command on `coworkercontracts` or `coworkerinvoices` (neither supports commands at all). Raise via `nexudus_run_command("coworkers", "COWORKER_BILL_RUN", [coworkerId, ...])` — it bills that coworker's active contract(s). **Void and credit-note are not achievable via this API surface** — `Void`/`CreditNote`/`Paid`/`PaidAmount`/`TotalAmount` are all read-only on `coworkerinvoices`, which has no create/delete/commands. Record payment via `CoworkerLedgerEntry` (`CoworkerInvoiceId` + `Credit` = amount) instead — this is expected to reconcile the invoice's Paid state but is inferred from field design, not confirmed in the guide. Ledger `Code` (e.g. `"PAYM"`) is a free-text field, not an API-enforced enum — it's a project convention.
13. **Proposals** — create at status=1, then update to status=3 to accept. The entity guide says a `ProposalContract` is created immediately alongside the Proposal and "becomes" a `CoworkerContract` when accepted — this supports the auto-creates-a-contract claim but isn't an explicit documented trigger; spot-check on first live run.
14. **Financial accounts** — assign to Products, ExtraServices, and Tariffs at creation time.
15. **Tax rates** — assign to Products, ExtraServices, and Tariffs at creation time.
16. **Discount codes** — apply via Proposal.DiscountCodeId or Booking.DiscountCode text field.
17. **Recurring bookings** — `Repeats` is schema-required even for one-offs (send `1`/Daily with `RepeatEvent=false`); set `RepeatEvent=true` + `RepeatEvery` + `RepeatUntil` for real series. `CalendarEvent` uses a different, more granular enum (`eCalendarEventRepeatCycle`) than `Booking`'s — don't assume they match.
18. **Booking products** — `BookingProducts` is a genuine inline child on `Booking.create` (`[{ProductId, Quantity}]`) — no separate call needed.
19. **Booking guests** — create `BookingVisitor` standalone with a real `VisitorId`, not via Booking's inline `BookingVisitors` (that path writes `VisitorFullName`/`VisitorEmail`, which are read-only on the standalone entity — ambiguous whether/how it resolves a Visitor).
20. **Daily update** — run `daily_update.py` for fresh daily records. Unlike the layer generators, it resolves its own context live (no `prev_output` chain) since it's meant to run standalone, repeatedly, long after the one-time seed.
21. **Events** — `CalendarEvent` has no inline children. Create order is `CalendarEvent` → `EventProduct` (a ticket type is **required** before any attendee, not optional) → `EventAttendee`.
22. **Deliveries** — create as pending, then update `Collected=true` + `CollectedOn` (or `Forwarded`/`Recycled`/`ReturnedToSender` + their `*On` field) to mark an outcome.
23. **Community threads/messages** — require `UserId`, a field distinct from `CoworkerId`. Seeded coworkers have no linked User account (`Coworker.UserId` is `updateOnly`, not populated by creation), so the resolved admin user's ID is used as `UserId` throughout, with `CoworkerId` set alongside for attribution.
24. **Engagement fields** — ⚠️ ChurnProbability/EngagementLevel unconfirmed via API. Validate first.
25. **ContractTerm** — set on ~30% of contracts. This is the minimum term end date (start + N months), not a duration field.
