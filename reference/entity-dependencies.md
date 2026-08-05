# Entity Dependency Graph

```
Layer 0 — Reference (query or create once)
├── Business (query existing IDs via whoami)
├── TaxRate × 3
├── FinancialAccount × 8
└── ResourceType × 5

Layer 1 — Structural Setup
├── Team × 5
├── Tariff × 8 (refs: Business, TaxRate, FinancialAccount)
├── Product × 15 (refs: Business, TaxRate, FinancialAccount)
├── ExtraService × 6 (refs: Business, ResourceType, TaxRate, FinancialAccount)
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

Layer 2 — People
├── Coworker × 60 (refs: Business, Team)
└── Visitor × 60 (refs: Business, Coworker as host)

Layer 3 — Contracts & Occupancy
├── CoworkerContract × 90 (refs: Coworker, Tariff, Business)
├── ContractProduct × 30 (refs: CoworkerContract, Product)
├── ContractSchedule × 8 (refs: CoworkerContract)
├── ContractPausedPeriod × 12 (refs: CoworkerContract)
├── ContractDeposit × 10 (refs: CoworkerContract, Product)
├── CoworkerInventoryAsset × 12 (refs: Coworker, InventoryAsset)
└── FloorPlanDesk.CoworkerId updates (refs: FloorPlanDesk, Coworker)

Layer 4 — Activity
├── Booking × 240 (refs: Coworker, Resource)
├── BookingVisitor × 50 (refs: Booking, Visitor)
├── CheckIn × 300 (refs: Coworker, Business)
├── CoworkerExtraService × 80 (refs: Coworker, ExtraService, Booking)
├── CoworkerBookingCredit × 25 (refs: Coworker, Business)
├── CoworkerTimePass × 40 (refs: Coworker, TimePass)
├── CoworkerProduct × 20 (refs: Coworker, Product)
├── CoworkerDelivery × 40 (refs: Coworker, Business)
├── CalendarEvent × 20 (refs: Business, Resource, CalendarEventCategory)
├── EventAttendee × 60 (refs: CalendarEvent, Coworker)
├── HelpDeskMessage × 25 (refs: Coworker, Business, HelpDeskDepartment)
├── CommunityThread × 15 (refs: Coworker, CommunityGroup, Business)
├── CommunityMessage × 40 (refs: CommunityThread, Coworker)
├── BlogPost × 10 (refs: Business)
├── CoworkerTask × 20 (refs: Coworker, Business)
├── Cancel bookings × 40 (delete selected Bookings)
└── CrmOpportunity × 30 (refs: CrmBoardColumn, Coworker)
    └── CrmOpportunityHistory × 60 (refs: CrmOpportunity, CrmBoardColumn)

Layer 5 — Financial & Proposals
├── Trigger invoice generation (billing cycle commands)
├── Pay/void/credit invoices → ledger entries auto-generated
├── CoworkerBookingCreditUseHistory × 50 (refs: CoworkerBookingCredit, Booking)
├── Proposal × 15 (refs: Coworker, Tariff, CrmOpportunity)
│   ├── Accept proposals (status=3) → auto-creates CoworkerContract
│   └── CoworkerDataFile × 10 (refs: Proposal)
└── Supplemental CoworkerLedgerEntry for edge cases
```
