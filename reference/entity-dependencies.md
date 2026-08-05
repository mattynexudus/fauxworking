# Entity Dependency Graph

Synced from test-data-seeding-strategy.md §3 — that doc is the source of
truth; regenerate this file from there if they drift.

```
Layer 0 — Reference  ✅ (00_reference.py)
├── Business (query via nexudus_list("businesses", {}) — no dedicated "whoami" MCP tool)
├── TaxRate × 3
├── FinancialAccount × 8
├── ResourceType × 5
└── AdminUserId (resolved from nexudus_list("users", {}) — needed for IssuedById on
    CoworkerContract/Proposal in later layers; not in the original plan)

Layer 1 — Structural Setup  ✅ (01_structural.py)
├── Team × 5
├── Tariff × 8 (refs: Business, TaxRate, FinancialAccount)
├── Product × 12 (refs: Business, TaxRate, FinancialAccount)
├── ExtraService × 7 (refs: Business, ResourceType, TaxRate, FinancialAccount)
│   — includes a Printing Credit rate, added to cover §4e (missing from the original plan)
├── TimePass × 4 (refs: Business)
├── Resource × 20 (refs: Business, ResourceType)
├── FloorPlan × 3 (refs: Business)
├── FloorPlanDesk × 40 (refs: FloorPlan)
├── InventoryAsset × 15 (refs: Business, Resource or FloorPlanDesk)
├── DiscountCode × 6 (refs: Business, Tariffs, Products, ResourceTypes)
├── CrmBoard × 2 (refs: Business)
├── CrmBoardColumn × 10 (refs: CrmBoard)
├── BusinessTimeSlot × 3 (refs: Business)
├── HelpDeskDepartment × 3 (refs: Business)
├── CommunityGroup × 3 (refs: Business)
└── CalendarEventCategory × 4 (refs: Business)

Layer 2 — People  ✅ (02_people.py)
├── Coworker × 60 (refs: Business, Team) — set engagement fields
└── Visitor × 60 (refs: Business, Coworker as host)

Layer 3 — Contracts & Occupancy  ✅ (03_contracts.py)
├── CoworkerContract × 90 (refs: Coworker, Tariff, Business, AdminUserId) — 27 with ContractTerm,
│   20 with Value ≠ Price. ContractSchedule (8) is attached INLINE at create time via the
│   `ContractSchedules` child array — it's the only inline child CoworkerContract supports;
│   ContractDeposit and ContractPausedPeriod are NOT inline and need separate calls.
├── ContractProduct × 30 (refs: CoworkerContract, Product)
├── ContractPausedPeriod × 12 (refs: CoworkerContract) — dates aligned to month boundaries
├── ContractDeposit × 10 (refs: CoworkerContract, Product)
├── CoworkerInventoryAsset × 12 (refs: Coworker, InventoryAsset)
└── FloorPlanDesk.CoworkerId updates × 28 (refs: FloorPlanDesk, Coworker; also sets Available=false)

Layer 4a — Activity  ✅ (04_activity.py)
├── Booking × 240 (refs: Coworker, Resource) — 10 recurring, includes BookingProducts inline (~36)
├── BookingVisitor × ~82 guest links (refs: Booking, Visitor) — created STANDALONE with a real
│   VisitorId, not via Booking's inline `BookingVisitors` (that path writes VisitorFullName/
│   VisitorEmail, which are read-only on the standalone entity — ambiguous, avoided)
├── Cancel bookings × 40 (nexudus_delete after products/guests exist → CancelledBooking
│   auto-created; recurring bookings are never in this set)
├── CheckIn × 300 (refs: Coworker, Business)
├── CoworkerExtraService × 80 (refs: Coworker, ExtraService, Booking) — 47 booking charges +
│   25 time credits + 8 printing credits
├── CoworkerBookingCredit × 25 (refs: Coworker, Business) — moved here from Layer 5; no
│   dependency on invoices, fits naturally with the other credit/pass entities
├── CoworkerBookingCreditUseHistory × 50 (refs: CoworkerBookingCredit, Booking) — moved here
│   from Layer 5 for the same reason
├── CoworkerTimePass × 40 (refs: Coworker, TimePass) — 20 get a follow-up nexudus_update
│   to set Used=true (Used is updateOnly, not settable at create)
└── CoworkerProduct × 20 (refs: Coworker, Product)

Layer 4b — Community  ✅ (05_community.py)
├── CoworkerDelivery × 40 (refs: Coworker, Business)
├── CalendarEvent × 20 (refs: Business, Resource, CalendarEventCategory) — no inline children;
│   create order is CalendarEvent -> EventProduct -> EventAttendee
├── EventProduct × 20 (refs: CalendarEvent) — one ticket type per event, REQUIRED before any
│   EventAttendee (not optional, despite the original plan treating it as an add-on)
├── EventAttendee × ~60 (refs: CalendarEvent, EventProduct, Coworker) — ~70% linked to a
│   seeded coworker (FullName/Email pulled from their record, still required fields even
│   with CoworkerId set), rest ad-hoc guest names
├── HelpDeskMessage × 25 (refs: Coworker, Business, HelpDeskDepartment)
├── CommunityThread × 15 (refs: Coworker, CommunityGroup, Business, UserId) — UserId is a
│   required FK distinct from CoworkerId; uses the resolved admin user (see §4t)
├── CommunityMessage × 40 (refs: CommunityThread, Coworker)
├── BlogPost × 10 (refs: Business)
└── CoworkerTask × 20 (refs: Coworker, Business)

Layer 5 — Financial & CRM  ✅ (06_financial.py, 07_crm_proposals.py)
├── Raise invoices: nexudus_run_command("coworkers", "COWORKER_BILL_RUN", [...]) for every
│   coworker with an active contract — NOT a command on coworkercontracts/coworkerinvoices,
│   neither of which supports commands at all. Sweeps in plan fee + any InvoiceThisCoworker=true
│   item sales (bookings/extra services/products from Layer 4a). Invoice count is server-determined,
│   discovered live via nexudus_list rather than planned in data/*.json.
├── Pay ~60% of raised invoices → CoworkerLedgerEntry (CoworkerInvoiceId + Credit=amount)
├── Void 5 + credit-note 10 more → CoworkerInvoiceHistory (audit-trail action against the
│   invoice, since Void/CreditNote/Paid are read-only on coworkerinvoices itself) + an
│   offsetting CoworkerLedgerEntry for the balance impact
├── 5 supplemental CoworkerLedgerEntry rows for edge cases (unrelated to any invoice)
├── CrmOpportunity × 30 (refs: CrmBoardColumn, Coworker) — placed directly on its target
│   stage's column; WonOn/LostOn set explicitly since we're not driving an incremental move
│   └── CrmOpportunityHistory × ~87 (refs: CrmOpportunity, CrmBoardColumn) — full stage-path
│       trail per opportunity, not just one transition
├── Proposal × 15 (refs: Coworker, Tariff, AdminUserId) — created at status=1 (Draft), then
│   updated to its target status (rule 13). Accepted ones (5) tied to a Won opportunity's coworker
└── CoworkerDataFile × 10 (refs: Proposal via ProposalGuid) — placeholder text file, not a real document
```
