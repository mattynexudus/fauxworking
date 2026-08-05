# Nexudus Test Data Generator

Generates and seeds realistic test data into a Nexudus instance via MCP tools. Populates all reports and dashboards with ~2,800+ records across 50+ entity types.

## Quick Start

```bash
pip install -r requirements.txt
bash scripts/seed_all.sh        # Run all layers in order
bash scripts/daily.sh           # Create today's fresh records
bash scripts/verify.sh          # Check record counts
```

## Rolling Date Window

All dates are **relative to the run date** — the data spans 24 months back from today. No hardcoded dates. Re-running on a different day shifts the entire window.

## Structure

| Directory | Purpose |
|-----------|---------|
| `config.py` | Volumes, rolling date helpers, scale multiplier, test markers |
| `generators/` | One Python file per layer (00–07) + daily update |
| `generators/base.py` | Shared base class: idempotency, ID tracking, test markers |
| `scripts/` | Shell wrappers: seed_all, seed_layer, daily, teardown, verify |
| `reference/` | Entity dependency graph, variance scenarios, enum values |
| `data/created-ids/` | JSON files tracking IDs of records created per entity |

## Layers

| Layer | Generator | What |
|-------|-----------|------|
| 0 | `00_reference.py` | Tax rates, financial accounts, resource types |
| 1 | `01_structural.py` | Tariffs, products, resources, desks, inventory, discounts, CRM boards |
| 2 | `02_people.py` | Coworkers + visitors |
| 3 | `03_contracts.py` | Contracts, deposits, freezes, inventory assignments, occupancy |
| 4 | `04_activity.py` | Bookings, check-ins, credits, passes |
| 4 | `05_community.py` | Deliveries, events, help desk, threads, blogs, tasks |
| 5 | `06_financial.py` | Invoice triggering, payments, ledger |
| 5 | `07_crm_proposals.py` | CRM opportunities, proposals |
| — | `daily_update.py` | Fresh daily records (check-ins, bookings, visitors, deliveries) |

## Scale

Default is "small" profile. Change `SCALE = "large"` in `config.py` for 3× volume.

## Test Markers

All test records use identifiable prefixes for safe teardown:
- Coworker emails: `test-NNN@seeddata.local`
- Resource/product/asset names: `[TEST] ...`
- Help desk subjects: `[TEST] ...`

## Teardown

```bash
bash scripts/teardown.sh   # Deletes only test-marked records (with confirmation)
```
