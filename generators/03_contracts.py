"""
Layer 3 — Contracts & Occupancy

Reads pre-generated definitions from data/contracts.json + siblings,
then pushes them to the Nexudus API.

Creates:
- CoworkerContract (~79, lifecycle scenarios §4a — ~30% with ContractTerm,
  20 with a Value benchmark different from Price). ContractSchedule entries
  are attached inline at contract-creation time (the only inline child
  CoworkerContract supports).
- ContractProduct (30) — recurring add-ons on active contracts.
- ContractPausedPeriod (12) — past/current/future freezes, month-aligned.
- ContractDeposit (10) — office (£1000) + desk (£250) deposits, §4k.
- CoworkerInventoryAsset (12) — locker/monitor/standing-desk assignments, §4n.
- FloorPlanDesk.CoworkerId updates (~28) — occupancy, §4d.

Prerequisites: Layer 0 (financial refs), Layer 1 (tariffs, products,
inventory assets, floor plan desks), Layer 2 (coworkers).
Data files: Run `python prebuild.py` first to generate data/*.json.

Usage:
    python generators/03_contracts.py              # Live mode
    python generators/03_contracts.py --dry-run     # Log only
"""

import json
import sys
from datetime import date
from pathlib import Path

from dateutil.relativedelta import relativedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.base import BaseGenerator, parse_args
from config import DATA_DIR, TODAY, to_utc_str


class ContractsGenerator(BaseGenerator):
    entity_name = "contracts"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.contract_ids = {}  # ContractIndex -> Id

        self.contract_defs = self._load_data("contracts.json")
        self.contract_product_defs = self._load_data("contract_products.json")
        self.contract_schedule_defs = self._load_data("contract_schedules.json")
        self.contract_paused_period_defs = self._load_data("contract_paused_periods.json")
        self.contract_deposit_defs = self._load_data("contract_deposits.json")
        self.inventory_assignment_defs = self._load_data("coworker_inventory_assets.json")
        self.desk_assignment_defs = self._load_data("desk_assignments.json")

        # ContractIndex -> [schedule defs] for inline attachment on create
        self.schedules_by_contract = {}
        for defn in self.contract_schedule_defs:
            self.schedules_by_contract.setdefault(defn["ContractIndex"], []).append(defn)

        self.set_target("coworkercontracts", len(self.contract_defs))
        self.set_target("contractproducts", len(self.contract_product_defs))
        self.set_target("contractpausedperiods", len(self.contract_paused_period_defs))
        self.set_target("contractdeposits", len(self.contract_deposit_defs))
        self.set_target("coworkerinventoryassets", len(self.inventory_assignment_defs))
        self.set_target("floorplandesks", len(self.desk_assignment_defs))

    @staticmethod
    def _load_data(filename):
        path = DATA_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"{path} not found. Run 'python prebuild.py' first.")
        return json.loads(path.read_text())

    def run(self, nexudus_list, nexudus_create, nexudus_update, prev_output):
        biz = prev_output["business_id"]
        coworker_ids = prev_output["coworker_ids"]
        tariff_ids = prev_output["tariff_ids"]
        product_ids = prev_output["product_ids"]
        inventory_asset_ids = prev_output["inventory_asset_ids"]
        floor_plan_desk_ids = prev_output["floor_plan_desk_ids"]

        self._create_contracts(biz, coworker_ids, tariff_ids, nexudus_create)
        self._create_contract_products(product_ids, nexudus_create)
        self._create_contract_paused_periods(nexudus_create)
        self._create_contract_deposits(product_ids, nexudus_create)
        self._create_inventory_assignments(biz, coworker_ids, inventory_asset_ids, nexudus_create)
        self._assign_desks(coworker_ids, floor_plan_desk_ids, nexudus_update)

        self.log.info("Layer 3 complete. Contracts: %d", len(self.contract_ids))

        return {
            **prev_output,
            "contract_ids": self.contract_ids,
            "contract_defs": self.contract_defs,
        }

    # ------------------------------------------------------------------
    # Date helpers — day/month offsets resolved against config.TODAY
    # ------------------------------------------------------------------

    @staticmethod
    def _from_day_offset(offset):
        return TODAY + relativedelta(days=offset)

    @staticmethod
    def _first_of_month(month_offset):
        d = TODAY + relativedelta(months=month_offset)
        return date(d.year, d.month, 1)

    # ------------------------------------------------------------------
    # CoworkerContract (+ inline ContractSchedules)
    # ------------------------------------------------------------------
    def _create_contracts(self, biz, coworker_ids, tariff_ids, nexudus_create):
        self.log.info("--- Coworker Contracts (%d) ---", len(self.contract_defs))

        for defn in self.contract_defs:
            idx = defn["index"]
            track_key = str(idx)

            if self.already_created("ContractIndex", track_key, entity="coworkercontracts"):
                existing = next(r for r in self.get_tracked_ids() if r.get("ContractIndex") == track_key)
                self.contract_ids[idx] = existing["Id"]
                self.log.info("Contract #%d already tracked (id=%s)", idx, existing["Id"])
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping contract #%d — coworker #%d was never created (seat limit?)",
                                  idx, defn["CoworkerIndex"],
                                  skip=True, entity="coworkercontracts", reason="parent_skipped")
                continue

            start_date = self._from_day_offset(defn["StartDayOffset"])
            body = {
                # IssuedById is the Business ID, not a User ID — confirmed via
                # a live UI-created contract's IssuedByName resolving to the
                # business name, not the admin user's.
                "IssuedById": biz,
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "TariffId": tariff_ids[defn["TariffName"]],
                "BillingDay": defn["BillingDay"],
                "Quantity": 1,
                "StartDate": to_utc_str(start_date),
            }

            if defn.get("CancellationDayOffset") is not None:
                body["CancellationDate"] = to_utc_str(self._from_day_offset(defn["CancellationDayOffset"]))
                if defn.get("CancellationReason") is not None:
                    body["CancellationReason"] = defn["CancellationReason"]

            if defn.get("ContractTermMonths"):
                term_date = start_date + relativedelta(months=defn["ContractTermMonths"])
                body["ContractTerm"] = to_utc_str(term_date)

            if defn.get("PriceOverride") is not None:
                body["Price"] = defn["PriceOverride"]
            if defn.get("ValueOverride") is not None:
                body["Value"] = defn["ValueOverride"]

            schedules = self.schedules_by_contract.get(idx)
            if schedules:
                body["ContractSchedules"] = [
                    {
                        "Price": s["NewPrice"],
                        "ApplyOn": to_utc_str(self._from_day_offset(s["ApplyOnDayOffset"])),
                    }
                    for s in schedules
                ]

            if self.dry_run:
                self.log_would_create("coworkercontracts", body)
                self.contract_ids[idx] = f"DRY-CONTRACT-{idx}"
                continue

            try:
                result = nexudus_create("coworkercontracts", body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("coworkercontracts", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping contract creation — this error has repeated several "
                        "times in a row, likely an account-wide condition: %s", e,
                        skip=True, entity="coworkercontracts", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping contract #%d — create failed: %s", idx, e,
                                  skip=True, entity="coworkercontracts", reason="unknown_error")
                continue

            self.contract_ids[idx] = result["Id"]
            self.track_id({
                "entity": "coworkercontracts", **result,
                "ContractIndex": track_key, "Scenario": defn["Scenario"],
            })
            self.log.info("Created contract #%d [%s/%s] (id=%s)",
                          idx, defn["Scenario"], defn["TariffName"], result["Id"])

    # ------------------------------------------------------------------
    # ContractProduct
    # ------------------------------------------------------------------
    def _create_contract_products(self, product_ids, nexudus_create):
        self.log.info("--- Contract Products (%d) ---", len(self.contract_product_defs))

        for defn in self.contract_product_defs:
            track_key = str(defn["index"])
            if self.already_created("ContractProductIndex", track_key, entity="contractproducts"):
                self.log.info("ContractProduct #%d already tracked", defn["index"])
                continue

            contract_id = self.contract_ids.get(defn["ContractIndex"])
            if contract_id is None:
                self.log.warning("Skipping ContractProduct #%d — contract #%d not created",
                                  defn["index"], defn["ContractIndex"],
                                  skip=True, entity="contractproducts", reason="parent_skipped")
                continue

            body = {
                "CoworkerContractId": contract_id,
                "ProductId": product_ids[defn["ProductName"]],
                "Quantity": defn["Quantity"],
            }

            if self.dry_run:
                self.log_would_create("contractproducts", body)
                continue

            try:
                result = nexudus_create("contractproducts", body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("contractproducts", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping contract product creation — this error has repeated "
                        "several times in a row, likely an account-wide condition: %s", e,
                        skip=True, entity="contractproducts", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping contract product #%d — create failed: %s", defn["index"], e,
                                  skip=True, entity="contractproducts", reason="unknown_error")
                continue

            self.track_id({
                "entity": "contractproducts", **result,
                "ContractProductIndex": track_key,
            })
            self.log.info("Created contract product #%d on contract #%d (id=%s)",
                          defn["index"], defn["ContractIndex"], result["Id"])

    # ------------------------------------------------------------------
    # ContractPausedPeriod
    # ------------------------------------------------------------------
    def _create_contract_paused_periods(self, nexudus_create):
        self.log.info("--- Contract Paused Periods (%d) ---", len(self.contract_paused_period_defs))

        for defn in self.contract_paused_period_defs:
            track_key = str(defn["index"])
            if self.already_created("PausedPeriodIndex", track_key, entity="contractpausedperiods"):
                self.log.info("ContractPausedPeriod #%d already tracked", defn["index"])
                continue

            contract_id = self.contract_ids.get(defn["ContractIndex"])
            if contract_id is None:
                self.log.warning("Skipping paused period #%d — contract #%d not created",
                                  defn["index"], defn["ContractIndex"],
                                  skip=True, entity="contractpausedperiods", reason="parent_skipped")
                continue

            pause_from = self._first_of_month(defn["PauseFromMonthOffset"])
            pause_until = self._first_of_month(defn["PauseFromMonthOffset"] + defn["DurationMonths"])
            body = {
                "CoworkerContractId": contract_id,
                "PauseFrom": to_utc_str(pause_from),
                "PauseUntil": to_utc_str(pause_until),
            }

            if self.dry_run:
                self.log_would_create("contractpausedperiods", body)
                continue

            try:
                result = nexudus_create("contractpausedperiods", body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("contractpausedperiods", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping paused period creation — this error has repeated "
                        "several times in a row, likely an account-wide condition: %s", e,
                        skip=True, entity="contractpausedperiods", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping paused period #%d — create failed: %s", defn["index"], e,
                                  skip=True, entity="contractpausedperiods", reason="unknown_error")
                continue

            self.track_id({
                "entity": "contractpausedperiods", **result,
                "PausedPeriodIndex": track_key,
            })
            self.log.info("Created paused period #%d [%s] on contract #%d (id=%s)",
                          defn["index"], defn["Bucket"], defn["ContractIndex"], result["Id"])

    # ------------------------------------------------------------------
    # ContractDeposit
    # ------------------------------------------------------------------
    def _create_contract_deposits(self, product_ids, nexudus_create):
        self.log.info("--- Contract Deposits (%d) ---", len(self.contract_deposit_defs))

        for defn in self.contract_deposit_defs:
            track_key = str(defn["index"])
            if self.already_created("DepositIndex", track_key, entity="contractdeposits"):
                self.log.info("ContractDeposit #%d already tracked", defn["index"])
                continue

            contract_id = self.contract_ids.get(defn["ContractIndex"])
            if contract_id is None:
                self.log.warning("Skipping deposit #%d — contract #%d not created",
                                  defn["index"], defn["ContractIndex"],
                                  skip=True, entity="contractdeposits", reason="parent_skipped")
                continue

            body = {
                "CoworkerContractId": contract_id,
                "ProductId": product_ids[defn["ProductName"]],
                "Price": defn["Price"],
                "Refundable": defn["Refundable"],
            }

            if self.dry_run:
                self.log_would_create("contractdeposits", body)
                continue

            try:
                result = nexudus_create("contractdeposits", body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("contractdeposits", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping deposit creation — this error has repeated several "
                        "times in a row, likely an account-wide condition: %s", e,
                        skip=True, entity="contractdeposits", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping deposit #%d — create failed: %s", defn["index"], e,
                                  skip=True, entity="contractdeposits", reason="unknown_error")
                continue

            self.track_id({
                "entity": "contractdeposits", **result,
                "DepositIndex": track_key,
            })
            self.log.info("Created deposit #%d on contract #%d (id=%s)",
                          defn["index"], defn["ContractIndex"], result["Id"])

    # ------------------------------------------------------------------
    # CoworkerInventoryAsset
    # ------------------------------------------------------------------
    def _create_inventory_assignments(self, biz, coworker_ids, inventory_asset_ids, nexudus_create):
        self.log.info("--- Inventory Assignments (%d) ---", len(self.inventory_assignment_defs))

        for defn in self.inventory_assignment_defs:
            track_key = str(defn["index"])
            if self.already_created("InventoryAssignmentIndex", track_key, entity="coworkerinventoryassets"):
                self.log.info("Inventory assignment #%d already tracked", defn["index"])
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping inventory assignment #%d — coworker #%d was never created (seat limit?)",
                                  defn["index"], defn["CoworkerIndex"],
                                  skip=True, entity="coworkerinventoryassets", reason="parent_skipped")
                continue

            body = {
                "BusinessId": biz,
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "InventoryAssetId": inventory_asset_ids[defn["AssetName"]],
                "AssignedFrom": to_utc_str(self._from_day_offset(defn["AssignedFromDayOffset"])),
            }
            if defn.get("AssignedToDayOffset") is not None:
                body["AssignedTo"] = to_utc_str(self._from_day_offset(defn["AssignedToDayOffset"]))

            if self.dry_run:
                self.log_would_create("coworkerinventoryassets", body)
                continue

            try:
                result = nexudus_create("coworkerinventoryassets", body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("coworkerinventoryassets", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping inventory assignment creation — this error has "
                        "repeated several times in a row, likely an account-wide "
                        "condition: %s", e,
                        skip=True, entity="coworkerinventoryassets", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping inventory assignment #%d — create failed: %s", defn["index"], e,
                                  skip=True, entity="coworkerinventoryassets", reason="unknown_error")
                continue

            self.track_id({
                "entity": "coworkerinventoryassets", **result,
                "InventoryAssignmentIndex": track_key,
            })
            self.log.info("Assigned '%s' to coworker #%d (id=%s)",
                          defn["AssetName"], defn["CoworkerIndex"], result["Id"])

    # ------------------------------------------------------------------
    # FloorPlanDesk.CoworkerId occupancy
    # ------------------------------------------------------------------
    def _assign_desks(self, coworker_ids, floor_plan_desk_ids, nexudus_update):
        self.log.info("--- Desk Assignments (%d) ---", len(self.desk_assignment_defs))

        for defn in self.desk_assignment_defs:
            track_key = str(defn["index"])
            if self.already_created("DeskAssignmentIndex", track_key, entity="floorplandesks"):
                self.log.info("Desk assignment #%d already tracked", defn["index"])
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping desk assignment #%d — coworker #%d was never created (seat limit?)",
                                  defn["index"], defn["CoworkerIndex"],
                                  skip=True, entity="floorplandesks", reason="parent_skipped")
                continue

            desk_id = floor_plan_desk_ids[defn["DeskName"]]
            coworker_id = coworker_ids[defn["CoworkerIndex"]]
            body = {"CoworkerId": coworker_id, "Available": False}

            if self.dry_run:
                self.log.info("WOULD UPDATE floorplandesks %s: %s", desk_id, json.dumps(body))
                continue

            try:
                result = nexudus_update("floorplandesks", desk_id, body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("floorplandesks", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping desk assignment — this error has repeated several "
                        "times in a row, likely an account-wide condition: %s", e,
                        skip=True, entity="floorplandesks", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping desk assignment #%d — update failed: %s", defn["index"], e,
                                  skip=True, entity="floorplandesks", reason="unknown_error")
                continue

            self.track_id({
                "entity": "floorplandesks", **result,
                "DeskAssignmentIndex": track_key,
            })
            self.log.info("Assigned desk '%s' to coworker #%d",
                          defn["DeskName"], defn["CoworkerIndex"])


if __name__ == "__main__":
    args = parse_args()
    gen = ContractsGenerator(dry_run=args.dry_run)

    if gen.dry_run:
        import importlib
        struct = importlib.import_module("generators.01_structural")

        mock_coworker_ids = {i: f"DRY-CW-{i}" for i in range(1, 61)}
        mock_tariff_ids = {t["Name"]: f"DRY-TARIFF-{t['Name']}" for t in struct.TARIFFS}
        mock_product_ids = {p["Name"]: f"DRY-PROD-{p['Name']}" for p in struct.PRODUCTS}
        mock_inventory_ids = {a["Name"]: f"DRY-ASSET-{a['Name']}" for a in struct.INVENTORY_ASSETS}
        mock_desk_ids = {d["Name"]: f"DRY-DESK-{d['Name']}" for d in struct.FLOOR_PLAN_DESKS}

        mock_prev = {
            "business_id": "DRY-BIZ-1",
            "admin_user_id": "DRY-ADMIN-1",
            "coworker_ids": mock_coworker_ids,
            "tariff_ids": mock_tariff_ids,
            "product_ids": mock_product_ids,
            "inventory_asset_ids": mock_inventory_ids,
            "floor_plan_desk_ids": mock_desk_ids,
        }
        gen.run(
            nexudus_list=lambda entity, filters: [],
            nexudus_create=lambda entity, body: {"Id": f"DRY-{entity}-{body.get('CoworkerId', 'x')}-{body.get('TariffId', body.get('ProductId', 'x'))}"},
            nexudus_update=lambda entity, id, body: {"Id": id},
            prev_output=mock_prev,
        )
    else:
        import pipeline
        pipeline.run_up_to(3, business_id=args.business_id)
