# Test Data Seeding Strategy

> **Purpose:** Define all records, volumes, and variance needed to meaningfully populate every report and dashboard spec. This document is the blueprint for a standalone `nexudus-test-data` repo where the Nexudus CLI + agent skills will create the data programmatically.

---

## 1. Design Principles

| Principle | Rationale |
|-----------|-----------|
| **Time-span coverage** | Data spans **24 months** (Aug 2024 → Aug 2026) for monthly trends, churn cohorts, and YoY comparisons. |
| **Deterministic but realistic** | Fixed seed for reproducibility. Plausible UK names, dates, and amounts. |
| **Layered creation order** | Entities have FK dependencies. Strict dependency order (§3). |
| **Idempotent scripts** | Query before creating (by email/name key). Safe to re-run. |
| **Configurable scale** | `config.py` sets volumes. Default = "small". Optional 3× "large" multiplier. |
| **CLI-first** | All ops via `nexudus <entity> <command> --agent`. Operator already authenticated. |
| **Additive** | Creates new data regardless of existing records. No clean-slate assumption. |
| **Workflow-aware** | Cancelled bookings come from deleting active bookings. Invoices from billing cycles. Ledger entries from invoice/payment actions. Proposals accepted via status update. |

---

## 2. Target Volumes (Small Profile)

### Layer 0 — Reference & Configuration

| Entity | CLI command | Count | Notes |
|--------|-------------|-------|-------|
| **Business** | `businesses list` | 3 | Query existing IDs. Multi-location filtering. |
| **TaxRate** | `taxrates` | 3 | UK: Standard 20%, Reduced 5%, Zero-rated 0%. |
| **FinancialAccount** | `financialaccounts` | 8 | See §4h. Assigned to products/services/tariffs. |
| **ResourceType** | `resourcetypes` | 5 | Meeting Room, Hot Desk, Private Office, Phone Booth, Parking. |

### Layer 1 — Structural Setup

| Entity | CLI command | Count | Notes |
|--------|-------------|-------|-------|
| **Team** | `teams` | 5 | Across locations. |
| **Tariff** (Plan) | `tariffs` | 8 | Varied billing frequency + target prices. FinancialAccount + TaxRate assigned. §4b. |
| **Product** | `products` | 15 | Add-ons, deposits, day passes, credit bundles, event tickets. Each has FinancialAccount + TaxRate. §4h. |
| **ExtraService** | `extraservices` | 6 | Per resource type. FinancialAccount + TaxRate linked. |
| **TimePass** | `timepasses` | 4 | Day pass and timed pass definitions. |
| **Resource** | `resources` | 20 | Mix of SystemResourceType. Across locations. |
| **FloorPlan** | `floorplans` | 3 | One per business. |
| **FloorPlanDesk** | `floorplandesks` | 40 | Size, Capacity, Price (target), Area. ItemType 1–5. §4d. |
| **InventoryAsset** | `inventoryassets` | 15 | Lockers (8) + equipment (7). AssignToType 2 or 3. §4n. |
| **DiscountCode** | `discountcodes` | 6 | Percentage/fixed. Plan/booking/product scoped. §4i. |
| **CrmBoard** | `crmboards` | 2 | "New Business", "Expansion". |
| **CrmBoardColumn** | `crmboardcolumns` | 10 | 5 stages/board. Win/Loss terminal columns flagged. |
| **BusinessTimeSlot** | `businesstimeslots` | 3 | Mon–Fri 08:00–18:00 per location. |
| **HelpDeskDepartment** | `helpdeskdepartments` | 3 | IT Support, Facilities, Billing. |
| **CommunityGroup** | `communitygroups` | 3 | General, Networking, Announcements. |
| **CalendarEventCategory** | `calendareventcategories` | 4 | Workshop, Networking, Social, Wellness. |

### Layer 2 — People

| Entity | CLI command | Count | Notes |
|--------|-------------|-------|-------|
| **Coworker** | `coworkers` | 60 | Spread across teams/locations. Mix lifecycle states. Engagement fields set. §4o. |
| **Visitor** | `visitors` | 60 | VisitorSource 1/2/3. Mix departed/on-site. |

### Layer 3 — Contracts & Assignments

| Entity | CLI command | Count | Notes |
|--------|-------------|-------|-------|
| **CoworkerContract** | `coworkercontracts` | 90 | All lifecycle scenarios §4a. ~30% have ContractTerm set (6/12mo). §4p. |
| **ContractProduct** | `contractproducts` | 30 | Recurring add-ons. Linked to Products with FinancialAccount. |
| **ContractSchedule** | `contractschedules` | 8 | Future price changes. |
| **ContractPausedPeriod** | `contractpausedperiods` | 12 | Past, current, future freezes. |
| **ContractDeposit** | `contractdeposits` | 10 | Mix refundable/non-refundable. Linked to deposit Product. §4k. |
| **CoworkerInventoryAsset** | `coworkerinventoryassets` | 12 | Lockers/equipment assigned to members. §4n. |
| **FloorPlanDesk assign** | `floorplandesks update` | ~28 | Set CoworkerId on occupied units. |

### Layer 4 — Activity & Transactions

| Entity | CLI command | Count | Notes |
|--------|-------------|-------|-------|
| **Booking** | `bookings` | 240 | 200 kept + 40 to be cancelled. §4g + §4j. |
| **Booking → Cancel** | `bookings delete` | 40 | Deleting creates CancelledBooking snapshots. §4j. |
| **BookingProduct** | (inline on booking) | 30 | Add-ons at booking creation. |
| **BookingVisitor** | `bookingvisitors` | 50 | External attendees. |
| **CheckIn** | `checkins` | 300 | Daily over 12 months. Some open (no ToTime). |
| **CoworkerExtraService** | `coworkerextraservices` | 80 | Booking charges + time credits + printing credits. §4e. |
| **CoworkerBookingCredit** | `coworkerbookingcredits` | 25 | Monetary wallets. Active/expired/near-expiry. |
| **CoworkerBookingCreditUseHistory** | `coworkerbookingcreditusehistories` | 50 | Spend transactions. |
| **CoworkerTimePass** | `coworkertimepasses` | 40 | Used/unused. Plan-issued/purchased. |
| **CoworkerProduct** | `coworkerproducts` | 20 | Recurring product subscriptions. |
| **CoworkerDelivery** | `coworkerdeliveries` | 40 | Mix of DeliveryType 1–5. Some collected, some pending. §4q. |
| **CalendarEvent** | `calendarevents` | 20 | Mix: one-off + recurring. Past + upcoming. §4r. |
| **EventAttendee** | `eventattendees` | 60 | 3–5 per event. Mix checked-in/not. |
| **HelpDeskMessage** | `helpdeskmessages` | 25 | Mix open/closed. Varied priorities. §4s. |
| **CommunityThread** | `communitythreads` | 15 | Message board posts. Across groups. §4t. |
| **CommunityMessage** | `communitymessages` | 40 | Replies on threads (2–4 per thread). |
| **BlogPost** | `blogposts` | 10 | Published articles. Spread over 12 months. §4u. |
| **CoworkerTask** | `coworkertasks` | 20 | Mix completed/pending. Varied due dates. §4v. |

### Layer 5 — Financial Records & CRM

| Entity | CLI command | Count | Notes |
|--------|-------------|-------|-------|
| **Invoices** | (system-generated) | ~150 | Triggered by contract billing cycles + manual charges. |
| **Invoice ops** | update/commands | ~35 | Pay(~90), void(~5), credit note(~10), partial(~15). Ledger entries auto-created. |
| **CoworkerLedgerEntry** | `coworkerledgerentries` | ~200 | Mostly auto-created. Manual supplements for edge cases. |
| **CrmOpportunity** | `crmopportunities` | 30 | Open/Won/Lost. Varied values + lead sources. |
| **CrmOpportunityHistory** | `crmopportunityhistories` | 60 | Stage transitions for conversion reporting. |
| **Proposal** | `proposals` | 15 | Linked to Won opps. Accepted ones create contracts. §4f. |
| **CoworkerDataFile** | `coworkerdatafiles` | 10 | Signed/unsigned proposal documents. |

**Total: ~2,800+ records across 50+ entity types.**

---

## 3. Creation Order (Dependency Graph)

```
Layer 0 — Reference (query or create once)
├── Business (query existing IDs via `nexudus whoami`)
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
├── Coworker × 60 (refs: Business, Team) — set engagement fields
└── Visitor × 60 (refs: Business, Coworker as host)

Layer 3 — Contracts & Occupancy
├── CoworkerContract × 90 (refs: Coworker, Tariff, Business) — ~30% with ContractTerm
├── ContractProduct × 30 (refs: CoworkerContract, Product)
├── ContractSchedule × 8 (refs: CoworkerContract)
├── ContractPausedPeriod × 12 (refs: CoworkerContract)
├── ContractDeposit × 10 (refs: CoworkerContract, Product)
├── CoworkerInventoryAsset × 12 (refs: Coworker, InventoryAsset)
└── FloorPlanDesk.CoworkerId updates (refs: FloorPlanDesk, Coworker)

Layer 4 — Activity
├── Booking × 240 (refs: Coworker, Resource) — includes BookingProducts inline, ~10 recurring
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
├── Cancel bookings × 40 (delete selected Bookings → CancelledBooking auto-created)
└── CrmOpportunity × 30 (refs: CrmBoardColumn, Coworker)
    └── CrmOpportunityHistory × 60 (refs: CrmOpportunity, CrmBoardColumn)

Layer 5 — Financial & Proposals
├── Trigger invoice generation (billing cycle commands on contracts)
├── Pay/void/credit invoices → ledger entries auto-generated
├── CoworkerBookingCreditUseHistory × 50 (refs: CoworkerBookingCredit, Booking)
├── Proposal × 15 (refs: Coworker, Tariff, CrmOpportunity context)
│   ├── Accept proposals (status=3) → auto-creates CoworkerContract
│   └── CoworkerDataFile × 10 (refs: Proposal)
└── Supplemental CoworkerLedgerEntry for edge cases
```

---

## 4. Variance Scenarios

### 4a. Membership Lifecycle (DEF-2 movement types)

| Scenario | Members | Contract Pattern |
|----------|---------|-----------------|
| **Long-term active** | 20 | StartDate 12–24mo ago. No EndDate. Open-ended. |
| **New joiner** | 8 | StartDate within last 3 months. |
| **Plan change** | 6 | Contract A ends month M, Contract B starts M or M+1. Same CoworkerId. |
| **Churned** | 10 | EndDate in past. No subsequent contract. |
| **Returned** | 4 | Churned 6+mo ago, new contract recently. |
| **Multi-contract (subscribed)** | 6 | Two active contracts simultaneously. |
| **Unsubscribed** | 3 | Had 2 contracts, one ended, one remains. |
| **Ending soon** | 3 | CancellationDate within 30–90 days. |

### 4b. Tariff Configuration (DEF-1 normalisation + target prices)

| Plan Name | SystemTariffType | InvoiceEvery | InvoiceEveryWeeks | Price | Target/Floor | Monthly Equiv |
|-----------|-----------------|-------------|-------------------|-------|--------------|---------------|
| Hot Desk Monthly | 5 (FullTimeHotDesk) | 1 | 0 | £150 | £150 | £150 |
| Dedicated Desk Monthly | 3 (FullTimeDedicatedDesk) | 1 | 0 | £350 | £350 | £350 |
| Private Office Small | 1 (FullTimePrivateOffice) | 1 | 0 | £1,200 | £1,200 | £1,200 |
| Private Office Large | 1 | 1 | 0 | £2,000 | £2,000 | £2,000 |
| Hot Desk Quarterly | 5 | 3 | 0 | £400 | — | £133 |
| Private Office Annual | 1 | 12 | 0 | £12,000 | — | £1,000 |
| Flex Weekly | 6 (PartTimeHotDesk) | 0 | 1 | £50 | — | £214 |
| Flex Fortnightly | 6 | 0 | 2 | £250 | — | £536 |

Each tariff assigned:
- `FinancialAccountId` → "Membership Revenue" account
- `TaxRateId` → Standard 20%
- `MinimumPrice` where applicable
- `DiscountExtraServices` / `DiscountTimePasses` on premium plans (10–20%)

### 4c. Invoice Status Distribution

| Status | Count | How achieved |
|--------|-------|-------------|
| **Paid** | ~90 | Create ledger entry (PAYM) against invoice. Update `Paid=true`. |
| **Unpaid** | ~25 | Leave as-is after generation. DueDate in future. |
| **Overdue** | ~20 | Leave as-is. DueDate in past (30–120 days ago). |
| **Credit Note** | ~10 | Issue credit note against paid/unpaid invoice. |
| **Void** | ~5 | Update invoice `Draft=true` or use void workflow. |

### 4d. Floor Plan Desk (Unit) Distribution

| Unit Type (ItemType) | Count | Occupied | Vacant | Size (sqft) | Capacity | Price (Target) |
|---------------------|-------|----------|--------|-------------|----------|----------------|
| Office (1) | 10 | 8 | 2 | 150–400 | 2–8 | £1,200–£2,500 |
| Dedicated Desk (2) | 12 | 9 | 3 | 30–50 | 1 | £300–£400 |
| Hot Desk (3) | 10 | 6 | 4 | 20–30 | 1 | £100–£180 |
| Other (4) — Storage | 4 | 3 | 1 | 10–20 | 0 | £50–£100 |
| Room (5) | 4 | 2 | 2 | 200–600 | 4–20 | £1,800–£3,000 |

All units have `Area` set (e.g., "Ground Floor", "First Floor", "Mezzanine").

### 4e. Credit & Pass Scenarios

| Type | Creation method | Active | Expired/Used | Near-Expiry |
|------|----------------|--------|-------------|-------------|
| **Monetary Credits** | `coworkerbookingcredits create` | 12 | 8 | 5 expire this month |
| **Time Credits** | `coworkerextraservices create` (ExtraServiceId, TotalUses, no BookingId) | 15 | 10 fully used | — |
| **Printing Credits** | `coworkerextraservices create` (IsPrintingCredit ExtraService) | 5 | 3 | — |
| **Day Passes** | `coworkertimepasses create` | 15 unused | 20 used | 5 expiring ≤30 days |

### 4f. Proposal → Contract Workflow

For Won CRM opportunities:
1. Create `Proposal` with `ProposalStatus=1` (Draft), linking `CoworkerId`, `TariffId`, `Price`, `StartDate`
2. Update to `ProposalStatus=2` (Sent)
3. For accepted: Update to `ProposalStatus=3` → system auto-creates `CoworkerContract`
4. For rejected: Update to `ProposalStatus=4`
5. Attach `CoworkerDataFile` with `ProposalGuid` reference

| Proposal Status | Count | Contract Created? |
|----------------|-------|-------------------|
| Draft | 3 | No |
| Sent | 4 | No |
| Accepted | 5 | Yes (auto) |
| Rejected | 3 | No |

Some accepted proposals use a `DiscountCodeId` to apply plan discounts.

### 4g. Booking Distribution

| Characteristic | Spread |
|---------------|--------|
| Resources | All 20 resources used. Weight toward meeting rooms. |
| Duration | 30min (20%), 1hr (35%), 2hr (25%), 4hr (15%), full day (5%) |
| Time of day | Morning 08–12 (40%), Afternoon 12–17 (40%), Evening 17–20 (20%) |
| Days of week | Mon–Fri (85%), Sat–Sun (15%) |
| Month distribution | Heavier in recent 6mo, lighter in older months |
| Admin-booked | ~10% have BookedByAdminUserId |
| **Recurring** | ~10 bookings have `Repeats` > 0 (weekly repeating room bookings) |
| **With products** | ~15% (36 bookings) have BookingProduct records — catering, AV, etc. |
| **With guests** | ~25% (50 bookings) have BookingVisitor records — external attendees |
| With discount | ~5% have DiscountCode text field set |
| Tentative | ~5% have Tentative=true (pending approval) |

**Recurring bookings detail:**
- 5 weekly meeting room bookings (Repeats=2, RepeatEvery=1) — ongoing team standups
- 3 weekly hot desk bookings — part-time members
- 2 monthly all-hands (Repeats=3, RepeatEvery=1) — larger room

**Booking products (inline at creation):**
- Tea/Coffee service (£5) — on ~20 bookings
- Catering lunch (£15) — on ~10 bookings  
- AV Equipment (£25) — on ~6 bookings

**Booking visitors/guests:**
- 1–3 visitors per applicable booking
- Mix of registered Visitors (have Visitor record) and ad-hoc names

### 4h. Financial Accounts & Products

**Financial Accounts (8):**

| Name | Code | AccountType | Used by |
|------|------|-------------|---------|
| Membership Revenue | MEM-001 | 1 (Sales) | All Tariffs |
| Booking Revenue | BKG-001 | 1 (Sales) | ExtraServices (booking rates) |
| Product Sales | PRD-001 | 1 (Sales) | Products (add-ons, one-offs) |
| Event Revenue | EVT-001 | 1 (Sales) | Event-related products |
| Credit Sales | CRD-001 | 1 (Sales) | Credit bundle products |
| Payment Receipts | PAY-001 | 2 (Payments) | Payment gateway receipts |
| Deposit Holding | DEP-001 | 3 (Deposits) | ContractDeposit products |
| Refund Account | REF-001 | 2 (Payments) | Refund tracking |

**Products (12):**

| Product | AvailableAs | SystemProductType | Price | FinancialAccount | TaxRate |
|---------|-------------|-------------------|-------|------------------|---------|
| Catering - Tea/Coffee | 3 (OneOff) | 5 (BookingProducts) | £5 | Product Sales | Standard 20% |
| Catering - Lunch | 3 (OneOff) | 5 | £15 | Product Sales | Standard 20% |
| AV Equipment | 3 (OneOff) | 5 | £25 | Product Sales | Standard 20% |
| Storage Locker | 2 (Recurring) | 99 (Other) | £30/mo | Product Sales | Standard 20% |
| Parking Space | 2 (Recurring) | 99 | £75/mo | Product Sales | Standard 20% |
| Mail Handling | 2 (Recurring) | 99 | £20/mo | Product Sales | Zero 0% |
| Day Pass (5-pack) | 3 (OneOff) | 1 (DayPass) | £120 | Membership Revenue | Standard 20% |
| Credit Bundle £50 | 3 (OneOff) | 2 (CreditBundle) | £50 | Credit Sales | Standard 20% |
| Credit Bundle £200 | 3 (OneOff) | 2 | £200 | Credit Sales | Standard 20% |
| Security Deposit - Office | 3 (OneOff) | 99 | £1,000 | Deposit Holding | Zero 0% |
| Security Deposit - Desk | 3 (OneOff) | 99 | £250 | Deposit Holding | Zero 0% |
| Printing Credits (500 pages) | 3 (OneOff) | 99 | £25 | Product Sales | Standard 20% |

### 4i. Discount Codes

| Code | Type | Value | Scope | Restrictions |
|------|------|-------|-------|-------------|
| WELCOME20 | Percentage | 20% | Plans (all tariffs) | MaxUses=50, new members only |
| TEAM10 | Percentage | 10% | Plans (office tariffs only) | OnlyForMembers=true |
| FREEMONTH | Fixed | £350 | Plans (Dedicated Desk) | MaxUsesPerUser=1 |
| BOOKFREE | Percentage | 100% | Bookings (Meeting Room type) | MaxUses=20 |
| PRODUCT15 | Percentage | 15% | Products (all) | ValidFrom/ValidTo window |
| LAUNCH50 | Fixed | £50 | Plans + Products | Expired (ValidTo in past) |

### 4j. Cancelled Booking Workflow

**Important:** `CancelledBooking` records cannot be created directly. They are system-generated snapshots when a booking is deleted.

**Process:**
1. Create 240 bookings total (200 to keep + 40 to cancel)
2. After all bookings and booking visitors/products are created on the 40 designated ones
3. Delete the 40 selected bookings via `nexudus bookings delete <id> --yes --agent`
4. System automatically creates CancelledBooking records preserving all metadata

**Cancellation reasons to cover:**
- No longer needed (~15)
- Rebooked for different time (~10)
- Auto-cancelled for not checking in / no-show (~8)
- Cost concerns (~4)
- Failure to pay upfront (~3)

**Note:** CancellationReason is a text field on the snapshot, set by the system based on context. Manually deleted bookings won't have rich reasons — only system-triggered cancellations (e.g., no-show auto-cancel) populate the reason field meaningfully. Consider using commands if available, or accept that manually deleted bookings will have generic cancellation metadata.

### 4k. Deposits

| Type | Count | Refundable | Price | Product |
|------|-------|-----------|-------|---------|
| Office deposit | 6 | Yes (4), No (2) | £1,000 | Security Deposit - Office |
| Desk deposit | 4 | Yes (3), No (1) | £250 | Security Deposit - Desk |

Created via `contractdeposits create` linked to the contract and deposit product. On cancellation of refundable deposits, system auto-generates credit notes.

### 4l. CRM Pipeline Distribution

| Stage | Column Flags | Opportunities | Avg Value | Avg Days |
|-------|-------------|--------------|-----------|----------|
| Lead | — | 8 | £800 | 5 |
| Qualified | — | 6 | £1,200 | 12 |
| Proposal Sent | — | 5 | £1,500 | 8 |
| Negotiation | — | 4 | £2,000 | 15 |
| Won | WinOpportunity=true | 5 | £1,800 | — |
| Lost | LoseOpportunity=true | 7 | £1,000 | — |

Won opportunities have corresponding Proposals created and accepted (§4f).
Each opportunity has 2–4 CrmOpportunityHistory records showing stage transitions.

### 4m. Check-in Distribution

| Characteristic | Spread |
|---------------|--------|
| Members checking in | ~40 of 60 members have check-in activity |
| Frequency | Heavy users: 4–5×/week. Light users: 1–2×/week. |
| Open check-ins | ~5 currently open (no ToTime) |
| Duration | 2–10 hours typical |
| Source distribution | Manual (30%), NexIO tablet (50%), Door access (20%) |

### 4n. Lockers & Equipment (InventoryAssets)

**Asset definitions (15):**

| Name | AssignToType | Value | Location |
|------|-------------|-------|----------|
| Locker A-01 through A-08 | 3 (FloorPlanItem) | £0 | Ground Floor |
| Monitor – Dell 27" (×3) | 2 (Resource) | £350 | Various |
| Standing Desk Converter (×2) | 2 (Resource) | £200 | Various |
| Webcam – Logitech (×2) | 1 (Location) | £80 | Various |

**Assignments (CoworkerInventoryAsset × 12):**
- 6 lockers assigned to members (AssignedFrom in past, no AssignedTo = current)
- 2 lockers assigned then returned (AssignedTo set = historical)
- 3 monitors currently assigned to members
- 1 standing desk converter assigned

### 4o. Coworker Engagement Fields

**⚠️ ChurnProbability and EngagementLevel are NOT confirmed in the CLI entity reference.** These may be platform-internal fields. Validate against a live instance API response before scripting.

If available via update:

| Field | Distribution across 60 members |
|-------|-------------------------------|
| ChurnProbability | Low (30), Medium (15), High (10), null/unset (5) |
| EngagementLevel | High (20), Medium (25), Low (10), null/unset (5) |

Correlation: High churn probability should align with low engagement and churned/ending-soon members.

### 4p. ContractTerm (Minimum Term)

~30% of contracts (27 of 90) have `ContractTerm` set:

| Term | Count | Pattern |
|------|-------|---------|
| 6 months | 12 | Hot Desk and Dedicated Desk plans |
| 12 months | 10 | Private Office plans |
| 24 months | 5 | Enterprise/Large Office plans |

Mix of:
- Past minimum term (already expired — member stayed beyond it)
- Currently within minimum term (not yet reached)
- Approaching minimum term end (within 30–60 days)

Also set `Value` (benchmark price) different from `Price` on ~20 contracts to feed attainment calculations. E.g., FloorPlanDesk.Price = £1,500 but contract Price = £1,350 (discounted).

### 4q. Deliveries

| DeliveryType | Count | Collected | Pending | Forwarded/Other |
|-------------|-------|-----------|---------|-----------------|
| Mail (1) | 15 | 10 | 3 | 2 forwarded |
| Parcel (2) | 12 | 7 | 4 | 1 returned |
| Check (3) | 3 | 3 | 0 | — |
| Publicity (4) | 5 | 2 | 1 | 2 recycled |
| Other (5) | 5 | 3 | 2 | — |

HandlingPreference spread: StoreForCollection (25), Forward (5), Recycle (4), Shred (3), ReturnToSender (3).

Dates spread over 6 months. ~30% have `Collected=true` + `CollectedOn` set.

### 4r. Events

| Event | Recurring | Attendees | Past/Upcoming |
|-------|-----------|-----------|---------------|
| Weekly Networking Lunch | Yes (weekly) | 8–12 | Both |
| Monthly All-Hands | Yes (monthly) | 20–30 | Both |
| Yoga Wednesday | Yes (weekly) | 5–8 | Both |
| Summer BBQ | No | 40 | Past |
| Workshop: Startup Finance | No | 15 | Past |
| Workshop: Marketing 101 | No | 12 | Past |
| New Member Orientation | Yes (monthly) | 3–6 | Both |
| Q3 Town Hall | No | 25 | Upcoming |
| Demo Day | No | 30 | Upcoming |
| Holiday Party | No | 50 | Upcoming (Dec) |

~20 total CalendarEvent records. Some linked to a Resource (room blocked).
EventAttendees: mix of `CheckedIn=true/false`, `Invoiced=true/false`.

### 4s. Help Desk Tickets

| Priority | Open | Closed | Total |
|----------|------|--------|-------|
| High (1) | 2 | 5 | 7 |
| Medium (2) | 3 | 8 | 11 |
| Low (3) | 2 | 5 | 7 |

Departments: IT Support (10), Facilities (9), Billing (6).
Spread over 6 months. Subjects: "WiFi not working", "AC too cold", "Invoice query", "Locker jammed", "Parking issue", etc.

### 4t. Community / Message Board

| Group | Threads | Messages per thread |
|-------|---------|---------------------|
| General | 6 | 2–5 |
| Networking | 5 | 2–4 |
| Announcements | 4 | 1–2 |

15 threads total, ~40 messages. Posted by test coworkers. Spread over 6 months.
Some threads tagged, some private.

### 4u. Blog Posts / Articles

10 published articles spread over 12 months:
- "Welcome to Our New Space" (oldest)
- "Summer Networking Tips"
- "How to Maximise Your Hot Desk"
- "New Facilities Announcement"
- "Member Spotlight: [Test Member]"
- "Community Guidelines Update"
- "Year in Review 2025"
- "New Plans Available"
- "Booking Best Practices"
- "Upcoming Events This Quarter" (newest)

Mix of `OnlyForMembers=true` (4) and public (6). Some with `ShowInHomePage=true`.

### 4v. Tasks

| Status | Count | Due Date |
|--------|-------|----------|
| Completed | 10 | Past (completed on time) |
| Pending (overdue) | 5 | Past (not completed) |
| Pending (upcoming) | 5 | Future (within 30 days) |

Assigned to test coworkers. Responsibilities: various admin users.
Examples: "Complete induction", "Return equipment", "Update payment method", "Sign contract", "Collect delivery".

---

## 5. Daily Update Script

Reports with real-time or daily summaries need fresh records each day to look alive. The `daily_update.py` generator creates a small batch of "today's" records each time it runs.

### What it creates per run

| Entity | Records/day | Logic |
|--------|------------|-------|
| **CheckIn** | 8–15 | Random subset of active members check in between 07:30–10:00. ~3 left open (no ToTime). |
| **Booking** | 3–6 | Bookings for today on random resources. Some created "yesterday" (future booking), some same-day. |
| **Visitor** | 2–4 | Guests arriving today. Mix of hosted (CoworkerId set) and walk-in. |
| **CoworkerDelivery** | 1–3 | Mail/parcel arrivals. Left as pending (Collected=false). |

### How it works

```bash
python generators/daily_update.py          # Creates today's records
python generators/daily_update.py --days 7 # Backfill last 7 days
python generators/daily_update.py --date 2026-08-01  # Specific date
```

### Design

- **Time-aware:** All timestamps use today's date (or `--date` override) with realistic times.
- **Randomised from active pool:** Only creates records for members with active contracts (queries contracts model first).
- **Idempotent per day:** Checks if records for today already exist (by date range query) before creating.
- **Closes yesterday's open check-ins:** Before creating today's, finds any check-ins from yesterday with no ToTime and updates them with a checkout time (16:00–18:30).
- **Collects yesterday's deliveries:** Randomly marks 50% of yesterday's pending deliveries as collected.

### Scheduling

Can be run manually or via cron/launchd:
```bash
# Daily at 09:00 local time
0 9 * * * cd /path/to/nexudus-test-data && python generators/daily_update.py
```

Or run on-demand before reviewing dashboards that show "today" data.

---

## 6. Repo Structure (nexudus-test-data)

```
nexudus-test-data/
├── CLAUDE.md                    ← Agent instructions (see §7)
├── README.md                    ← Human overview
├── config.py                    ← Volumes, date ranges, business IDs, scale multiplier
├── requirements.txt             ← faker, etc.
├── data/
│   ├── members.json             ← Generated member profiles
│   ├── contracts.json           ← Contract lifecycle scenarios
│   ├── bookings.json            ← Booking records
│   ├── invoices.json            ← Invoice + line items
│   └── created-ids/             ← ID tracking per entity per run
│       ├── coworkers.json
│       ├── contracts.json
│       └── ...
├── generators/
│   ├── __init__.py
│   ├── base.py                  ← BaseGenerator with idempotency + ID tracking
│   ├── 00_reference.py          ← Layer 0: Tax rates, financial accounts, resource types
│   ├── 01_structural.py         ← Layer 1: Tariffs, products, resources, desks, inventory, discounts, CRM
│   ├── 02_people.py             ← Layer 2: Coworkers (with engagement fields), visitors
│   ├── 03_contracts.py          ← Layer 3: Contracts (with ContractTerm), deposits, freezes, occupancy
│   ├── 04_activity.py           ← Layer 4: Bookings (recurring + products + guests), check-ins, credits
│   ├── 05_community.py          ← Layer 4: Deliveries, events, help desk, threads, blogs, tasks
│   ├── 06_financial.py          ← Layer 5: Invoice triggering, payments, ledger supplements
│   ├── 07_crm_proposals.py      ← Layer 5: Opportunities, proposals, document files
│   └── daily_update.py          ← Daily: fresh check-ins, bookings, visitors, deliveries
├── scripts/
│   ├── seed_all.sh              ← Run all layers in order
│   ├── seed_layer.sh <N>        ← Run one specific layer
│   ├── daily.sh                 ← Run daily_update.py (for cron/manual use)
│   ├── teardown.sh              ← Delete all test data (with safety prompt)
│   └── verify.sh               ← Count records per entity, validate targets
├── reference/
│   ├── entity-dependencies.md   ← Dependency graph (copy of §3)
│   ├── variance-scenarios.md    ← All scenarios (copy of §4)
│   ├── field-enums.md           ← Required enum values per entity
│   ├── financial-accounts.md    ← Account chart with codes
│   ├── tax-rates.md             ← UK tax rate definitions
│   ├── cli-patterns.md          ← Common CLI patterns per entity
│   └── invoice-workflows.md     ← How invoices are generated and adjusted
└── .claude/
    └── skills/
        └── nexudus/             ← Symlink or copy of the nexudus skill
```

---

## 7. CLAUDE.md for the Test Data Repo

```markdown
# Nexudus Test Data — Agent Instructions

## What this repo does

Generates and seeds realistic test data into a Nexudus instance via the `nexudus` CLI.
The data populates all reports and dashboards defined in the `reports` repo.

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

1. **Always use `nexudus <entity> <command> --agent`** for CLI calls.
2. **Check idempotency** — before creating, query for existing records with same key.
3. **Respect dependency order** — never create a child before its parent.
4. **Use config.py** for all magic numbers (volumes, dates, business IDs).
5. **Dates must be UTC with Z suffix** — e.g. `"2025-06-15T09:00:00Z"`.
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
8. **Run `nexudus whoami --agent` first** to get DefaultBusinessId and defaults.
9. **Log all created IDs** — append to `data/created-ids/<entity>.json`.
10. **One CLI call at a time** — do not parallelise nexudus CLI calls.
11. **Cancelled bookings** — create booking first, then delete it. System creates CancelledBooking.
12. **Invoices** — cannot be created directly. Trigger via billing cycle commands on contracts.
    Adjustments (pay, void, credit) create ledger entries automatically.
13. **Proposals** — create at status=1, then update to status=3 to accept (auto-creates contract).
14. **Financial accounts** — assign to Products, ExtraServices, and Tariffs at creation time.
15. **Tax rates** — assign to Products, ExtraServices, and Tariffs at creation time.
16. **Discount codes** — apply via Proposal.DiscountCodeId or Booking.DiscountCode text field.
17. **Recurring bookings** — set `Repeats` (enum: 1=Daily, 2=Weekly, 3=Monthly) + `RepeatEvery`.
18. **Booking products** — create BookingProduct records after the booking exists.
19. **Booking guests** — create BookingVisitor records linking Booking → Visitor.
20. **Daily update** — run `daily_update.py` for fresh daily records (check-ins, bookings, visitors, deliveries).
21. **Events** — CalendarEvent creates the event; EventAttendee adds people to it.
22. **Deliveries** — create as pending, then update `Collected=true` + `CollectedOn` to mark collected.
23. **Community threads** — create CommunityThread, then CommunityMessage as replies.
24. **Engagement fields** — ⚠️ ChurnProbability/EngagementLevel unconfirmed via CLI. Validate first.
25. **ContractTerm** — set on ~30% of contracts. This is the minimum term end date.

## Entity creation patterns

### Tax Rate (Layer 0)
```bash
nexudus taxrates create --business <BusinessId> --name "Standard" --rate 20 --agent
nexudus taxrates create --business <BusinessId> --name "Reduced" --rate 5 --agent
nexudus taxrates create --business <BusinessId> --name "Zero-rated" --rate 0 --agent
```

### Financial Account (Layer 0)
```bash
nexudus financialaccounts create --business <BusinessId> --name "Membership Revenue" --code "MEM-001" --account-type 1 --agent
```

### Tariff with financial + tax links (Layer 1)
```bash
nexudus tariffs create \
  --business <BusinessId> \
  --name "Hot Desk Monthly" \
  --system-tariff-type 5 \
  --price 150.00 \
  --invoice-every 1 \
  --invoice-every-weeks 0 \
  --currency-id <CurrencyId> \
  --financial-account-id <FinAcctId> \
  --tax-rate-id <StandardTaxId> \
  --cancellation-period 30 \
  --agent
```

### Product with financial + tax (Layer 1)
```bash
nexudus products create \
  --business <BusinessId> \
  --name "[TEST] Catering - Lunch" \
  --price 15.00 \
  --available-as 3 \
  --system-product-type 5 \
  --currency-id <CurrencyId> \
  --financial-account-id <ProdSalesAcctId> \
  --tax-rate-id <StandardTaxId> \
  --agent
```

### Coworker (Layer 2)
```bash
nexudus coworkers create \
  --full-name "Alice Johnson" \
  --email "test-001@seeddata.local" \
  --business <BusinessId> \
  --team <TeamId> \
  --agent
```

### CoworkerContract (Layer 3)
```bash
nexudus coworkercontracts create \
  --coworker <CoworkerId> \
  --tariff <TariffId> \
  --start-date "2025-01-01T00:00:00Z" \
  --price 350.00 \
  --business <BusinessId> \
  --agent
```

### Contract Deposit (Layer 3)
```bash
nexudus contractdeposits create \
  --coworker-contract-id <ContractId> \
  --product-id <DepositProductId> \
  --refundable true \
  --agent
```

### Booking with add-ons (Layer 4)
```bash
nexudus bookings create \
  --coworker <CoworkerId> \
  --resource <ResourceId> \
  --from-time "2025-06-15T09:00:00Z" \
  --to-time "2025-06-15T10:00:00Z" \
  --agent
```

### Cancel a booking → creates CancelledBooking (Layer 4)
```bash
nexudus bookings delete <BookingId> --yes --agent
```

### CoworkerExtraService — Time Credit (Layer 4)
```bash
nexudus coworkerextraservices create \
  --coworker-id <CoworkerId> \
  --business-id <BusinessId> \
  --extra-service-id <TimeCreditExtraServiceId> \
  --total-uses 600 \
  --charge-period 1 \
  --agent
```

### Pay invoice via ledger entry (Layer 5)
```bash
nexudus coworkerledgerentries create \
  --business-id <BusinessId> \
  --coworker-id <CoworkerId> \
  --coworker-invoice-id <InvoiceId> \
  --description "PAYM-Payment received" \
  --code "PAYM" \
  --debit 0 \
  --credit 350.00 \
  --balance 0 \
  --agent
```

### Proposal → Accept → Auto-creates contract (Layer 5)
```bash
# Create
nexudus proposals create \
  --coworker-id <CoworkerId> \
  --tariff-id <TariffId> \
  --issued-by-id <AdminUserId> \
  --responsible-id <AdminUserId> \
  --reference "PROP-001" \
  --proposal-status 1 \
  --billing-day 1 \
  --quantity 1 \
  --price 350.00 \
  --start-date "2026-07-01T00:00:00Z" \
  --agent

# Accept (creates contract automatically)
nexudus proposals update <ProposalId> --proposal-status 3 --agent
```

### Delivery (Layer 4)
```bash
nexudus coworkerdeliveries create \
  --business-id <BusinessId> \
  --coworker-id <CoworkerId> \
  --name "Amazon parcel" \
  --location "Reception" \
  --delivery-type 2 \
  --handling-preference StoreForCollection \
  --agent
```

### Event + Attendee (Layer 4)
```bash
nexudus calendarevents create \
  --business-id <BusinessId> \
  --name "[TEST] Weekly Networking Lunch" \
  --start-date "2026-08-06T12:00:00Z" \
  --end-date "2026-08-06T13:30:00Z" \
  --repeats 2 \
  --repeat-every 1 \
  --which-events-to-update 1 \
  --resource-id <RoomResourceId> \
  --agent

nexudus eventattendees create \
  --business-id <BusinessId> \
  --calendar-event-id <EventId> \
  --event-product-id <EventProductId> \
  --full-name "Alice Johnson" \
  --email "test-001@seeddata.local" \
  --coworker-id <CoworkerId> \
  --agent
```

### Help Desk Ticket (Layer 4)
```bash
nexudus helpdeskmessages create \
  --business-id <BusinessId> \
  --coworker-id <CoworkerId> \
  --subject "[TEST] WiFi not working in meeting room 3" \
  --message-text "Cannot connect to WiFi since this morning." \
  --priority 1 \
  --help-desk-department-id <ITDeptId> \
  --ai-processing-result "" \
  --agent
```

### Community Thread + Reply (Layer 4)
```bash
nexudus communitythreads create \
  --business-id <BusinessId> \
  --user-id <UserId> \
  --coworker-id <CoworkerId> \
  --community-group-id <GroupId> \
  --subject "Anyone up for lunch today?" \
  --message "Thinking of trying the new café next door." \
  --agent

nexudus communitymessages create \
  --community-thread-id <ThreadId> \
  --user-id <UserId> \
  --coworker-id <CoworkerId> \
  --message "Count me in! 12:30?" \
  --agent
```

### Inventory Asset + Assignment (Layer 1 + 3)
```bash
nexudus inventoryassets create \
  --business-id <BusinessId> \
  --name "[TEST] Locker A-01" \
  --assign-to-type 3 \
  --floor-plan-desk-id <DeskId> \
  --value 0 \
  --agent

nexudus coworkerinventoryassets create \
  --coworker-id <CoworkerId> \
  --business-id <BusinessId> \
  --inventory-asset-id <AssetId> \
  --assigned-from "2026-01-15T00:00:00Z" \
  --agent
```

### Task (Layer 4)
```bash
nexudus coworkertasks create \
  --business-id <BusinessId> \
  --coworker-id <CoworkerId> \
  --name "Complete induction checklist" \
  --responsible-id <AdminUserId> \
  --due-date "2026-08-15T00:00:00Z" \
  --agent
```

### Recurring Booking (Layer 4)
```bash
nexudus bookings create \
  --coworker <CoworkerId> \
  --resource <ResourceId> \
  --from-time "2026-08-04T09:00:00Z" \
  --to-time "2026-08-04T10:00:00Z" \
  --repeats 2 \
  --repeat-every 1 \
  --which-bookings-to-update 1 \
  --agent
```

### Daily Update (run daily or on-demand)
```bash
python generators/daily_update.py          # Today's records
python generators/daily_update.py --days 7 # Backfill last 7 days
```

## Verification

After seeding, run `bash scripts/verify.sh`:
```bash
nexudus coworkers list --page-size 1 --agent | jq '.meta.total'
nexudus coworkercontracts list --page-size 1 --agent | jq '.meta.total'
nexudus bookings list --page-size 1 --agent | jq '.meta.total'
nexudus cancelledbookings list --page-size 1 --agent | jq '.meta.total'
nexudus coworkerinvoices list --page-size 1 --agent | jq '.meta.total'
# ... etc for each entity
```
```

---

## 8. Implementation Phases

| Phase | What | Dependencies |
|-------|------|-------------|
| **Phase 1** | Repo setup: structure, config.py, base.py, reference docs | None |
| **Phase 2** | Layer 0 + Layer 1: Tax, accounts, tariffs, products, resources, desks, inventory, discounts, events setup, help desk depts | None |
| **Phase 3** | Layer 2: Coworkers (60, with engagement fields) + Visitors (60) | Phase 2 |
| **Phase 4** | Layer 3: Contracts (with ContractTerm), deposits, freezes, inventory assignments, occupancy | Phase 3 |
| **Phase 5** | Layer 4a: Bookings (recurring + products + guests) + cancellations, check-ins, credits, passes | Phase 4 |
| **Phase 6** | Layer 4b: Deliveries, events + attendees, help desk, community, blogs, tasks | Phase 4 |
| **Phase 7** | Layer 5: Invoice generation + payments, CRM opportunities, proposals | Phase 5 |
| **Phase 8** | Daily update script + verification + teardown scripts | Phase 7 |
| **Phase 9** | Run against test instance, validate all report widgets populate | Phase 8 |

---

## 9. Things You Might Have Missed

These are additional considerations surfaced during analysis:

| Item | Status | Notes |
|------|--------|-------|
| **Recurring bookings** | ✅ Covered | ~10 bookings with `Repeats` > 0. §4g. |
| **Booking products (sold with booking)** | ✅ Covered | ~36 bookings with inline BookingProduct records. §4g. |
| **Booking guests** | ✅ Covered | ~50 BookingVisitor records. §4g. |
| **Deliveries** | ✅ Covered | 40 records, all 5 types, handling preferences. §4q. |
| **Events** | ✅ Covered | 20 CalendarEvents + 60 attendees. §4r. |
| **Help Desk** | ✅ Covered | 25 tickets, 3 priorities, 3 departments. §4s. |
| **Blog / Articles** | ✅ Covered | 10 published articles. §4u. |
| **Community / Message Board** | ✅ Covered | 15 threads + 40 messages across 3 groups. §4t. |
| **Lockers & Equipment** | ✅ Covered | 15 InventoryAssets + 12 assignments. §4n. |
| **Tasks** | ✅ Covered | 20 tasks, mix completed/pending/overdue. §4v. |
| **Engagement / Churn fields** | ✅ Covered (⚠️ validate) | ChurnProbability + EngagementLevel. §4o. |
| **ContractTerm (minimum term)** | ✅ Covered | ~30% of contracts. §4p. |
| **Daily update script** | ✅ Covered | Creates fresh daily records. §5. |
| **Multiple locations** | ✅ Covered | Distribute ~50%/30%/20% across 3 locations. |
| **Invoice line source diversity** | ✅ Covered | All 4 source UniqueIds represented. |
| **Contract Value field** | ✅ Covered | Benchmark ≠ Price on ~20 contracts. §4p. |
| **Resource availability** | Partially | Set `Available=false` on 2 resources for maintenance. |
| **Floor plan desk variants** | Low priority | May need `FloorPlanDeskVariant` if reports use them. |
| **Event products (ticketing)** | To add | Need `EventProduct` records (ticket types) before `EventAttendee`. |
| **CoworkerDiscountCode** | Not needed | Discount is applied at Proposal or Booking level, not separately assigned. |

---

## 10. Answers to Design Questions

| # | Question | Answer |
|---|----------|--------|
| 1 | Variance for item sales sources / opportunities / check-in sources? | **Yes, all covered.** Invoice lines have all 4 source types. Opportunities have 5 lead sources. Check-ins have 3 source types. See §4g, §4l, §4m. |
| 2 | Are deposits considered? | **Yes.** 10 ContractDeposits (mix refundable/non-refundable). Linked to deposit Products with FinancialAccount. §4k. |
| 3 | Discounts and discount codes? | **Yes.** 6 discount codes covering percentage/fixed, plan/booking/product scope. Applied via Proposals and Booking.DiscountCode. §4i. |
| 4 | Cancelled bookings workflow? | **Correct — create then delete.** 40 bookings created specifically to be deleted. System generates CancelledBooking snapshots. §4j. |
| 5 | Floor plan desk fields? | **Area, Capacity, Size, and Price (target) all included.** See §4d. |
| 6 | Plan target prices? | **Yes.** Tariffs have Price set as the target. Contracts can override. MinimumPrice on some plans. §4b. |
| 7 | Proposals for opportunities? | **Yes.** Won opportunities get Proposals created → accepted (status=3) → auto-creates contracts. §4f. |
| 8 | Financial accounts + tax rates? | **Yes.** 8 financial accounts (UK accounting chart), 3 UK tax rates. All assigned to Products, ExtraServices, Tariffs at creation time. §4h. |
