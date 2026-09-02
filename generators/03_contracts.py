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
from config import (DATA_DIR, DESK_ITEM_TYPE_PLAN_BUCKET, DESK_PLAN_TYPES,
                    TEST_NAME_PREFIX, TODAY, to_utc_str)


class ContractsGenerator(BaseGenerator):
    entity_name = "contracts"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.contract_ids = {}  # ContractIndex -> Id
        self._desk_item_types_cache = None

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
        return json.loads(path.read_text(encoding="utf-8"))

    def run(self, nexudus_list, nexudus_create, nexudus_update, nexudus_get, prev_output):
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
        self._assign_desks(floor_plan_desk_ids, nexudus_update, nexudus_get)

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
                        "records in a row with none succeeding — skipping the rest of them rather than spending the wall-clock time to fail on each: %s", e,
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
                        "several records in a row with none succeeding — skipping the rest of them rather than spending the wall-clock time to fail on each: %s", e,
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
                        "several records in a row with none succeeding — skipping the rest of them rather than spending the wall-clock time to fail on each: %s", e,
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
                        "records in a row with none succeeding — skipping the rest of them rather than spending the wall-clock time to fail on each: %s", e,
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
                        "repeated several times in a row, skipping the rest of them rather than "
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
    # Floor plan unit occupancy — linked to the contract, not the person
    #
    # A unit used to be occupied by writing FloorPlanDesk.CoworkerId. That
    # models occupancy on the wrong record: what actually occupies an
    # office is the office *contract*, and the unit type has to match the
    # plan type (office unit <-> office plan, hot desk <-> hot desk/flex).
    # prebuild.py::generate_desk_assignments already pairs them that way and
    # now emits the ContractIndex to prove it; this writes the link. The
    # unit's CoworkerId then follows from the contract on its own — Nexudus
    # derives it and refuses a direct write once a unit is linked.
    #
    # The link is written from the CONTRACT side, not the unit side —
    # confirmed live (see CLAUDE.md rule 55). FloorPlanDesk.CoworkerContractIds
    # looks writable and is not: three of the four shapes originally probed
    # here were accepted and silently dropped, the fourth 500'd, which is
    # why an earlier run logged 28 successful assignments while every unit
    # stayed unlinked. CoworkerContract.AddedDesks is the write side, and it
    # must be a LIST — the same id as a bare string is accepted and dropped
    # exactly like the unit-side fields. Nexudus then populates the
    # contract's Desks / FloorPlanDeskIds / FloorPlanDeskNames and the
    # unit's own CoworkerContractIds itself.
    # ------------------------------------------------------------------

    @staticmethod
    def _contract_link_present(record, contract_id):
        """True if `record` (as returned by the update) actually came back
        carrying `contract_id` — the only proof a candidate shape stuck."""
        if not isinstance(record, dict):
            return False
        wanted = str(contract_id)
        for field in ("CoworkerContractIds", "Contracts", "CoworkerContractId"):
            value = record.get(field)
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, dict):
                        if str(item.get("Id")) == wanted:
                            return True
                    elif str(item) == wanted:
                        return True
            elif wanted in str(value).split(","):
                return True
        return False

    def _desk_item_types(self):
        """DeskName -> eFloorPlanItemType, read from Layer 1's own desk
        definitions so the two can't disagree about what a unit is."""
        if self._desk_item_types_cache is None:
            import importlib
            struct = importlib.import_module("generators.01_structural")
            self._desk_item_types_cache = {
                d["Name"]: d["ItemType"] for d in struct.FLOOR_PLAN_DESKS
            }
        return self._desk_item_types_cache

    def _contract_for_assignment(self, defn):
        """The contract that occupies this unit.

        Normally straight from the plan file's ContractIndex. Plan files
        written before the contract link existed only carry CoworkerIndex
        (prebuild is incremental and won't rewrite them — CLAUDE.md rule
        50), so fall back to re-deriving it here: that coworker's active
        contract whose plan type matches the unit type, or any active
        contract of theirs for a storage unit or room, which have no plan
        type of their own.
        """
        contract_index = defn.get("ContractIndex")
        if contract_index is not None:
            return contract_index

        item_type = self._desk_item_types().get(defn["DeskName"])
        bucket = DESK_ITEM_TYPE_PLAN_BUCKET.get(item_type)
        wanted = DESK_PLAN_TYPES.get(bucket) if bucket else None

        for c in self.contract_defs:
            if c["CoworkerIndex"] != defn.get("CoworkerIndex"):
                continue
            cancelled = c.get("CancellationDayOffset")
            if cancelled is not None and cancelled <= 0:
                continue
            if wanted is None:
                return c["index"]
            if c["TariffName"][len(TEST_NAME_PREFIX):] in wanted:
                return c["index"]
        return None

    def _assign_desks(self, floor_plan_desk_ids, nexudus_update, nexudus_get):
        self.log.info("--- Desk Assignments (%d) ---", len(self.desk_assignment_defs))

        for defn in self.desk_assignment_defs:
            track_key = str(defn["index"])
            desk_name = defn["DeskName"]

            desk_id = floor_plan_desk_ids.get(desk_name)
            if desk_id is None:
                self.log.warning("Skipping desk assignment #%d — unit '%s' was never created",
                                  defn["index"], desk_name,
                                  skip=True, entity="floorplandesks", reason="parent_skipped")
                continue

            contract_index = self._contract_for_assignment(defn)
            contract_id = self.contract_ids.get(contract_index) if contract_index else None
            if contract_id is None:
                self.log.warning("Skipping desk assignment #%d — no contract for unit '%s' "
                                  "(coworker #%s); its contract was never created",
                                  defn["index"], desk_name, defn.get("CoworkerIndex"),
                                  skip=True, entity="floorplandesks", reason="parent_skipped")
                continue

            already = self.already_created("DeskAssignmentIndex", track_key, entity="floorplandesks")
            if already and not self._needs_reassignment(desk_id, contract_id,
                                                        nexudus_get, nexudus_update):
                self.log.info("Desk assignment #%d already tracked", defn["index"])
                continue
            if already:
                self.log.info("Re-applying desk assignment #%d — unit '%s' is not linked to its "
                              "contract", defn["index"], desk_name)

            result = self._link_desk_to_contract(desk_id, contract_id, defn,
                                                 nexudus_update, nexudus_get)
            if result is None:
                continue
            if result == "systemic":
                break

            # Never track in dry-run: track_id has no dry_run guard of its
            # own, so the synthetic record above would land in
            # data/created-ids/ as a real assignment (rule 56).
            if not already and not self.dry_run:
                self.track_id({
                    "entity": "floorplandesks", **result,
                    "DeskAssignmentIndex": track_key,
                })
            self.log.info("Linked unit '%s' to contract #%s (coworker #%s)",
                          desk_name, contract_index, defn.get("CoworkerIndex"))

    def _needs_reassignment(self, desk_id, contract_id, nexudus_get, nexudus_update):
        """Whether a unit assigned by an earlier run still needs its
        contract link written. Without this every unit assigned before the
        move to the contract-side link would keep its person link forever,
        since already_created() would skip it every run.

        A unit that IS already linked but still reads Available=False is
        normalised here rather than reported as needing reassignment —
        re-adding an already-added desk to its contract isn't worth the
        write."""
        if self.dry_run:
            return False
        try:
            current = nexudus_get("floorplandesks", desk_id)
        except Exception as e:  # noqa: BLE001
            self.log.warning("Could not re-check unit %s (%s) — leaving it as it is", desk_id, e)
            return False
        if not self._contract_link_present(current, contract_id):
            return True
        self._assert_unit_available(desk_id, current, nexudus_update)
        return False

    def _link_desk_to_contract(self, desk_id, contract_id, defn, nexudus_update, nexudus_get):
        """Occupy a unit by adding it to its contract's AddedDesks.

        Returns the unit's record as it reads back after the write (what
        gets tracked), None to skip this unit, or "systemic" to stop.
        """
        if self.dry_run:
            self.log.info("WOULD UPDATE coworkercontracts %s: %s",
                          contract_id, json.dumps({"AddedDesks": [desk_id]}))
            return {"Id": desk_id, "CoworkerContractIds": str(contract_id)}

        try:
            nexudus_update("coworkercontracts", contract_id, {"AddedDesks": [desk_id]})
        except Exception as e:  # noqa: BLE001
            verdict = self.classify_failure("floorplandesks", e)
            if verdict == "systemic":
                self.log.warning(
                    "Stopping desk assignment — this error has repeated several "
                    "records in a row with none succeeding — skipping the rest of them rather than spending the wall-clock time to fail on each: %s", e,
                    skip=True, entity="floorplandesks", reason="systemic_rate_limit")
                return "systemic"
            self.log.warning("Skipping desk assignment #%d — linking unit to contract %s failed: %s",
                              defn["index"], contract_id, e,
                              skip=True, entity="floorplandesks", reason="unknown_error")
            return None

        # AddedDesks is write-only and the update echoes the contract, not
        # the unit, so the unit itself is the only place to confirm the
        # link landed. A silent no-op here is exactly the failure mode the
        # old unit-side shapes had, so it's checked rather than assumed.
        try:
            record = nexudus_get("floorplandesks", desk_id)
        except Exception as e:  # noqa: BLE001
            self.log.warning("Linked unit %s to contract %s but could not read it back (%s) — "
                             "tracking it anyway", desk_id, contract_id, e)
            return {"Id": desk_id}

        if not self._contract_link_present(record, contract_id):
            self.log.warning(
                "Skipping desk assignment #%d — AddedDesks was accepted for contract %s but "
                "unit %s did not come back carrying the link. Capture the admin UI's own "
                "request when assigning a unit to a contract (see CLAUDE.md rules 27/55).",
                defn["index"], contract_id, desk_id,
                skip=True, entity="floorplandesks", reason="unknown_error")
            return None

        self._assert_unit_available(desk_id, record, nexudus_update)
        return record

    def _assert_unit_available(self, desk_id, record, nexudus_update):
        """Undo the Available=False the pre-contract-link behaviour set to
        mark occupancy — that's the contract link's job now.

        The unit's CoworkerId is deliberately left alone: once a unit is
        linked to a contract Nexudus derives the person from it (confirmed
        live — every linked unit's CoworkerId matches its contract's own
        coworker) and rejects a direct write with "This is desk is linked to
        a contract. To change the person it is assigned to you should change
        that contract." Nulling it here is both impossible and pointless.
        """
        if record.get("Available") is not False:
            return
        try:
            nexudus_update("floorplandesks", desk_id, {"Available": True})
        except Exception as e:  # noqa: BLE001
            self.log.warning("Could not mark unit %s available: %s", desk_id, e)


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
            nexudus_get=lambda entity, id: {"Id": id},
            prev_output=mock_prev,
        )
    else:
        import pipeline
        pipeline.run_up_to(3, business_id=args.business_id)
