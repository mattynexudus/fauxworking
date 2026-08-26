"""
Layer 5 — CRM & Proposals

Reads pre-generated definitions from data/crm_opportunities.json + siblings,
then pushes them to the Nexudus API.

Creates:
- CrmOpportunity (30) across the pipeline (Lead/Qualified/Proposal Sent/
  Negotiation/Won/Lost) — §4l. CrmBoardColumnId placed directly on the
  target stage's column (not moved incrementally); WonOn/LostOn set
  explicitly for Won/Lost since we're not driving the move through the UI.
- CrmOpportunityHistory (~87) — a stage-path trail per opportunity so
  conversion reports have transition data to chart.
- Proposal (15) — created at status=1 (Draft), then updated to its target
  status. Accepted ones (5) are tied to a Won opportunity's coworker.
  Per the CoworkerContract entity guide, a Proposal auto-creates a
  ProposalContract at creation time, which is said to "become" a
  CoworkerContract once accepted — this is inferred from the guide text,
  not an explicitly documented trigger, so it's worth a spot-check on
  first live run (see reference/entity-dependencies.md gotchas).
- CoworkerDataFile (10) — placeholder documents linked to proposals via
  ProposalGuid. NewFileDataUrl is a tiny synthetic text placeholder, not
  a real contract — there's no real document content in this pipeline.

Prerequisites: Layer 0 (admin user), Layer 1 (CRM boards/columns,
tariffs, discount codes), Layer 2 (coworkers).
Data files: Run `python prebuild.py` first to generate data/*.json.

Usage:
    python generators/07_crm_proposals.py              # Live mode
    python generators/07_crm_proposals.py --dry-run     # Log only
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.base import BaseGenerator, parse_args
from config import DATA_DIR, TODAY, to_utc_str
from datetime import timedelta

# CrmBoardColumn full_key = "<board short name>/<column name>", matching
# generators/01_structural.py's CRM_BOARD_COLUMNS
STAGE_COLUMN_KEY = {
    "Lead": "New Business/Lead",
    "Qualified": "New Business/Qualified",
    "Proposal Sent": "New Business/Proposal Sent",
    "Negotiation": "Expansion/Negotiation",
    "Won": "New Business/Won",
    "Lost": "New Business/Lost",
}
# eCrmOpportunityStatus: InProgress=1, Won=2, Lost=3
STAGE_STATUS = {"Won": 2, "Lost": 3}

# NewFileDataUrl must be a real, fetchable HTTP(S) URL — confirmed live
# that the API fetches it server-side rather than accepting a data: URI
# (which fails with a generic 500, not a validation error). A standard
# W3C test fixture, not our own content, since this is just a filler
# document for demo purposes.
PLACEHOLDER_FILE_DATA_URL = "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"


class CrmProposalsGenerator(BaseGenerator):
    entity_name = "crm_proposals"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.opportunity_ids = {}   # OpportunityIndex -> Id
        self.proposal_ids = {}      # ProposalIndex -> Id
        self.proposal_guids = {}    # ProposalIndex -> UniqueId

        self.opportunity_defs = self._load_data("crm_opportunities.json")
        self.opportunity_history_defs = self._load_data("crm_opportunity_history.json")
        self.proposal_defs = self._load_data("proposals.json")
        self.data_file_defs = self._load_data("coworker_data_files.json")

        self.set_target("crmopportunities", len(self.opportunity_defs))
        self.set_target("crmopportunityhistories", len(self.opportunity_history_defs))
        self.set_target("proposals", len(self.proposal_defs))
        self.set_target("coworkerdatafiles", len(self.data_file_defs))

    @staticmethod
    def _load_data(filename):
        path = DATA_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"{path} not found. Run 'python prebuild.py' first.")
        return json.loads(path.read_text(encoding="utf-8"))

    def run(self, nexudus_list, nexudus_create, nexudus_update, nexudus_run_command, prev_output):
        biz = prev_output["business_id"]
        admin_id = prev_output["admin_user_id"]
        currency_id = prev_output["currency_id"]
        coworker_ids = prev_output["coworker_ids"]
        crm_board_column_ids = prev_output["crm_board_column_ids"]
        opportunity_type_ids = prev_output["opportunity_type_ids"]
        tariff_ids = prev_output["tariff_ids"]
        discount_code_ids = prev_output["discount_code_ids"]

        self._create_opportunities(biz, admin_id, currency_id, coworker_ids, crm_board_column_ids,
                                    opportunity_type_ids, nexudus_create, nexudus_update)
        self._create_opportunity_history(crm_board_column_ids, nexudus_create)
        self._create_proposals(biz, admin_id, coworker_ids, tariff_ids, discount_code_ids,
                                nexudus_create, nexudus_update, nexudus_run_command)
        self._create_data_files(biz, coworker_ids, nexudus_create)

        self.log.info("Layer 5 CRM/Proposals complete. Opportunities: %d, Proposals: %d",
                      len(self.opportunity_ids), len(self.proposal_ids))

        return {**prev_output, "opportunity_ids": self.opportunity_ids, "proposal_ids": self.proposal_ids}

    # ------------------------------------------------------------------
    # Date helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _at(day_offset):
        d = TODAY + timedelta(days=day_offset)
        return to_utc_str(d)

    # ------------------------------------------------------------------
    # CrmOpportunity
    # ------------------------------------------------------------------
    def _create_opportunities(self, biz, admin_id, currency_id, coworker_ids, crm_board_column_ids,
                               opportunity_type_ids, nexudus_create, nexudus_update):
        self.log.info("--- CRM Opportunities (%d) ---", len(self.opportunity_defs))
        lead_column_id = crm_board_column_ids.get(STAGE_COLUMN_KEY["Lead"])
        opportunity_type_id_list = list(opportunity_type_ids.values())

        for defn in self.opportunity_defs:
            idx = defn["index"]
            track_key = str(idx)
            if self.already_created("OpportunityIndex", track_key, entity="crmopportunities"):
                existing = next(r for r in self.get_tracked_ids()
                                 if r.get("entity") == "crmopportunities" and r.get("OpportunityIndex") == track_key)
                self.opportunity_ids[idx] = existing["Id"]
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping opportunity #%d — coworker #%d was never created (seat limit?)",
                                  idx, defn["CoworkerIndex"],
                                  skip=True, entity="crmopportunities", reason="parent_skipped")
                continue

            stage = defn["Stage"]
            # Every opportunity is created at Lead first, then moved via a
            # follow-up UPDATE for anything not already Lead. An earlier
            # version of this code created directly at the real target
            # column/status in one call — a comment here claimed that was
            # confirmed live to work (a direct admin-UI create on
            # Negotiation, plus a direct test onto Negotiation/Won/Lost),
            # but that testing was against a different, older account.
            # Reproduced live on *this* account: direct creation onto
            # Qualified, Proposal Sent, and even Won all fail with the
            # generic "Ooops!" 500 — every stage except Lead — isolated by
            # varying only the CrmBoardColumnId against an otherwise
            # identical, already-proven-working body (bisected against
            # both the coworker and the column independently to rule out
            # anything else). Lead is genuinely the only column this
            # account accepts a direct create into.
            body = {
                # BusinessId/IssuedById/ResponsibleId/CurrencyId were all
                # missing here originally and are why every opportunity
                # create failed regardless of column — found by capturing
                # the real admin UI's create payload and diffing it
                # against this body (same technique as rule 27). IssuedById
                # is the business id, not a user id (same convention as
                # 03_contracts.py); ResponsibleId is the admin user.
                "BusinessId": biz,
                "IssuedById": biz,
                "ResponsibleId": admin_id,
                "CurrencyId": currency_id,
                "CrmBoardColumnId": lead_column_id,
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                # Optional per the schema but effectively required on this
                # account — create fails with a generic 500 without one.
                "OpportunityTypeId": (opportunity_type_id_list[idx % len(opportunity_type_id_list)]
                                      if opportunity_type_id_list else None),
                "Status": 1,
                "Value": defn["Value"],
                "LeadSource": defn["LeadSource"],
                # Confirmed live: Nexudus auto-normalizes Position to spaced
                # multiples of 100 (1, 200, 300, ...) regardless of what's
                # sent — it's just a display-order hint, not meaningful
                # data. Sending our pre-generated sequential values (9, 10,
                # 11, ...) collides with its internal resequencing and
                # fails with a generic 500; isolated bisection (varying one
                # field at a time against otherwise-identical bodies) showed
                # Position was the actual cause, not rate limiting or a
                # coworker/type issue as first suspected. Always sending 1
                # avoids it entirely and was confirmed live to succeed
                # repeatedly with no failures.
                "Position": 1,
                "DueDate": self._at(defn["DueDayOffset"]),
            }

            if self.dry_run:
                self.log_would_create("crmopportunities", body)
                if stage != "Lead":
                    self.log.info("WOULD MOVE opportunity #%d to [%s]", idx, stage)
                self.opportunity_ids[idx] = f"DRY-OPP-{idx}"
                continue

            try:
                result = nexudus_create("crmopportunities", body)
            except Exception as e:  # noqa: BLE001
                # Nexudus's generic 500 here has turned out to be bursty
                # transient flakiness as often as a real rejection (see
                # CLAUDE.md rule 37 — nexudus_client.py already retries it
                # several times before this is ever reached).
                # classify_failure additionally tells a one-off from the
                # same error repeating (systemic — stop rather than take
                # down opportunity history and proposals after it by
                # hammering through every remaining opportunity the same
                # way).
                verdict = self.classify_failure("crmopportunities", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping opportunity creation — this error has repeated "
                        "several times in a row, likely an account-wide condition: %s", e,
                        skip=True, entity="crmopportunities", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping opportunity #%d — create failed: %s", idx, e,
                                  skip=True, entity="crmopportunities", reason="unknown_error")
                continue

            self.opportunity_ids[idx] = result["Id"]
            actual_stage = "Lead"

            if stage != "Lead":
                move_body = {
                    "CrmBoardColumnId": crm_board_column_ids.get(STAGE_COLUMN_KEY[stage], lead_column_id),
                    "Status": STAGE_STATUS.get(stage, 1),
                }
                if stage == "Won":
                    move_body["WonOn"] = self._at(defn["DueDayOffset"])
                elif stage == "Lost":
                    move_body["LostOn"] = self._at(defn["DueDayOffset"])
                try:
                    result = nexudus_update("crmopportunities", result["Id"], move_body)
                    actual_stage = stage
                except Exception as e:  # noqa: BLE001
                    # The opportunity itself was created successfully — a
                    # failed move leaves it sitting at Lead rather than
                    # losing it entirely, so this is a distinct, separately
                    # classified failure from the create above, and not
                    # counted against crmopportunities' own created/failed
                    # tally (the record does exist). Downstream logic that
                    # reads Stage/Status (opportunity history, proposals
                    # tied to a Won opportunity) will see it as Lead, same
                    # as if it had been generated that way from the start.
                    verdict = self.classify_failure("crmopportunities:move", e)
                    if verdict == "systemic":
                        self.log.warning(
                            "Opportunity stage moves look blocked for the rest of this "
                            "run — this error has repeated several times in a row: %s", e)
                    else:
                        self.log.warning("Opportunity #%d created but couldn't be moved to "
                                          "[%s] — left at Lead: %s", idx, stage, e)

            self.track_id({
                "entity": "crmopportunities", **result,
                "OpportunityIndex": track_key, "Stage": actual_stage,
            })
            self.log.info("Created opportunity #%d [%s] (id=%s)", idx, actual_stage, result["Id"])

    # ------------------------------------------------------------------
    # CrmOpportunityHistory
    # ------------------------------------------------------------------
    def _create_opportunity_history(self, crm_board_column_ids, nexudus_create):
        self.log.info("--- CRM Opportunity History (%d) ---", len(self.opportunity_history_defs))

        for defn in self.opportunity_history_defs:
            track_key = str(defn["index"])
            if self.already_created("HistoryIndex", track_key, entity="crmopportunityhistories"):
                continue

            opp_id = self.opportunity_ids.get(defn["OpportunityIndex"])
            if opp_id is None:
                self.log.warning("Skipping history #%d — opportunity #%d was never created",
                                  defn["index"], defn["OpportunityIndex"],
                                  skip=True, entity="crmopportunityhistories", reason="parent_skipped")
                continue

            body = {
                "CrmOpportunityId": opp_id,
                "NewCrmBoardColumnId": crm_board_column_ids.get(STAGE_COLUMN_KEY[defn["NewStage"]]),
                "ToTime": self._at(defn["DayOffset"]),
            }
            if defn["OldStage"]:
                body["OldCrmBoardColumnId"] = crm_board_column_ids.get(STAGE_COLUMN_KEY[defn["OldStage"]])

            if self.dry_run:
                self.log_would_create("crmopportunityhistories", body)
                continue

            try:
                result = nexudus_create("crmopportunityhistories", body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("crmopportunityhistories", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping opportunity history creation — this error has "
                        "repeated several times in a row, likely an account-wide "
                        "condition: %s", e,
                        skip=True, entity="crmopportunityhistories", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping history #%d — create failed: %s", defn["index"], e,
                                  skip=True, entity="crmopportunityhistories", reason="unknown_error")
                continue

            self.track_id({
                "entity": "crmopportunityhistories", **result, "HistoryIndex": track_key,
            })
            self.log.info("Created history #%d (%s -> %s) on opportunity #%d (id=%s)",
                          defn["index"], defn["OldStage"], defn["NewStage"],
                          defn["OpportunityIndex"], result["Id"])

    # ------------------------------------------------------------------
    # Proposal — create at Draft, then update to target status
    # ------------------------------------------------------------------
    def _create_proposals(self, biz, admin_id, coworker_ids, tariff_ids, discount_code_ids,
                           nexudus_create, nexudus_update, nexudus_run_command):
        self.log.info("--- Proposals (%d) ---", len(self.proposal_defs))

        for defn in self.proposal_defs:
            idx = defn["index"]
            track_key = str(idx)
            if self.already_created("ProposalIndex", track_key, entity="proposals"):
                existing = next(r for r in self.get_tracked_ids()
                                 if r.get("entity") == "proposals" and r.get("ProposalIndex") == track_key)
                self.proposal_ids[idx] = existing["Id"]
                self.proposal_guids[idx] = existing.get("UniqueId")
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping proposal #%d — coworker #%d was never created (seat limit?)",
                                  idx, defn["CoworkerIndex"],
                                  skip=True, entity="proposals", reason="parent_skipped")
                continue

            body = {
                # IssuedById is the Business ID, not a User ID — see the same
                # fix/comment in 03_contracts.py._create_contracts.
                "IssuedById": biz,
                "ResponsibleId": admin_id,
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "Reference": defn["Reference"],
                "ProposalStatus": 1,  # always create as Draft; accept via update (rule 13)
                "TariffId": tariff_ids[defn["TariffName"]],
                "Price": defn["Price"],
                "StartDate": self._at(defn["StartDayOffset"]),
                "BillingDay": defn["BillingDay"],
                "Quantity": defn["Quantity"],
            }
            if defn["UseDiscountCode"]:
                code_id = discount_code_ids.get("WELCOME20")
                if code_id is not None:
                    body["DiscountCodeId"] = code_id

            if self.dry_run:
                self.log_would_create("proposals", body)
                self.proposal_ids[idx] = f"DRY-PROP-{idx}"
                self.proposal_guids[idx] = f"DRY-PROP-GUID-{idx}"
                if defn["ProposalStatus"] != 1:
                    self.log.info("WOULD UPDATE proposals DRY: ProposalStatus=%d", defn["ProposalStatus"])
                continue

            try:
                result = nexudus_create("proposals", body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("proposals", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping proposal creation — this error has repeated several "
                        "times in a row, likely an account-wide condition: %s", e,
                        skip=True, entity="proposals", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping proposal #%d — create failed: %s", idx, e,
                                  skip=True, entity="proposals", reason="unknown_error")
                continue

            self.proposal_ids[idx] = result["Id"]
            self.proposal_guids[idx] = result.get("UniqueId")
            self.track_id({
                "entity": "proposals", **result, "ProposalIndex": track_key,
                "TargetStatus": defn["ProposalStatus"], "UniqueId": result.get("UniqueId"),
            })
            self.log.info("Created proposal #%d '%s' (id=%s)", idx, defn["Reference"], result["Id"])

            # Draft -> Sent -> Accepted must go through PROPOSAL_SEND /
            # PROPOSAL_ACCEPT commands, not a direct ProposalStatus
            # update. A direct update to Accepted always fails
            # ("Accepted proposals cannot be changed", even on a brand
            # new Draft) — confirmed live by capturing the real admin
            # UI's network request, which hits .../proposals/commands,
            # not a plain field update. nexudus_list_commands claiming
            # "proposals does not support commands" was simply wrong.
            # PROPOSAL_SEND also properly populates the readonly SentOn
            # field, which a direct status update never did either.
            #
            # Not skip=True/entity= on failure here — the proposal itself
            # was already created and counted above; a failed status
            # transition leaves it correctly existing at Draft rather than
            # its intended status, not "missing." classify_failure is
            # still used for systemic detection (stop the loop rather than
            # hammer through every remaining proposal's transition the
            # same way), with dedicated signature keys per transition type
            # so send/accept/reject failures aren't conflated.
            try:
                if defn["ProposalStatus"] == 2:
                    nexudus_run_command("proposals", "PROPOSAL_SEND", [result["Id"]])
                    self.log.info("Sent proposal #%d", idx)
                elif defn["ProposalStatus"] == 3:
                    nexudus_run_command("proposals", "PROPOSAL_SEND", [result["Id"]])
                    nexudus_run_command("proposals", "PROPOSAL_ACCEPT", [result["Id"]])
                    self.log.info("Accepted proposal #%d", idx)
                elif defn["ProposalStatus"] == 4:
                    nexudus_update("proposals", result["Id"], {"ProposalStatus": defn["ProposalStatus"]})
                    self.log.info("Updated proposal #%d to status=%d", idx, defn["ProposalStatus"])
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure(f"proposals:status_{defn['ProposalStatus']}", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Proposal status transitions to status=%d look blocked — this "
                        "error has repeated several times in a row, likely an "
                        "account-wide condition: %s", defn["ProposalStatus"], e)
                else:
                    self.log.warning("Created proposal #%d but failed to reach status=%d: %s",
                                      idx, defn["ProposalStatus"], e)

    # ------------------------------------------------------------------
    # CoworkerDataFile
    # ------------------------------------------------------------------
    def _create_data_files(self, biz, coworker_ids, nexudus_create):
        self.log.info("--- Coworker Data Files (%d) ---", len(self.data_file_defs))

        for defn in self.data_file_defs:
            track_key = str(defn["index"])
            if self.already_created("DataFileIndex", track_key, entity="coworkerdatafiles"):
                continue

            if defn["CoworkerIndex"] not in coworker_ids:
                self.log.warning("Skipping data file #%d — coworker #%d was never created (seat limit?)",
                                  defn["index"], defn["CoworkerIndex"],
                                  skip=True, entity="coworkerdatafiles", reason="parent_skipped")
                continue

            proposal_guid = self.proposal_guids.get(defn["ProposalIndex"])
            body = {
                "BusinessId": biz,
                "CoworkerId": coworker_ids[defn["CoworkerIndex"]],
                "Name": defn["Name"],
                "NewFileDataUrl": PLACEHOLDER_FILE_DATA_URL,
                # Confirmed live: signature requests require the coworker to
                # have portal access, and seeded coworkers never do (no
                # linked User account — same constraint as rule 23's
                # CommunityThread.CoworkerId gap). The file itself is still
                # valid without one, so just don't request a signature
                # rather than skip the record.
                "RequestDigitalSignature": False,
            }
            if proposal_guid:
                body["ProposalGuid"] = proposal_guid

            if self.dry_run:
                self.log_would_create("coworkerdatafiles", body)
                continue

            try:
                result = nexudus_create("coworkerdatafiles", body)
            except Exception as e:  # noqa: BLE001
                verdict = self.classify_failure("coworkerdatafiles", e)
                if verdict == "systemic":
                    self.log.warning(
                        "Stopping data file creation — this error has repeated several "
                        "times in a row, likely an account-wide condition: %s", e,
                        skip=True, entity="coworkerdatafiles", reason="systemic_rate_limit")
                    break
                self.log.warning("Skipping data file #%d — create failed: %s", defn["index"], e,
                                  skip=True, entity="coworkerdatafiles", reason="unknown_error")
                continue

            self.track_id({
                "entity": "coworkerdatafiles", **result, "DataFileIndex": track_key,
            })
            self.log.info("Created data file #%d (id=%s)", defn["index"], result["Id"])


if __name__ == "__main__":
    args = parse_args()
    gen = CrmProposalsGenerator(dry_run=args.dry_run)

    if gen.dry_run:
        import importlib
        struct = importlib.import_module("generators.01_structural")

        mock_coworker_defs = json.loads((DATA_DIR / "coworkers.json").read_text(encoding="utf-8"))
        mock_coworker_ids = {c["index"]: f"DRY-CW-{c['index']}" for c in mock_coworker_defs}
        mock_tariff_ids = {t["Name"]: f"DRY-TARIFF-{t['Name']}" for t in struct.TARIFFS}
        mock_discount_code_ids = {d["Code"]: f"DRY-DISC-{d['Code']}" for d in struct.DISCOUNT_CODES}

        mock_crm_board_column_ids = {}
        for board_short_name, columns in struct.CRM_BOARD_COLUMNS.items():
            for col in columns:
                mock_crm_board_column_ids[f"{board_short_name}/{col['Name']}"] = \
                    f"DRY-COL-{board_short_name}-{col['Name']}"

        mock_opportunity_type_ids = {t["Name"]: f"DRY-OPPTYPE-{t['Name']}" for t in struct.OPPORTUNITY_TYPES}

        mock_prev = {
            "business_id": "DRY-BIZ-1",
            "admin_user_id": "DRY-ADMIN-1",
            "currency_id": "DRY-CUR-1",
            "coworker_ids": mock_coworker_ids,
            "crm_board_column_ids": mock_crm_board_column_ids,
            "opportunity_type_ids": mock_opportunity_type_ids,
            "tariff_ids": mock_tariff_ids,
            "discount_code_ids": mock_discount_code_ids,
        }

        _counter = {"n": 0}

        def _mock_create(entity, body):
            _counter["n"] += 1
            return {"Id": f"DRY-{entity}-{_counter['n']}", "UniqueId": f"DRY-GUID-{_counter['n']}"}

        gen.run(
            nexudus_list=lambda entity, filters: [],
            nexudus_create=_mock_create,
            nexudus_update=lambda entity, id, body: {"Id": id},
            nexudus_run_command=lambda entity, key, ids, parameters=None: None,
            prev_output=mock_prev,
        )
    else:
        import pipeline
        pipeline.run_up_to(7, business_id=args.business_id)
