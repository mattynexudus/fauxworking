# Test Data Seeding Strategy

> **Purpose:** Define all records, volumes, and variance needed to meaningfully populate every report and dashboard spec. This was the original blueprint for this repo; Phases 1–5 (§8) are now built — Layers 0 through 4a. **§7 has been superseded by the repo's top-level `CLAUDE.md`** — that file is the source of truth for agent instructions and entity creation patterns; this doc keeps the volumes/variance/dependency-graph design plus a status log of what's built and where it diverged from the original plan.
>
> **Key divergences from the original plan**, expanded on in §8:
>
> - **MCP tools, not a CLI.** All Nexudus operations go through `nexudus_list` / `nexudus_get` / `nexudus_create` / `nexudus_update` / `nexudus_delete` (the `claude.ai Nexudus` MCP server), not a `nexudus <entity> <command> --agent` shell command. Every `nexudus ...` CLI example below is illustrative of the operation, not literal syntax.
> - **No `[TEST]` name prefixes.** Records are meant to look like real data. `config.TEST_NAME_PREFIX` is `""`. The only marker baked into a record is the coworker email (`test-{id}@seeddata.local`); teardown safety comes from `data/created-ids/<entity>.json` ID tracking, not name matching.
> - **Two-step generation.** `prebuild.py` generates deterministic profiles into committed `data/*.json` files; the layer generators (`generators/0N_*.py`) read those files and push to Nexudus, resolving day/month offsets against `config.TODAY` at run time. This keeps faker/randomness out of the live-run path and avoids re-deriving the same data (and burning tokens) on every run.

---

## 1. Design Principles

| Principle | Rationale |
|-----------|-----------|
| **Time-span coverage** | Data spans **24 months** (Aug 2024 → Aug 2026) for monthly trends, churn cohorts, and YoY comparisons. |
| **Deterministic but realistic** | Fixed seed for reproducibility. Plausible UK names, dates, and amounts. |
| **Layered creation order** | Entities have FK dependencies. Strict dependency order (§3). |
| **Idempotent scripts** | Query before creating (by email/name key). Safe to re-run. |
| **Configurable scale** | `config.py` sets volumes. Default = "small". Optional 3× "large" multiplier. |
| **MCP-first** | All ops via the `claude.ai Nexudus` MCP server's `nexudus_list`/`nexudus_create`/`nexudus_update`/`nexudus_delete` tools, called by the agent — not a shell CLI. Operator already authenticated via the MCP connector. |
| **No name markers** | Records look like real data (no `[TEST]` prefixes). Safety comes from `data/created-ids/<entity>.json`, which tracks every created record's Id for teardown. |
| **Additive** | Creates new data regardless of existing records. No clean-slate assumption. |
| **Workflow-aware** | Cancelled bookings come from deleting active bookings. Invoices from billing cycles. Ledger entries from invoice/payment actions. Proposals accepted via status update. |

---

## 2. Target Volumes (Small Profile)

> This table is the original target; the `CLI command` column is illustrative of the operation (translate to `nexudus_list`/`nexudus_create`/etc. — see §1). Actual built counts match unless noted; see §3 for the few that shifted.

### Layer 0 — Reference & Configuration (`00_reference.py`, ✅ built)

| Entity | CLI command | Count | Notes |
|--------|-------------|-------|-------|
| **Business** | `businesses list` | 3 | Query existing IDs. Multi-location filtering. Note: the connected test account currently has exactly 1 business (§9). |
| **TaxRate** | `taxrates` | 3 | UK: Standard 20%, Reduced 5%, Zero-rated 0%. |
| **FinancialAccount** | `financialaccounts` | 8 | See §4h. Assigned to products/services/tariffs. |
| **ResourceType** | `resourcetypes` | 5 | Meeting Room, Hot Desk, Private Office, Phone Booth, Parking. |

### Layer 1 — Structural Setup (`01_structural.py`, ✅ built)

| Entity | CLI command | Count | Notes |
|--------|-------------|-------|-------|
| **Team** | `teams` | 5 | Across locations. |
| **Tariff** (Plan) | `tariffs` | 8 | Varied billing frequency + target prices. FinancialAccount + TaxRate assigned. §4b. |
| **Product** | `products` | 12 (built 12, not 15 — see §4h's actual list) | Add-ons, deposits, day passes, credit bundles. Each has FinancialAccount + TaxRate. §4h. |
| **ExtraService** | `extraservices` | 7 (was 6 — added Printing Credit for §4e) | Per resource type + Time/Printing Credit. FinancialAccount + TaxRate linked. |
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

### Layer 2 — People (`02_people.py`, ✅ built)

| Entity | CLI command | Count | Notes |
|--------|-------------|-------|-------|
| **Coworker** | `coworkers` | 60 | Spread across teams/locations. Mix lifecycle states. Engagement fields set. §4o. |
| **Visitor** | `visitors` | 60 | VisitorSource 1/2/3. Mix departed/on-site. |

### Layer 3 — Contracts & Occupancy (`03_contracts.py`, ✅ built)

| Entity | CLI command | Count | Notes |
|--------|-------------|-------|-------|
| **CoworkerContract** | `coworkercontracts` | 90 | All lifecycle scenarios §4a. 27 have ContractTerm set (6/12/24mo). §4p. |
| **ContractProduct** | `contractproducts` | 30 | Recurring add-ons. Linked to Products with FinancialAccount. |
| **ContractSchedule** | `contractschedules` | 8 | Future price changes. Attached inline via `CoworkerContract.ContractSchedules` at create time, not a separate call. |
| **ContractPausedPeriod** | `contractpausedperiods` | 12 | Past, current, future freezes, month-boundary aligned. |
| **ContractDeposit** | `contractdeposits` | 10 | Mix refundable/non-refundable. Linked to deposit Product. §4k. |
| **CoworkerInventoryAsset** | `coworkerinventoryassets` | 12 | Lockers/equipment assigned to members. §4n. |
| **FloorPlanDesk assign** | `floorplandesks update` | ~28 | Set CoworkerId on occupied units. |

### Layer 4a — Activity (`04_activity.py`, ✅ built)

| Entity | CLI command | Count | Notes |
|--------|-------------|-------|-------|
| **Booking** | `bookings` | 240 | 200 kept + 40 to be cancelled, 10 recurring. §4g + §4j. |
| **Booking → Cancel** | `bookings delete` | 40 | Deleting creates CancelledBooking snapshots. §4j. |
| **BookingProduct** | (inline on booking) | 36 | Add-ons at booking creation. |
| **BookingVisitor** | `bookingvisitors` | ~82 links on 50 bookings | External attendees (1–3 per applicable booking). Standalone create with a real VisitorId, not inline. |
| **CheckIn** | `checkins` | 300 | ~40 of 60 coworkers, heavy/light frequency. Some open (no ToTime). |
| **CoworkerExtraService** | `coworkerextraservices` | 80 | 47 booking charges + 25 time credits + 8 printing credits. §4e. |
| **CoworkerBookingCredit** | `coworkerbookingcredits` | 25 | Monetary wallets. Active/expired/near-expiry. Moved here from Layer 5. |
| **CoworkerBookingCreditUseHistory** | `coworkerbookingcreditusehistories` | 50 | Spend transactions. Moved here from Layer 5. |
| **CoworkerTimePass** | `coworkertimepasses` | 40 | Used/unused. 20 marked Used via follow-up update. |
| **CoworkerProduct** | `coworkerproducts` | 20 | Recurring product subscriptions. |

### Layer 4b — Community (`05_community.py`, ⬜ not yet built)

| Entity | CLI command | Count | Notes |
|--------|-------------|-------|-------|
| **CoworkerDelivery** | `coworkerdeliveries` | 40 | Mix of DeliveryType 1–5. Some collected, some pending. §4q. |
| **CalendarEvent** | `calendarevents` | 20 | Mix: one-off + recurring. Past + upcoming. §4r. |
| **EventAttendee** | `eventattendees` | 60 | 3–5 per event. Mix checked-in/not. |
| **HelpDeskMessage** | `helpdeskmessages` | 25 | Mix open/closed. Varied priorities. §4s. |
| **CommunityThread** | `communitythreads` | 15 | Message board posts. Across groups. §4t. |
| **CommunityMessage** | `communitymessages` | 40 | Replies on threads (2–4 per thread). |
| **BlogPost** | `blogposts` | 10 | Published articles. Spread over 12 months. §4u. |
| **CoworkerTask** | `coworkertasks` | 20 | Mix completed/pending. Varied due dates. §4v. |

### Layer 5 — Financial Records & CRM (`06_financial.py`, `07_crm_proposals.py`, ⬜ not yet built)

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

**Status:** ✅ built = generator exists and dry-run verified; ⬜ not yet built. See §8 for the phase log.

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

Layer 4b — Community  ⬜ (05_community.py — not yet built)
├── CoworkerDelivery × 40 (refs: Coworker, Business)
├── CalendarEvent × 20 (refs: Business, Resource, CalendarEventCategory)
├── EventAttendee × 60 (refs: CalendarEvent, Coworker)
├── HelpDeskMessage × 25 (refs: Coworker, Business, HelpDeskDepartment)
├── CommunityThread × 15 (refs: Coworker, CommunityGroup, Business)
├── CommunityMessage × 40 (refs: CommunityThread, Coworker)
├── BlogPost × 10 (refs: Business)
└── CoworkerTask × 20 (refs: Coworker, Business)

Layer 5 — Financial & CRM  ⬜ (06_financial.py, 07_crm_proposals.py — not yet built)
├── Trigger invoice generation (billing cycle commands on contracts)
├── Pay/void/credit invoices → ledger entries auto-generated
├── CrmOpportunity × 30 (refs: CrmBoardColumn, Coworker)
│   └── CrmOpportunityHistory × 60 (refs: CrmOpportunity, CrmBoardColumn)
├── Proposal × 15 (refs: Coworker, Tariff, CrmOpportunity context, AdminUserId)
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

**Implementation note:** the pattern above alone produces 79 contracts (60 coworkers, several with 2). To hit the original 90-contract target, `prebuild.py` pads a handful of coworkers with one extra contract each, staying inside each scenario's narrative: 3 long-term-actives get a secondary add-on plan, 2 churned members get a prior plan that fed into the one they churned from, 3 multi-contract members get a third concurrent contract, and all 3 ending-soon members get the earlier contract they renewed from (`CancellationReason=Renewed`). Also note: `CoworkerContract` has no `EndDate` field — "contract ends" in this table means setting `CancellationDate`.

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
| **With products** | ~15% (36 bookings) have BookingProduct records — catering, AV, etc. Attached inline on the booking create call. |
| **With guests** | ~25% (50 bookings) have 1–3 BookingVisitor records each (~82 links total) — external attendees. Created standalone with a real `VisitorId`, not via Booking's inline `BookingVisitors` (that path writes `VisitorFullName`/`VisitorEmail`, which are read-only on the standalone entity, so the inline route's actual resolution behavior is ambiguous — safer to link an existing Visitor directly). |
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
1. Create 240 bookings total (200 to keep + 40 to cancel), including inline BookingProducts and standalone BookingVisitor guests where applicable
2. Delete the 40 selected bookings via `nexudus_delete("bookings", <id>)` — recurring bookings are never in this set (they're deleted via `WhichBookingsToUpdate`, not a plain delete, and aren't part of this workflow)
3. System automatically creates CancelledBooking records preserving all metadata

**Implementation status:** built in `04_activity.py`, dry-run verified for the create + delete call sequence. Not yet confirmed against a live instance that delete actually produces a CancelledBooking snapshot — the Nexudus entity guide for `bookings` doesn't explicitly document this behavior (see gotcha below); it's asserted by house convention (originally CLAUDE.md rule 11). Worth a spot-check once this layer runs live.

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
0 9 * * * cd /path/to/data-generator && python generators/daily_update.py
```

Or run on-demand before reviewing dashboards that show "today" data.

---

## 6. Repo Structure (as built)

```
data-generator/
├── CLAUDE.md                    ← Agent instructions — source of truth, supersedes §7 below
├── README.md                    ← Human overview
├── config.py                    ← Volumes, rolling date helpers, TEST_NAME_PREFIX="", scale multiplier
├── requirements.txt             ← faker, python-dateutil
├── prebuild.py                  ← Generates every data/*.json below from faker + scenario logic.
│                                   Run once (or on re-seed with a new --seed); output is committed.
├── data/
│   ├── coworkers.json                    ← Layer 2
│   ├── visitors.json                     ← Layer 2
│   ├── contracts.json                    ← Layer 3
│   ├── contract_products.json            ← Layer 3
│   ├── contract_schedules.json           ← Layer 3 (attached inline, not a separate API call)
│   ├── contract_paused_periods.json      ← Layer 3
│   ├── contract_deposits.json            ← Layer 3
│   ├── coworker_inventory_assets.json    ← Layer 3
│   ├── desk_assignments.json             ← Layer 3
│   ├── bookings.json                     ← Layer 4a
│   ├── checkins.json                     ← Layer 4a
│   ├── extra_services.json               ← Layer 4a
│   ├── booking_credits.json              ← Layer 4a
│   ├── credit_use_history.json           ← Layer 4a
│   ├── time_passes.json                  ← Layer 4a
│   ├── coworker_products.json            ← Layer 4a
│   └── created-ids/             ← Runtime: every created record's Id, per entity — the actual
│                                   safety net for teardown (gitignored, not committed)
├── generators/
│   ├── __init__.py
│   ├── base.py                  ← BaseGenerator: idempotency, ID tracking, dry-run
│   ├── 00_reference.py          ← ✅ Layer 0: tax rates, financial accounts, resource types, admin user id
│   ├── 01_structural.py         ← ✅ Layer 1: tariffs, products, resources, desks, inventory, discounts, CRM
│   ├── 02_people.py             ← ✅ Layer 2: coworkers (engagement fields), visitors
│   ├── 03_contracts.py          ← ✅ Layer 3: contracts (ContractTerm inline schedules), deposits, freezes, occupancy
│   ├── 04_activity.py           ← ✅ Layer 4a: bookings, check-ins, credits, passes
│   ├── 05_community.py          ← ⬜ Layer 4b: deliveries, events, help desk, threads, blogs, tasks
│   ├── 06_financial.py          ← ⬜ Layer 5: invoice triggering, payments, ledger supplements
│   ├── 07_crm_proposals.py      ← ⬜ Layer 5: opportunities, proposals, document files
│   └── daily_update.py          ← ⬜ Daily: fresh check-ins, bookings, visitors, deliveries
├── scripts/
│   ├── seed_all.sh              ← Run all layer generators in order
│   ├── seed_layer.sh <N>        ← Run one specific layer
│   ├── daily.sh                 ← Run daily_update.py (for cron/manual use)
│   ├── teardown.sh              ← Stub — delete loops over data/created-ids/*.json still TODO
│   └── verify.sh                ← Count records per entity, validate targets
└── reference/
    ├── entity-dependencies.md   ← Dependency graph (copy of §3)
    ├── field-enums.md           ← Enum values, validated against live Nexudus schemas
    ├── financial-accounts.md    ← Account chart with codes
    └── tax-rates.md             ← UK tax rate definitions
```

Each layer generator's `run()` takes `nexudus_list`/`nexudus_create`/`nexudus_update`/(`nexudus_delete` where needed) callables plus the previous layer's output dict, and returns its own output dict merged on top — chaining IDs (business_id, admin_user_id, tariff_ids, coworker_ids, ...) forward through the pipeline. In dry-run mode each file's `__main__` block builds a mock chain and no-op callables so it's independently testable; live mode requires an agent with the Nexudus MCP connector to supply real callables (there's no standalone "run everything end-to-end" entry point yet — `seed_all.sh` invokes each script's dry-run-capable path, but a live run is currently agent-orchestrated, not a single shell command).

---

## 7. Agent Instructions

Superseded by the repo's top-level `CLAUDE.md` — that file is the live, MCP-based source of truth for key commands and standing rules, and is kept current as generators are built (it does not go stale the way a doc-embedded copy would). Refer to it directly rather than to a snapshot here.

What's worth keeping in this doc instead is the set of non-obvious Nexudus API behaviors discovered while building Layers 0-4a, since they informed real code decisions and aren't written down anywhere else:

- **No "whoami" MCP tool.** Business/currency/country/timezone defaults come from `nexudus_list("businesses", {})` on the single business this account is scoped to, not a dedicated whoami call.
- **`CoworkerContract.IssuedById` is required** and has no sensible default -- resolve it from `nexudus_list("users", {})`, picking the `IsAdmin=true` record tied to the business, at run time (not hardcoded). Same field is required on `Proposal` (Layer 5, not yet built).
- **`ContractSchedule` is the only inline child `CoworkerContract` supports** (`ContractSchedules: [{Price, ApplyOn}]` on create). `ContractDeposit` and `ContractPausedPeriod` looked like plausible inline candidates but are NOT -- they need separate `nexudus_create` calls after the parent contract exists.
- **`ContractPausedPeriod` dates must align to billing-cycle boundaries** (first-of-month for monthly plans), not arbitrary dates -- the entity guide is explicit about this.
- **`Booking` supports two real inline children**, `BookingProducts` and `BookingVisitors` -- but the inline `BookingVisitors` path writes `VisitorFullName`/`VisitorEmail`, fields that are read-only on the standalone `BookingVisitor` entity. That mismatch means the inline path's resolution behavior isn't fully pinned down from the schema alone, so guests are created standalone with a real `VisitorId` instead once each booking has an Id.
- **`CoworkerTimePass.Used` is `updateOnly`** -- you cannot mark a pass used at create time; create it, then a follow-up `nexudus_update` sets `Used=true` + `UsedDate`.
- **`CoworkerBookingCredit` has a literal typo baked into the API**: the field is `CaneBeUsedForBookings`, not `CanBeUsedForBookings`. Not a bug in generator code -- matches the live schema.
- **`CoworkerExtraService` does not require a `BookingId`** -- time/printing credits are free-standing allowances (`TotalUses`, no booking link); `BookingId` is only set when the record represents a specific per-booking charge.
- **Deleting a `Booking` to produce a `CancelledBooking`** (§4j) is asserted by house convention, not confirmed in the `bookings` entity guide text -- worth a spot-check on first live run.
- **`FloorPlanDesk.CoworkerId`** is a plain writable field, settable via `nexudus_update`; there's no separate "occupied" boolean, so `Available=false` is set alongside it to represent occupancy.

---

## 8. Implementation Phases

| Phase | What | Dependencies | Status |
|-------|------|-------------|--------|
| **Phase 1** | Repo setup: structure, config.py, base.py, reference docs | None | ✅ Done |
| **Phase 2** | Layer 0 + Layer 1: Tax, accounts, tariffs, products, resources, desks, inventory, discounts, CRM boards, help desk depts | None | ✅ Done (`00_reference.py`, `01_structural.py`) |
| **Phase 3** | Layer 2: Coworkers (60, with engagement fields) + Visitors (60) | Phase 2 | ✅ Done (`02_people.py`) |
| **Phase 4** | Layer 3: Contracts (with ContractTerm), deposits, freezes, inventory assignments, occupancy | Phase 3 | ✅ Done (`03_contracts.py`) |
| **Phase 5** | Layer 4a: Bookings (recurring + products + guests) + cancellations, check-ins, credits, passes | Phase 4 | ✅ Done (`04_activity.py`) |
| **Phase 6** | Layer 4b: Deliveries, events + attendees, help desk, community, blogs, tasks | Phase 4 | ⬜ Not started (`05_community.py`) |
| **Phase 7** | Layer 5: Invoice generation + payments, CRM opportunities, proposals | Phase 5 | ⬜ Not started (`06_financial.py`, `07_crm_proposals.py`) |
| **Phase 8** | Daily update script + verification + teardown scripts | Phase 7 | ⬜ Not started (`daily_update.py`, `verify.sh`/`teardown.sh` are stubs) |
| **Phase 9** | Run against test instance, validate all report widgets populate | Phase 8 | ⬜ Not started — nothing has been pushed live yet; every layer above is dry-run verified only |

**Notable deviations from the original plan, by phase:**
- **Phase 1:** `prebuild.py` added as a step that didn't exist in the original design — see §1 and §6.
- **Phase 2:** added a Printing Credit `ExtraService` (§4e needed it, wasn't in the original Layer 1 list).
- **Phase 4:** contract count landed at 79 from the scenario math in §4a alone; padded to 90 (matching the original target) with a few extra contracts layered onto existing scenarios — see §4a note below. `ContractSchedule` turned out to be an inline child of `CoworkerContract`, not a separate create step.
- **Phase 5:** `CoworkerBookingCredit` and `CoworkerBookingCreditUseHistory` were originally planned for Layer 5 (§3) but have no invoice dependency, so they were built here instead, alongside the other credit/pass entities.
- **No name-prefix test markers** (all phases): `[TEST]` prefixes were removed from all generated data so records look like real data; see §1 and §6.

---

## 9. Things You Might Have Missed

These are additional considerations surfaced during analysis. Status now distinguishes **✅ Built** (code exists, dry-run verified) from **📋 Planned** (designed here, not yet built):

| Item | Status | Notes |
|------|--------|-------|
| **Recurring bookings** | ✅ Built | 10 bookings with `Repeats` > 0. §4g. |
| **Booking products (sold with booking)** | ✅ Built | 36 bookings with inline BookingProduct records. §4g. |
| **Booking guests** | ✅ Built | ~82 BookingVisitor records across 50 bookings (1–3 each), created standalone. §4g. |
| **Lockers & Equipment** | ✅ Built | 15 InventoryAssets + 12 assignments. §4n. |
| **ContractTerm (minimum term)** | ✅ Built | 27 of 90 contracts. §4p. |
| **Contract Value field** | ✅ Built | Benchmark ≠ Price on 20 contracts. §4p. |
| **Engagement / Churn fields** | ✅ Built (⚠️ still unvalidated) | ChurnProbability + EngagementLevel are set in `02_people.py`'s request body, but never confirmed against a live `nexudus_describe_entity("coworkers")` response — could silently no-op if the fields don't exist. §4o. |
| **Deliveries** | 📋 Planned | Layer 4b, `05_community.py`. §4q. |
| **Events** | 📋 Planned | Layer 4b, `05_community.py`. §4r. |
| **Help Desk** | 📋 Planned | Layer 4b, `05_community.py`. §4s. |
| **Blog / Articles** | 📋 Planned | Layer 4b, `05_community.py`. §4u. |
| **Community / Message Board** | 📋 Planned | Layer 4b, `05_community.py`. §4t. |
| **Tasks** | 📋 Planned | Layer 4b, `05_community.py`. §4v. |
| **Daily update script** | 📋 Planned | `daily_update.py` not started. §5. |
| **Invoice line source diversity** | 📋 Planned | Layer 5, `06_financial.py` not started. |
| **Multiple locations** | ❌ Not applicable | The connected Nexudus MCP account (business "Explore 2.0", id 1421021016) has exactly **one** business — there's nothing to distribute across. `business_id` is used as a single scalar everywhere in the generators. Revisit if a multi-location instance is ever targeted. |
| **Resource availability** | Not yet addressed | Original idea: set `Available=false` on 2 resources for maintenance. Not implemented in `01_structural.py` yet. |
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
