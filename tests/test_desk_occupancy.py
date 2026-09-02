"""
A floor plan unit is occupied by the *contract* whose plan type matches the
unit type, not by a person: prebuild pairs them, 03_contracts.py writes the
link from the contract side (CoworkerContract.AddedDesks), confirms it read
back on the unit, clears any leftover CoworkerId, and re-applies to units
assigned before this change.

No network — nexudus_update/get are local stubs, and the generator's data
files are stubbed in rather than read from data/.
"""

import importlib
import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import prebuild
from config import DESK_ITEM_TYPE_PLAN_BUCKET, DESK_PLAN_TYPES

contracts_mod = importlib.import_module("generators.03_contracts")
structural = importlib.import_module("generators.01_structural")

DESK_ITEM_TYPES = {d["Name"]: d["ItemType"] for d in structural.FLOOR_PLAN_DESKS}


def _contracts():
    """Two coworkers per plan type, enough to fill every occupied unit."""
    out = []
    idx = 0
    for plan in ("Private Office Small", "Private Office Large", "Private Office Annual",
                 "Dedicated Desk Monthly", "Hot Desk Monthly", "Hot Desk Quarterly",
                 "Flex Weekly", "Flex Fortnightly"):
        for _ in range(5):
            idx += 1
            out.append({"index": idx, "CoworkerIndex": idx, "TariffName": plan,
                        "TariffCategory": "desk", "CancellationDayOffset": None})
    return out


class TestPlannedPairing(unittest.TestCase):
    def setUp(self):
        self.contracts = _contracts()
        self.assignments = prebuild.generate_desk_assignments(random.Random(42), self.contracts)
        self.by_index = {c["index"]: c for c in self.contracts}

    def test_every_assignment_names_a_contract(self):
        self.assertTrue(self.assignments)
        for a in self.assignments:
            self.assertIn("ContractIndex", a)
            self.assertIn(a["ContractIndex"], self.by_index)

    def test_unit_type_matches_the_plan_type(self):
        checked = 0
        for a in self.assignments:
            bucket = DESK_ITEM_TYPE_PLAN_BUCKET.get(DESK_ITEM_TYPES[a["DeskName"]])
            if bucket is None:  # storage / rooms have no plan type of their own
                continue
            checked += 1
            self.assertIn(self.by_index[a["ContractIndex"]]["TariffName"],
                          DESK_PLAN_TYPES[bucket], f"{a['DeskName']} got the wrong plan")
        self.assertGreater(checked, 0)

    def test_coworker_index_still_matches_its_contract(self):
        for a in self.assignments:
            self.assertEqual(a["CoworkerIndex"],
                             self.by_index[a["ContractIndex"]]["CoworkerIndex"])

    def test_a_cancelled_contract_is_never_given_a_unit(self):
        contracts = _contracts()
        for c in contracts[:10]:
            c["CancellationDayOffset"] = -30
        assignments = prebuild.generate_desk_assignments(random.Random(1), contracts)
        cancelled = {c["index"] for c in contracts if c["CancellationDayOffset"] == -30}
        self.assertFalse({a["ContractIndex"] for a in assignments} & cancelled)


class _GenHarness(unittest.TestCase):
    def make_gen(self, assignments, links=True, live_record=None, tracked=(),
                 update_error=None):
        """links: whether the fake API actually persists AddedDesks — False
        reproduces the silent no-op the unit-side fields had."""
        contracts = _contracts()
        with patch.object(contracts_mod.ContractsGenerator, "_load_data",
                          staticmethod(lambda filename: [])):
            gen = contracts_mod.ContractsGenerator(dry_run=False)
        gen.contract_defs = contracts
        gen.desk_assignment_defs = assignments
        gen.contract_ids = {c["index"]: f"CONTRACT-{c['index']}" for c in contracts}
        gen.track_id = lambda record: None
        gen._save_ids = lambda: None
        gen.already_created = lambda key, value, entity=None: value in tracked

        self.writes = []
        self.linked = {}  # desk id -> contract id the fake API recorded

        def _update(entity, id, body):
            self.writes.append((entity, id, dict(body)))
            if update_error and entity == "coworkercontracts":
                raise RuntimeError(update_error)
            if entity == "coworkercontracts" and isinstance(body.get("AddedDesks"), list) and links:
                for desk in body["AddedDesks"]:
                    self.linked[desk] = id
            return {"Id": id}

        def _get(entity, id):
            record = dict(live_record) if live_record else {"Id": id}
            record.setdefault("Id", id)
            if id in self.linked:
                record["CoworkerContractIds"] = self.linked[id]
            return record

        self.update_fn, self.get_fn = _update, _get
        self.contracts = contracts
        self.desk_ids = {d["Name"]: f"DESK-{d['Name']}" for d in structural.FLOOR_PLAN_DESKS}
        return gen

    def contract_writes(self):
        return [(i, b) for e, i, b in self.writes if e == "coworkercontracts"]

    def desk_writes(self):
        return [(i, b) for e, i, b in self.writes if e == "floorplandesks"]


class TestWritingTheLink(_GenHarness):
    def setUp(self):
        self.assignments = prebuild.generate_desk_assignments(random.Random(42), _contracts())

    def test_the_link_is_written_on_the_contract_not_the_unit(self):
        gen = self.make_gen(self.assignments)
        gen._assign_desks(self.desk_ids, self.update_fn, self.get_fn)

        writes = self.contract_writes()
        self.assertEqual(len(writes), len(self.assignments))
        for a, (contract_id, body) in zip(self.assignments, writes):
            self.assertEqual(contract_id, f"CONTRACT-{a['ContractIndex']}")
            self.assertEqual(body, {"AddedDesks": [f"DESK-{a['DeskName']}"]})

    def test_added_desks_is_a_list_a_bare_id_is_silently_dropped_live(self):
        gen = self.make_gen(self.assignments[:1])
        gen._assign_desks(self.desk_ids, self.update_fn, self.get_fn)
        _id, body = self.contract_writes()[0]
        self.assertIsInstance(body["AddedDesks"], list)

    def test_a_unit_that_does_not_read_back_the_link_is_reported_not_tracked(self):
        gen = self.make_gen(self.assignments[:2], links=False)
        tracked = []
        gen.track_id = lambda record: tracked.append(record)
        gen._assign_desks(self.desk_ids, self.update_fn, self.get_fn)

        self.assertEqual(len(self.contract_writes()), 2, "each unit is still attempted")
        self.assertEqual(tracked, [], "a silent no-op must not be tracked as a success")

    def test_a_failed_contract_update_skips_that_unit_only(self):
        gen = self.make_gen(self.assignments[:3], update_error="boom")
        tracked = []
        gen.track_id = lambda record: tracked.append(record)
        gen._assign_desks(self.desk_ids, self.update_fn, self.get_fn)
        self.assertEqual(tracked, [])

    def test_a_unit_that_was_never_created_is_skipped_not_crashed(self):
        gen = self.make_gen(self.assignments)
        desk_ids = dict(self.desk_ids)
        desk_ids.pop(self.assignments[0]["DeskName"])
        gen._assign_desks(desk_ids, self.update_fn, self.get_fn)
        self.assertEqual(len(self.contract_writes()), len(self.assignments) - 1)

    def test_an_uncreated_contract_is_skipped(self):
        gen = self.make_gen(self.assignments)
        gen.contract_ids.pop(self.assignments[0]["ContractIndex"])
        gen._assign_desks(self.desk_ids, self.update_fn, self.get_fn)
        self.assertEqual(len(self.contract_writes()), len(self.assignments) - 1)


class TestUnitIsLeftAvailable(_GenHarness):
    def setUp(self):
        self.assignments = prebuild.generate_desk_assignments(random.Random(42), _contracts())[:2]

    def test_the_old_occupancy_flag_is_undone(self):
        gen = self.make_gen(self.assignments,
                            live_record={"CoworkerId": 999, "Available": False})
        gen._assign_desks(self.desk_ids, self.update_fn, self.get_fn)

        writes = self.desk_writes()
        self.assertEqual(len(writes), 2)
        for _id, body in writes:
            self.assertEqual(body, {"Available": True},
                             "occupancy is the contract link's job, not Available=False")

    def test_the_derived_person_link_is_never_written(self):
        """Nexudus derives CoworkerId from the contract once a unit is
        linked and rejects a direct write with a 400."""
        gen = self.make_gen(self.assignments,
                            live_record={"CoworkerId": 999, "Available": False})
        gen._assign_desks(self.desk_ids, self.update_fn, self.get_fn)
        for _id, body in self.desk_writes():
            self.assertNotIn("CoworkerId", body)

    def test_an_available_unit_is_not_written_to_at_all(self):
        gen = self.make_gen(self.assignments,
                            live_record={"CoworkerId": 999, "Available": True})
        gen._assign_desks(self.desk_ids, self.update_fn, self.get_fn)
        self.assertEqual(self.desk_writes(), [])


class TestDryRunTracksNothing(_GenHarness):
    def test_a_dry_run_never_writes_a_synthetic_id_into_tracking(self):
        """track_id has no dry_run guard of its own — a dry run used to
        leave 28 "DRY-Office Unit 01" entries behind (rule 56)."""
        assignments = prebuild.generate_desk_assignments(random.Random(42), _contracts())
        gen = self.make_gen(assignments)
        gen.dry_run = True
        tracked = []
        gen.track_id = lambda record: tracked.append(record)

        gen._assign_desks(self.desk_ids, self.update_fn, self.get_fn)

        self.assertEqual(tracked, [])
        self.assertEqual(self.writes, [], "a dry run must not call the API at all")


class TestLegacyPlanFile(_GenHarness):
    def test_contract_is_re_derived_when_the_plan_predates_the_link(self):
        planned = prebuild.generate_desk_assignments(random.Random(42), _contracts())
        legacy = [{k: v for k, v in a.items() if k != "ContractIndex"} for a in planned]

        gen = self.make_gen(legacy)
        gen._assign_desks(self.desk_ids, self.update_fn, self.get_fn)

        writes = self.contract_writes()
        self.assertEqual(len(writes), len(legacy))
        for a, (contract_id, _body) in zip(planned, writes):
            self.assertEqual(contract_id, f"CONTRACT-{a['ContractIndex']}")


class TestSelfHeal(_GenHarness):
    def setUp(self):
        self.assignments = prebuild.generate_desk_assignments(random.Random(42), _contracts())[:3]
        self.tracked = {"1", "2", "3"}

    def test_a_tracked_unit_with_no_contract_link_is_re_applied(self):
        gen = self.make_gen(self.assignments, live_record={"CoworkerId": 999},
                            tracked=self.tracked)
        gen._assign_desks(self.desk_ids, self.update_fn, self.get_fn)
        self.assertEqual(len(self.contract_writes()), 3)

    def test_a_tracked_unit_already_linked_is_not_re_added(self):
        contract_id = f"CONTRACT-{self.assignments[0]['ContractIndex']}"
        gen = self.make_gen(self.assignments[:1],
                            live_record={"CoworkerId": None, "Available": True,
                                         "CoworkerContractIds": [contract_id]},
                            tracked=self.tracked)
        gen._assign_desks(self.desk_ids, self.update_fn, self.get_fn)
        self.assertEqual(self.writes, [])

    def test_a_tracked_unit_already_linked_still_gets_made_available(self):
        contract_id = f"CONTRACT-{self.assignments[0]['ContractIndex']}"
        gen = self.make_gen(self.assignments[:1],
                            live_record={"CoworkerId": 999, "Available": False,
                                         "CoworkerContractIds": [contract_id]},
                            tracked=self.tracked)
        gen._assign_desks(self.desk_ids, self.update_fn, self.get_fn)
        self.assertEqual(self.contract_writes(), [], "no need to re-add an already-added desk")
        self.assertEqual(len(self.desk_writes()), 1)

    def test_an_unreadable_unit_is_left_alone_rather_than_rewritten(self):
        gen = self.make_gen(self.assignments, tracked=self.tracked)

        def _boom(entity, id):
            raise RuntimeError("gone")

        gen._assign_desks(self.desk_ids, self.update_fn, _boom)
        self.assertEqual(self.writes, [])


class TestDeskFigures(unittest.TestCase):
    def test_every_unit_has_area_size_capacity_and_target_value(self):
        for d in structural.FLOOR_PLAN_DESKS:
            self.assertTrue(d["Area"], f"{d['Name']} has no Area")
            self.assertGreater(d["Size"], 0, f"{d['Name']} has no Size")
            self.assertGreater(d["Capacity"], 0, f"{d['Name']} has no Capacity")
            self.assertGreater(d["Price"], 0, f"{d['Name']} has no target value")

    def test_backfill_only_writes_what_drifted(self):
        gen = structural.StructuralGenerator(dry_run=False)
        writes = []
        defn = structural.FLOOR_PLAN_DESKS[0]

        gen._backfill_desk_figures(
            {"Id": 1, "Area": defn["Area"], "Size": defn["Size"],
             "Capacity": defn["Capacity"], "Price": defn["Price"]},
            defn, lambda e, i, b: writes.append(b))
        self.assertEqual(writes, [], "an up-to-date unit should not be touched")

        gen._backfill_desk_figures(
            {"Id": 1, "Area": defn["Area"], "Size": defn["Size"],
             "Capacity": 0, "Price": defn["Price"]},
            defn, lambda e, i, b: writes.append(b) or {"Id": i})
        self.assertEqual(writes, [{"Capacity": defn["Capacity"]}])

    def test_backfill_fills_a_missing_value(self):
        gen = structural.StructuralGenerator(dry_run=False)
        writes = []
        defn = structural.FLOOR_PLAN_DESKS[0]
        gen._backfill_desk_figures(
            {"Id": 1, "Area": None, "Size": None, "Capacity": None, "Price": None},
            defn, lambda e, i, b: writes.append(b) or {"Id": i})
        self.assertEqual(writes[0],
                         {"Size": defn["Size"], "Capacity": defn["Capacity"],
                          "Price": defn["Price"], "Area": defn["Area"]})

    def test_a_unit_live_as_unavailable_is_made_available(self):
        """Every unit created before Available was sent at create time
        defaulted to False, and Layer 3 used to set it False deliberately."""
        gen = structural.StructuralGenerator(dry_run=False)
        writes = []
        defn = structural.FLOOR_PLAN_DESKS[0]
        tracked = {"Id": 1, "Area": defn["Area"], "Size": defn["Size"],
                   "Capacity": defn["Capacity"], "Price": defn["Price"]}

        gen._backfill_desk_figures(tracked, defn, lambda e, i, b: writes.append(b) or {"Id": i},
                                   live={"Id": 1, "Available": False})
        self.assertEqual(writes, [{"Available": True}])

    def test_a_unit_live_as_available_is_left_alone(self):
        gen = structural.StructuralGenerator(dry_run=False)
        writes = []
        defn = structural.FLOOR_PLAN_DESKS[0]
        tracked = {"Id": 1, "Area": defn["Area"], "Size": defn["Size"],
                   "Capacity": defn["Capacity"], "Price": defn["Price"]}

        gen._backfill_desk_figures(tracked, defn, lambda e, i, b: writes.append(b) or {"Id": i},
                                   live={"Id": 1, "Available": True})
        self.assertEqual(writes, [])

    def test_new_units_are_created_available(self):
        gen = structural.StructuralGenerator(dry_run=True)
        gen.floor_plan_ids = {f"{structural.TEST_NAME_PREFIX}{a}": f"FP-{a}"
                              for a in ("Ground Floor", "First Floor", "Mezzanine")}
        gen.already_created = lambda key, value, entity=None: False
        bodies = []
        gen.log_would_create = lambda entity, body: bodies.append(body)

        gen._create_floor_plan_desks("BIZ", lambda e, f: [], None, None)

        self.assertEqual(len(bodies), len(structural.FLOOR_PLAN_DESKS))
        for body in bodies:
            self.assertTrue(body["Available"], f"{body['Name']} is created unavailable")


if __name__ == "__main__":
    unittest.main()
