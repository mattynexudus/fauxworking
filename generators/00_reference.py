"""
Layer 0 — Reference & Configuration

Creates:
- Queries business IDs (via whoami)
- TaxRate × 3 (Standard 20%, Reduced 5%, Zero-rated 0%)
- FinancialAccount × 8 (UK accounting chart)
- ResourceType × 5 (Meeting Room, Hot Desk, Private Office, Phone Booth, Parking)

Prerequisites: None (this is the first layer).

Usage:
    python generators/00_reference.py              # Live mode
    python generators/00_reference.py --dry-run     # Log only
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.base import BaseGenerator, parse_args

# ---------------------------------------------------------------------------
# Data definitions — what to create
# ---------------------------------------------------------------------------

TAX_RATES = [
    {"Name": "Standard",    "Rate": 20, "ExemptionReason": 1},
    {"Name": "Reduced",     "Rate": 5,  "ExemptionReason": 1},
    {"Name": "Zero-rated",  "Rate": 0,  "ExemptionReason": 1},
]

FINANCIAL_ACCOUNTS = [
    {"Name": "Membership Revenue", "Code": "MEM-001", "AccountType": 1},
    {"Name": "Booking Revenue",    "Code": "BKG-001", "AccountType": 1},
    {"Name": "Product Sales",      "Code": "PRD-001", "AccountType": 1},
    {"Name": "Event Revenue",      "Code": "EVT-001", "AccountType": 1},
    {"Name": "Credit Sales",       "Code": "CRD-001", "AccountType": 1},
    {"Name": "Payment Receipts",   "Code": "PAY-001", "AccountType": 2},
    {"Name": "Deposit Holding",    "Code": "DEP-001", "AccountType": 3},
    {"Name": "Refund Account",     "Code": "REF-001", "AccountType": 2},
]

RESOURCE_TYPES = [
    {"Name": "Meeting Room"},
    {"Name": "Hot Desk"},
    {"Name": "Private Office"},
    {"Name": "Phone Booth"},
    {"Name": "Parking"},
]


class ReferenceGenerator(BaseGenerator):
    entity_name = "reference"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # These get populated during run()
        self.business_id = None
        self.currency_id = None
        self.country_id = None
        self.timezone_id = None
        self.tax_rate_ids = {}       # name -> id
        self.fin_account_ids = {}    # code -> id
        self.resource_type_ids = {}  # name -> id
        self.admin_user_id = None

    def run(self, nexudus_list, nexudus_create, whoami_data):
        """
        Execute Layer 0 creation.

        Args:
            nexudus_list: callable(entity, filters) -> list of records
            nexudus_create: callable(entity, body) -> created record
            whoami_data: dict from nexudus whoami with DefaultBusinessId, etc.
                Also carries "AdminUserId" — resolved by the caller at run time
                from whichever admin user is authenticated (e.g. nexudus_list("users",
                {}) filtered to IsAdmin=true), not a hardcoded value. Later layers
                need it for CoworkerContract.IssuedById / Proposal.IssuedById.
        """
        # Step 1: Extract defaults from whoami
        self.business_id = whoami_data["DefaultBusinessId"]
        self.currency_id = whoami_data["DefaultCurrencyId"]
        self.country_id = whoami_data.get("DefaultCountryId")
        self.timezone_id = whoami_data.get("DefaultSimpleTimeZoneId")
        self.admin_user_id = whoami_data.get("AdminUserId")
        self.log.info("Business: %s, Currency: %s", self.business_id, self.currency_id)

        # Step 2: Tax Rates
        self._create_tax_rates(nexudus_list, nexudus_create)

        # Step 3: Financial Accounts
        self._create_financial_accounts(nexudus_list, nexudus_create)

        # Step 4: Resource Types
        self._create_resource_types(nexudus_list, nexudus_create)

        self.log.info("Layer 0 complete. Tax rates: %d, Accounts: %d, Resource types: %d",
                      len(self.tax_rate_ids), len(self.fin_account_ids),
                      len(self.resource_type_ids))
        return {
            "business_id": self.business_id,
            "currency_id": self.currency_id,
            "country_id": self.country_id,
            "timezone_id": self.timezone_id,
            "admin_user_id": self.admin_user_id,
            "tax_rate_ids": self.tax_rate_ids,
            "fin_account_ids": self.fin_account_ids,
            "resource_type_ids": self.resource_type_ids,
        }

    def _create_tax_rates(self, nexudus_list, nexudus_create):
        self.log.info("--- Tax Rates ---")
        existing = nexudus_list("taxrates", {"TaxRate_Business": self.business_id})
        existing_by_name = {r["Name"]: r["Id"] for r in existing}

        for defn in TAX_RATES:
            name = defn["Name"]
            if name in existing_by_name:
                self.log.info("Tax rate '%s' already exists (id=%s)", name, existing_by_name[name])
                self.count_skip()
                self.tax_rate_ids[name] = existing_by_name[name]
                continue

            body = {**defn, "BusinessId": self.business_id}
            if self.dry_run:
                self.log_would_create("taxrates", body)
                self.tax_rate_ids[name] = f"DRY-{name}"
            else:
                result = nexudus_create("taxrates", body)
                self.tax_rate_ids[name] = result["Id"]
                self.track_id({"entity": "taxrates", "Id": result["Id"], "Name": name})
                self.log.info("Created tax rate '%s' (id=%s)", name, result["Id"])

    def _create_financial_accounts(self, nexudus_list, nexudus_create):
        self.log.info("--- Financial Accounts ---")
        existing = nexudus_list("financialaccounts", {"FinancialAccount_Business": self.business_id})
        existing_by_code = {r["Code"]: r["Id"] for r in existing}

        for defn in FINANCIAL_ACCOUNTS:
            code = defn["Code"]
            if code in existing_by_code:
                self.log.info("Account '%s' already exists (id=%s)", code, existing_by_code[code])
                self.count_skip()
                self.fin_account_ids[code] = existing_by_code[code]
                continue

            body = {**defn, "BusinessId": self.business_id}
            if self.dry_run:
                self.log_would_create("financialaccounts", body)
                self.fin_account_ids[code] = f"DRY-{code}"
            else:
                result = nexudus_create("financialaccounts", body)
                self.fin_account_ids[code] = result["Id"]
                self.track_id({"entity": "financialaccounts", "Id": result["Id"], "Code": code})
                self.log.info("Created account '%s' (id=%s)", code, result["Id"])

    def _create_resource_types(self, nexudus_list, nexudus_create):
        self.log.info("--- Resource Types ---")
        existing = nexudus_list("resourcetypes", {"ResourceType_Business": self.business_id})
        existing_by_name = {r["Name"]: r["Id"] for r in existing}

        for defn in RESOURCE_TYPES:
            name = defn["Name"]
            if name in existing_by_name:
                self.log.info("Resource type '%s' already exists (id=%s)", name, existing_by_name[name])
                self.count_skip()
                self.resource_type_ids[name] = existing_by_name[name]
                continue

            body = {**defn, "BusinessId": self.business_id}
            if self.dry_run:
                self.log_would_create("resourcetypes", body)
                self.resource_type_ids[name] = f"DRY-{name}"
            else:
                result = nexudus_create("resourcetypes", body)
                self.resource_type_ids[name] = result["Id"]
                self.track_id({"entity": "resourcetypes", "Id": result["Id"], "Name": name})
                self.log.info("Created resource type '%s' (id=%s)", name, result["Id"])


if __name__ == "__main__":
    args = parse_args()
    gen = ReferenceGenerator(dry_run=args.dry_run)

    if gen.dry_run:
        # In dry-run, simulate whoami data
        mock_whoami = {
            "DefaultBusinessId": "DRY-BIZ-1",
            "DefaultCurrencyId": "DRY-CUR-1",
            "DefaultCountryId": "DRY-COUNTRY-1",
            "DefaultSimpleTimeZoneId": "DRY-TZ-1",
            "AdminUserId": "DRY-ADMIN-1",
        }
        gen.run(
            nexudus_list=lambda entity, filters: [],
            nexudus_create=lambda entity, body: {"Id": f"DRY-{entity}-{body.get('Name', 'unknown')}"},
            whoami_data=mock_whoami,
        )
    else:
        import pipeline
        pipeline.run_up_to(0)
