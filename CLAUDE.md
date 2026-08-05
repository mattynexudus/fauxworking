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
- `bash scripts/seed_all.sh` — Run all generators in order
- `bash scripts/daily.sh` — Run daily update (fresh records for today)
- `bash scripts/verify.sh` — Count created records and validate targets

## Standing rules

1. **All dates are rolling** — derived from `config.TODAY`. Never hardcode dates.
2. **Check idempotency** — before creating, query for existing records with same key.
3. **Respect dependency order** — never create a child before its parent.
4. **Use config.py** for all magic numbers (volumes, dates, business IDs).
5. **Dates must be UTC with Z suffix** — use `config.to_utc_str()`.
6. **Never delete production data** — teardown only deletes records matching test markers.
7. **Test markers** — naming conventions:
   - Coworkers: email `test-{id}@seeddata.local`
   - Resources: name prefix `[TEST]`
   - Products: name prefix `[TEST]`
   - InventoryAssets: name prefix `[TEST]`
   - HelpDesk: subject prefix `[TEST]`
   - Events: name prefix `[TEST]`
   - BlogPosts: title prefix `[TEST]`
   - Other entities: linked to test coworkers/resources
8. **Run `nexudus whoami` first** to get DefaultBusinessId and defaults.
9. **Log all created IDs** — append to `data/created-ids/<entity>.json`.
10. **One MCP call at a time** — do not parallelise Nexudus calls.
11. **Cancelled bookings** — create booking first, then delete it. System creates CancelledBooking.
12. **Invoices** — cannot be created directly. Trigger via billing cycle commands on contracts.
13. **Proposals** — create at status=1, then update to status=3 to accept (auto-creates contract).
14. **Financial accounts** — assign to Products, ExtraServices, and Tariffs at creation time.
15. **Tax rates** — assign to Products, ExtraServices, and Tariffs at creation time.
16. **Discount codes** — apply via Proposal.DiscountCodeId or Booking.DiscountCode text field.
17. **Recurring bookings** — set `Repeats` (enum: 1=Daily, 2=Weekly, 3=Monthly) + `RepeatEvery`.
18. **Booking products** — create BookingProduct records after the booking exists.
19. **Booking guests** — create BookingVisitor records linking Booking → Visitor.
20. **Daily update** — run `daily_update.py` for fresh daily records.
21. **Events** — CalendarEvent creates the event; EventAttendee adds people to it.
22. **Deliveries** — create as pending, then update `Collected=true` + `CollectedOn` to mark collected.
23. **Community threads** — create CommunityThread, then CommunityMessage as replies.
24. **Engagement fields** — ⚠️ ChurnProbability/EngagementLevel unconfirmed via API. Validate first.
25. **ContractTerm** — set on ~30% of contracts. This is the minimum term in months.
