"""
A floor plan unit is occupied by the *contract* whose plan type matches the
unit type, not by a person: prebuild pairs them, 03_contracts.py writes the
link, discovers which field carries it, clears any leftover CoworkerId, and
re-applies to units assigned before this change.

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
    def make_gen(self, assignments, accepted=None, live_record=None, tracked=()):
        """accepted: which link shape the fake API lets read back, or None
        for "nothing sticks"."""
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

        def _update(entity, id, body):
            self.writes.append((id, dict(body)))
            if accepted == "CoworkerContractIds-list" and isinstance(
                    body.get("CoworkerContractIds"), list):
                return {"Id": id, "CoworkerContractIds": body["CoworkerContractIds"]}
            if accepted == "Contracts-list" and isinstance(body.get("Contracts"), list):
                return {"Id": id, "Contracts": body["Contracts"]}
            return {"Id": id}

        def _get(entity, id):
            return dict(live_record or {"Id": id})

        self.update_fn, self.get_fn = _update, _get
        self.contracts = contracts
        self.desk_ids = {d["Name"]: f"DESK-{d['Name']}" for d in structural.FLOOR_PLAN_DESKS}
        self.coworker_ids = {c["CoworkerIndex"]: f"CW-{c['CoworkerIndex']}" for c in contracts}
        return gen


class TestWritingTheLink(_GenHarness):
    def setUp(self):
        self.assignments = prebuild.generate_desk_assignments(random.Random(42), _contracts())

    def test_writes_the_contract_and_clears_the_coworker(self):
        gen = self.make_gen(self.assignments, accepted="CoworkerContractIds-list")
        gen._assign_desks(self.coworker_ids, self.desk_ids, self.update_fn, self.get_fn)

        self.assertEqual(len(self.writes), len(self.assignments))
        for a, (desk_id, body) in zip(self.assignments, self.writes):
            self.assertEqual(desk_id, f"DESK-{a['DeskName']}")
            self.assertEqual(body["CoworkerContractIds"], [f"CONTRACT-{a['ContractIndex']}"])
            self.assertIsNone(body["CoworkerId"], "the person link must be cleared explicitly")
            self.assertFalse(body["Available"])

    def test_probes_shapes_until_one_reads_back_then_reuses_it(self):
        gen = self.make_gen(self.assignments, accepted="Contracts-list")
        gen._assign_desks(self.coworker_ids, self.desk_ids, self.update_fn, self.get_fn)

        # First unit tries the two CoworkerContractIds shapes, then Contracts.
        first_desk = self.writes[0][0]
        probes = [b for i, b in self.writes if i == first_desk]
        self.assertEqual(len(probes), 3)
        self.assertIn("Contracts", probes[-1])
        # Every later unit goes straight to the shape that worked.
        for _id, body in self.writes[3:]:
            self.assertIn("Contracts", body)

    def test_falls_back_to_the_person_link_once_when_nothing_sticks(self):
        gen = self.make_gen(self.assignments[:3], accepted=None)
        gen._assign_desks(self.coworker_ids, self.desk_ids, self.update_fn, self.get_fn)

        fallbacks = [b for _i, b in self.writes if b.get("CoworkerId")]
        self.assertEqual(len(fallbacks), 3, "each unit still gets an occupancy link")
        # The first unit probes all four shapes and then falls back; the
        # other two go straight to the fallback — the decision is made once
        # per run, not re-probed on every unit.
        shapes = len(contracts_mod.ContractsGenerator.DESK_CONTRACT_LINK_SHAPES)
        self.assertEqual(len(self.writes), shapes + 3)

    def test_a_unit_that_was_never_created_is_skipped_not_crashed(self):
        gen = self.make_gen(self.assignments, accepted="CoworkerContractIds-list")
        desk_ids = dict(self.desk_ids)
        desk_ids.pop(self.assignments[0]["DeskName"])
        gen._assign_desks(self.coworker_ids, desk_ids, self.update_fn, self.get_fn)
        self.assertEqual(len(self.writes), len(self.assignments) - 1)

    def test_an_uncreated_contract_is_skipped(self):
        gen = self.make_gen(self.assignments, accepted="CoworkerContractIds-list")
        gen.contract_ids.pop(self.assignments[0]["ContractIndex"])
        gen._assign_desks(self.coworker_ids, self.desk_ids, self.update_fn, self.get_fn)
        self.assertEqual(len(self.writes), len(self.assignments) - 1)


class TestLegacyPlanFile(_GenHarness):
    def test_contract_is_re_derived_when_the_plan_predates_the_link(self):
        planned = prebuild.generate_desk_assignments(random.Random(42), _contracts())
        legacy = [{k: v for k, v in a.items() if k != "ContractIndex"} for a in planned]

        gen = self.make_gen(legacy, accepted="CoworkerContractIds-list")
        gen._assign_desks(self.coworker_ids, self.desk_ids, self.update_fn, self.get_fn)

        self.assertEqual(len(self.writes), len(legacy))
        for a, (_id, body) in zip(planned, self.writes):
            self.assertEqual(body["CoworkerContractIds"], [f"CONTRACT-{a['ContractIndex']}"])


class TestSelfHeal(_GenHarness):
    def setUp(self):
        self.assignments = prebuild.generate_desk_assignments(random.Random(42), _contracts())[:3]
        self.tracked = {"1", "2", "3"}

    def test_a_tracked_unit_still_person_linked_is_re_applied(self):
        gen = self.make_gen(self.assignments, accepted="CoworkerContractIds-list",
                            live_record={"Id": "x", "CoworkerId": 999}, tracked=self.tracked)
        gen._assign_desks(self.coworker_ids, self.desk_ids, self.update_fn, self.get_fn)
        self.assertEqual(len(self.writes), 3)

    def test_a_tracked_unit_already_correct_is_left_alone(self):
        contract_id = f"CONTRACT-{self.assignments[0]['ContractIndex']}"
        gen = self.make_gen(self.assignments[:1], accepted="CoworkerContractIds-list",
                            live_record={"Id": "x", "CoworkerId": None,
                                         "CoworkerContractIds": [contract_id]},
                            tracked=self.tracked)
        gen._assign_desks(self.coworker_ids, self.desk_ids, self.update_fn, self.get_fn)
        self.assertEqual(self.writes, [])

    def test_an_unreadable_unit_is_left_alone_rather_than_rewritten(self):
        gen = self.make_gen(self.assignments, accepted="CoworkerContractIds-list",
                            tracked=self.tracked)

        def _boom(entity, id):
            raise RuntimeError("gone")

        gen._assign_desks(self.coworker_ids, self.desk_ids, self.update_fn, _boom)
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


if __name__ == "__main__":
    unittest.main()
