"""Fail-closed adversarial contracts for the pure report acceptance kernel.

These tests intentionally expose core gaps; they do not normalize inputs or
mark current failures as expected. No files, providers or devices are used.
"""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from anr_evidence.anr_strategy import ANR_TYPE_STRATEGIES
from anr_evidence.report_validation import (
    CONSERVATIVE_FLAGS,
    DELIVERABLE_STATUSES,
    strict_json_loads,
    validate_manager_report,
    validate_workspace_content,
)
from tests.acceptance_helpers import final_analysis, report_payload


INPUT_TYPES = {"input_dispatching_timeout", "no_focus_window"}
REF_FIELDS = ("evidenceRefs", "supportingEventIds", "evidence", "supportingEvidenceRefs")


def _reconstruction(group_id="anr-test", anr_type="no_focus_window", event_id="evt-one"):
    return {
        "groupId": group_id, "anrType": anr_type,
        "events": [{
            "eventId": event_id, "rawLine": "ANR notification for " + group_id,
            "groupId": group_id, "clockDomain": "wall:local",
            "timestamp": "2026-11-04T10:00:00.000", "deltaTSeconds": 0.0,
            "certainty": "observed",
        }],
        "evidenceCompleteness": {"complete": False, "missingSources": ["trace"]},
        "rootCausePatternHints": [],
    }


def _workspace_payload(group_id="anr-test", anr_type="no_focus_window", reference=None):
    payload = deepcopy(report_payload(group_id, reference))
    payload["anrType"] = anr_type
    if anr_type not in INPUT_TYPES:
        payload["inputEventAssessments"] = []
    return payload


def _manager_payload(group_id="anr-test", anr_type="no_focus_window", reference=None):
    workspace = _workspace_payload(group_id, anr_type, reference)
    fields = ("groupId", "anrType", "candidateChains", "inputEventAssessments", "anrModel", "simulationSteps")
    payload = {key: deepcopy(workspace[key]) for key in fields}
    payload.update({
        **CONSERVATIVE_FLAGS,
        "candidateConclusions": [], "remediationDrafts": [],
        "globalLimitations": ["Stack and continuous timing evidence are unavailable; collect them before attributing a cause."],
        "reProbeRequest": None,
    })
    return payload


def _workspace_text(payload):
    parts = [
        "<!-- AI_ANALYSIS_SLOT:" + slot + " -->\n"
        "Evidence is unavailable; collect the matching source before inferring a blocking mechanism.\n"
        for slot in ("trace", "eventlog", "logcat-anrmanager")
    ]
    return "\n".join(parts) + "\n<!-- AI_ANALYSIS_SLOT:final-anr -->\n" + final_analysis(payload)


def _group(reconstruction=None):
    reconstruction = _reconstruction() if reconstruction is None else reconstruction
    return {"id": reconstruction["groupId"], "reconstruction": reconstruction}


def _multi_groups():
    return [_group(), _group(_reconstruction("anr-two", event_id="evt-two"))]


def _multi_report():
    return {
        **CONSERVATIVE_FLAGS,
        "candidateChains": [], "candidateConclusions": [], "remediationDrafts": [],
        "globalLimitations": ["Each incident has independently disclosed missing evidence."],
        "inputEventAssessments": [], "anrModel": [], "simulationSteps": [],
        "groups": [_manager_payload(), _manager_payload("anr-two")],
    }


class AcceptanceAssertions(unittest.TestCase):
    def readiness(self, validate, *args, **kwargs):
        try:
            result = validate(*args, **kwargs)
        except Exception as exc:
            self.fail(f"{validate.__name__} raised {type(exc).__name__}: {exc}; expected a blocked readiness result")
        self.assertIsInstance(result, dict)
        self.assertIsInstance(result.get("policyVersion"), str)
        for key in ("blockers", "reviewItems", "warnings"):
            self.assertIsInstance(result.get(key), list, key)
        self.assertIsInstance(result.get("checks"), dict)
        self.assertIs(result.get("rootCauseConfirmed"), False)
        self.assertIs(result.get("repairVerified"), False)
        return result

    def blocked(self, validate, *args, **kwargs):
        result = self.readiness(validate, *args, **kwargs)
        self.assertEqual(result["status"], "blocked", "Malformed or mis-scoped evidence was accepted")
        self.assertTrue(result["blockers"], "A blocked result must explain the hard failure")
        return result

    def held(self, validate, *args, **kwargs):
        result = self.readiness(validate, *args, **kwargs)
        self.assertNotIn(result["status"], DELIVERABLE_STATUSES, "Unsupported measured timing was accepted")
        self.assertTrue(result["blockers"] or result["reviewItems"])
        return result

    def workspace(self, payload, reconstruction=None):
        reconstruction = _reconstruction() if reconstruction is None else reconstruction
        return self.readiness(
            validate_workspace_content, _workspace_text(payload), reconstruction,
            expected_group_id="anr-test",
        )


class StrictJsonAdversarialTests(unittest.TestCase):
    def test_legal_scalar_json_is_parsed_without_conferring_report_validity(self):
        for text, expected in (("null", None), ("true", True), ("7", 7), ('"report"', "report"), ("[]", [])):
            with self.subTest(text=text):
                self.assertEqual(strict_json_loads(text), expected)

    def test_non_text_inputs_raise_value_error(self):
        for value in (None, False, 7, {}, [], b"{}"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                strict_json_loads(value)

    def test_nested_and_escaped_duplicate_keys_are_rejected(self):
        for text in (r'{"a":1,"\u0061":2}', '{"candidate":{"executed":false,"executed":true}}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                strict_json_loads(text)

    def test_non_finite_comments_multiple_documents_and_trailing_data_are_rejected(self):
        for text in ('{"n":NaN}', '{"n":Infinity}', '{"n":-Infinity}', '{"n":1e9999}',
                     '{"n":-1e9999}', '{} {}', '{} trailing', '// comment\n{}', '{"a":1,}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                strict_json_loads(text)

    def test_oversized_utf8_is_a_value_error(self):
        text = json.dumps("\u00e9" * (2 * 1024 * 1024), ensure_ascii=False)
        with self.assertRaises(ValueError):
            strict_json_loads(text)

    def test_parser_depth_and_overflow_errors_become_value_errors(self):
        for error in (RecursionError("parser depth exhausted"), OverflowError("parser limit exhausted")):
            with self.subTest(error=type(error).__name__), \
                 patch("anr_evidence.report_validation.json.loads", side_effect=error), \
                 self.assertRaises(ValueError):
                strict_json_loads("{}")


class ManagerContainerAdversarialTests(AcceptanceAssertions):
    def test_scalar_reports_and_nonfinite_objects_are_blocked_without_crashing(self):
        for report in (None, False, 7, "report", [], {}, {"score": float("nan")}, {"score": float("inf")}):
            with self.subTest(report=report):
                self.blocked(validate_manager_report, report, groups=[_group()])

    def test_malformed_candidate_and_limitation_containers_are_blocked(self):
        fields = ("candidateChains", "candidateConclusions", "remediationDrafts", "globalLimitations",
                  "inputEventAssessments", "anrModel", "simulationSteps")
        for field in fields:
            for value in (None, {}, "not-an-array", 7):
                payload = _manager_payload()
                payload[field] = value
                with self.subTest(field=field, value=value):
                    self.blocked(validate_manager_report, payload, groups=[_group()])

    def test_malformed_group_inventory_is_blocked_not_a_python_exception(self):
        for groups in (None, {}, "groups", [], [None], ["anr-test"], [{"id": "anr-test", "reconstruction": None}]):
            with self.subTest(groups=groups):
                self.blocked(validate_manager_report, _manager_payload(), groups=groups)

    def test_malformed_reconstruction_events_and_record_refs_are_blocked(self):
        for field, value in (("events", None), ("events", {}), ("events", "evt-one"), ("events", [None]),
                             ("recordRefs", None), ("recordRefs", "forged"),
                             ("recordRefs", {"forged": True}), ("recordRefs", [{"eventId": "forged"}])):
            reconstruction = _reconstruction()
            if field == "events":
                reconstruction[field] = value
            else:
                reconstruction["events"][0][field] = value
            with self.subTest(field=field, value=value):
                self.blocked(validate_manager_report, _manager_payload(), groups=[_group(reconstruction)])

    def test_wrong_typed_or_unknown_conservative_flag_values_are_blocked(self):
        for field in CONSERVATIVE_FLAGS:
            for value in (None, "unknown", 0, 1, {}, []):
                payload = _manager_payload()
                payload[field] = value
                with self.subTest(field=field, value=value):
                    self.blocked(validate_manager_report, payload, groups=[_group()])

    def test_dict_string_and_nonstring_references_are_not_coerced_into_ids(self):
        for field in REF_FIELDS:
            for value in ("evt-one", {"evt-one": True}, [{"eventId": "evt-one"}], [7], [None], [""]):
                payload = _manager_payload()
                payload["anrModel"][0][field] = value
                with self.subTest(field=field, value=value):
                    self.blocked(validate_manager_report, payload, groups=[_group()])

    def test_string_or_dict_record_refs_cannot_mint_evidence_ids(self):
        for value, forged_ref in (("forged", "f"), ({"forged": False}, "forged")):
            reconstruction = _reconstruction()
            reconstruction["events"][0]["recordRefs"] = value
            with self.subTest(value=value):
                self.blocked(validate_manager_report, _manager_payload(reference=forged_ref), groups=[_group(reconstruction)])

    def test_real_ids_unique_raw_lines_and_wellformed_record_aliases_still_resolve(self):
        reconstruction = _reconstruction()
        reconstruction["events"][0]["recordRefs"] = ["record-alias"]
        for reference, field in (("evt-one", "supportingEventIds"),
                                 (reconstruction["events"][0]["rawLine"], "evidenceRefs"),
                                 ("record-alias", "evidenceRefs")):
            payload = _manager_payload()
            payload["anrModel"][0][field] = [reference]
            before = deepcopy((payload, reconstruction))
            with self.subTest(reference=reference):
                result = self.readiness(validate_manager_report, payload, groups=[_group(reconstruction)])
                self.assertIn(result["status"], DELIVERABLE_STATUSES)
                self.assertEqual((payload, reconstruction), before)

    def test_duplicate_evidence_ids_and_duplicate_raw_lines_are_ambiguous(self):
        for duplicate_id in (True, False):
            reconstruction = _reconstruction()
            event = deepcopy(reconstruction["events"][0])
            if duplicate_id:
                event["rawLine"] = "A different observation carrying a duplicate identity"
            else:
                event["eventId"] = "evt-other"
            reconstruction["events"].append(event)
            reference = "evt-one" if duplicate_id else event["rawLine"]
            with self.subTest(duplicate_id=duplicate_id):
                self.blocked(validate_manager_report, _manager_payload(reference=reference), groups=[_group(reconstruction)])

    def test_reconstruction_identity_and_explicit_record_scope_must_match_the_group(self):
        for field in ("reconstruction", "event"):
            group = _group()
            target = group["reconstruction"] if field == "reconstruction" else group["reconstruction"]["events"][0]
            target["groupId"] = "other-anr"
            with self.subTest(field=field):
                self.blocked(validate_manager_report, _manager_payload(reference="evt-one"), groups=[group])

    def test_unknown_metadata_cannot_hide_nonconservative_model_or_action_flags(self):
        for field, value in (("executed", True), ("validation", "verified"),
                             ("finalJudgment", True), ("requiresHumanConfirmation", False)):
            for array in ("anrModel", "simulationSteps"):
                payload = _manager_payload()
                nested = {
                    "summary": "Candidate collection action or mechanism node.",
                    "supportingEventIds": [], "metadata": {field: value},
                }
                if array == "anrModel":
                    nested.update(phase="blocking", certainty="unknown")
                else:
                    nested.update(step=1, action="Collect matching lifecycle logs.")
                payload[array][0]["phases" if array == "anrModel" else "actions"] = [nested]
                with self.subTest(array=array, flag=field):
                    self.blocked(validate_manager_report, payload, groups=[_group()])

    def test_nested_model_and_plan_references_are_validated(self):
        for array, nested_field in (("anrModel", "phases"), ("simulationSteps", "actions")):
            payload = _manager_payload()
            nested = {"summary": "Unverified candidate node.", "supportingEventIds": ["foreign-event"]}
            if array == "anrModel":
                nested.update(phase="blocking", certainty="unknown")
            else:
                nested.update(step=1, action="Collect matching lifecycle logs.")
            payload[array][0][nested_field] = [nested]
            with self.subTest(array=array):
                self.blocked(validate_manager_report, payload, groups=[_group()])


class WorkspaceContainerAdversarialTests(AcceptanceAssertions):
    def test_scalar_final_json_is_blocked_without_crashing(self):
        for payload in (None, False, 7, "report", [], {}):
            with self.subTest(payload=payload):
                result = self.workspace(payload)
                self.assertEqual(result["status"], "blocked")
                self.assertTrue(result["blockers"])

    def test_nontext_analysis_and_scalar_reconstruction_fail_closed(self):
        for text in (None, {}, [], 7):
            with self.subTest(text=text):
                self.blocked(validate_workspace_content, text, _reconstruction(), expected_group_id="anr-test")
        for reconstruction in (None, {}, [], "report", 7):
            with self.subTest(reconstruction=reconstruction):
                self.blocked(validate_workspace_content, _workspace_text(_workspace_payload()), reconstruction,
                             expected_group_id="anr-test")

    def test_unhashable_types_and_hint_ids_are_reported_as_blockers(self):
        for field, value in (("anrType", {}), ("anrType", []), ("primaryRootCauseHintId", {}),
                             ("primaryRootCauseHintId", []), ("supportingHintIds", [{}])):
            payload = _workspace_payload()
            payload[field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(self.workspace(payload)["status"], "blocked")

    def test_malformed_source_thread_and_gap_containers_are_blocked(self):
        for field, value in (("sourceAnalyses", None), ("sourceAnalyses", []), ("sourceAnalyses", {"trace": "summary"}),
                             ("blockingThread", "main"), ("ownerThread", []),
                             ("evidenceGaps", "missing trace"), ("remediationSuggestions", {})):
            payload = _workspace_payload()
            payload[field] = value
            with self.subTest(field=field):
                self.assertEqual(self.workspace(payload)["status"], "blocked")

    def test_malformed_evidence_completeness_and_source_hints_fail_closed(self):
        for field, value in (("evidenceCompleteness", None), ("evidenceCompleteness", []),
                             ("evidenceCompleteness", {"complete": "false"}),
                             ("rootCausePatternHints", None), ("rootCausePatternHints", [{}])):
            reconstruction = _reconstruction()
            reconstruction[field] = value
            with self.subTest(field=field, value=value):
                self.assertEqual(self.workspace(_workspace_payload(), reconstruction)["status"], "blocked")

    def test_dict_and_string_source_references_are_not_valid_evidence_arrays(self):
        for value in ("evt-one", {"evt-one": True}, [{"eventId": "evt-one"}]):
            payload = _workspace_payload()
            payload["sourceAnalyses"]["trace"]["keyEvidence"] = value
            with self.subTest(value=value):
                self.assertEqual(self.workspace(payload)["status"], "blocked")

    def test_model_supplied_ready_or_verified_fields_do_not_override_invalid_refs(self):
        payload = _workspace_payload(reference="missing-event")
        payload.update({"status": "ready", "deliveryReadiness": {"status": "ready", "blockers": []},
                        "rootCauseConfirmed": True, "repairVerified": True})
        result = self.workspace(payload)
        self.assertEqual(result["status"], "blocked")
        self.assertIs(result["rootCauseConfirmed"], False)
        self.assertIs(result["repairVerified"], False)

    def test_workspace_nested_executed_plan_and_foreign_scope_cannot_pass(self):
        for field, value in (("executed", True), ("validation", "verified"), ("groupId", "other-anr")):
            payload = _workspace_payload()
            payload["simulationSteps"][0]["actions"] = [{"step": 1, "action": "Collect matching lifecycle logs.", field: value}]
            with self.subTest(field=field):
                self.assertEqual(self.workspace(payload)["status"], "blocked")


class MultiGroupScopeAdversarialTests(AcceptanceAssertions):
    def test_complete_independently_scoped_reports_remain_deliverable_and_unmutated(self):
        report, groups = _multi_report(), _multi_groups()
        before = deepcopy((report, groups))
        result = self.readiness(validate_manager_report, report, groups=groups)
        self.assertIn(result["status"], DELIVERABLE_STATUSES)
        self.assertEqual((report, groups), before)

    def test_same_raw_line_in_different_groups_is_not_locally_ambiguous(self):
        report, groups = _multi_report(), _multi_groups()
        raw = "The same diagnostic text independently retained in two incidents"
        for group, child in zip(groups, report["groups"]):
            group["reconstruction"]["events"][0]["rawLine"] = raw
            child["anrModel"][0]["evidenceRefs"] = [raw]
        result = self.readiness(validate_manager_report, report, groups=groups)
        self.assertIn(result["status"], DELIVERABLE_STATUSES)

    def test_missing_duplicate_or_unknown_child_scope_is_blocked(self):
        for case in ("missing", "duplicate", "unknown", "absent-id", "dict-id", "list-id", "scalar-child"):
            report = _multi_report()
            if case == "missing":
                report["groups"].pop()
            elif case == "duplicate":
                report["groups"][1] = deepcopy(report["groups"][0])
            elif case == "absent-id":
                del report["groups"][1]["groupId"]
            elif case == "scalar-child":
                report["groups"][1] = "anr-two"
            else:
                report["groups"][1]["groupId"] = {"unknown": "foreign-anr", "dict-id": {}, "list-id": []}[case]
            with self.subTest(case=case):
                self.blocked(validate_manager_report, report, groups=_multi_groups())

    def test_scalar_dict_or_absent_multi_group_envelope_is_blocked(self):
        for value in (None, {}, "anr-test", []):
            report = _multi_report()
            report["groups"] = value
            with self.subTest(value=value):
                self.blocked(validate_manager_report, report, groups=_multi_groups())

    def test_duplicate_incident_inventory_does_not_authorize_duplicate_reports(self):
        groups = [_group(), _group()]
        report = _multi_report()
        report["groups"] = [_manager_payload(), _manager_payload()]
        self.blocked(validate_manager_report, report, groups=groups)

    def test_nonempty_root_candidates_cannot_bypass_multi_group_scoping(self):
        for field in ("candidateChains", "anrModel", "remediationDrafts"):
            report = _multi_report()
            candidate = deepcopy(report["groups"][0]["anrModel"][0])
            candidate["summary"] = "This aggregate claim lacks an incident scope."
            candidate["supportingEventIds"] = ["evt-one"]
            if field == "candidateChains":
                candidate.update(title="Unscoped aggregate chain", rationale="Mechanism remains unverified.")
            elif field == "remediationDrafts":
                candidate.update(title="Collect incident-specific evidence", actionDraft="Capture the matching process stack.")
            report[field] = [candidate]
            with self.subTest(field=field):
                self.blocked(validate_manager_report, report, groups=_multi_groups())

    def test_child_refs_cannot_borrow_a_foreign_group_event_or_shared_raw_line(self):
        for reference in ("evt-two", "same notification text"):
            groups = _multi_groups()
            if reference == "same notification text":
                for group in groups:
                    group["reconstruction"]["events"].append({"eventId": group["id"] + "-duplicate", "rawLine": reference})
                groups[0]["reconstruction"]["events"].append({"eventId": "another-local-copy", "rawLine": reference})
            report = _multi_report()
            report["groups"][0]["anrModel"][0]["supportingEventIds"] = [reference]
            with self.subTest(reference=reference):
                self.blocked(validate_manager_report, report, groups=groups)

    def test_explicit_candidate_group_must_match_its_parent_for_every_candidate_array(self):
        for field in ("candidateChains", "candidateConclusions", "remediationDrafts", "inputEventAssessments", "anrModel", "simulationSteps"):
            report = _multi_report()
            candidate = deepcopy(report["groups"][0]["anrModel"][0])
            candidate["groupId"] = "anr-two"
            if field == "candidateChains":
                candidate.update(title="Candidate mechanism", rationale="Mechanism remains unverified.")
            elif field == "candidateConclusions":
                candidate.update(statement="The mechanism remains unknown.", tentative=True)
            elif field == "remediationDrafts":
                candidate.update(title="Collect incident-specific evidence", actionDraft="Capture the matching process stack.")
            report["groups"][0][field] = [candidate]
            with self.subTest(field=field):
                self.blocked(validate_manager_report, report, groups=_multi_groups())


class AllTypeIncidentContractTests(AcceptanceAssertions):
    def test_missing_evidence_with_explicit_model_and_plan_gaps_is_deliverable_for_all_types(self):
        for anr_type in ANR_TYPE_STRATEGIES:
            reconstruction = _reconstruction(anr_type=anr_type)
            with self.subTest(anr_type=anr_type, profile="manager"):
                result = self.readiness(validate_manager_report, _manager_payload(anr_type=anr_type), groups=[_group(reconstruction)])
                self.assertIn(result["status"], DELIVERABLE_STATUSES)
            with self.subTest(anr_type=anr_type, profile="workspace"):
                self.assertIn(self.workspace(_workspace_payload(anr_type=anr_type), reconstruction)["status"], DELIVERABLE_STATUSES)

    def test_all_types_require_nonempty_model_and_plan_and_typed_input_field(self):
        for anr_type in ANR_TYPE_STRATEGIES:
            for field in ("anrModel", "simulationSteps", "inputEventAssessments"):
                for missing in (True, False):
                    reconstruction = _reconstruction(anr_type=anr_type)
                    manager = _manager_payload(anr_type=anr_type)
                    workspace = _workspace_payload(anr_type=anr_type)
                    for payload in (manager, workspace):
                        if missing:
                            del payload[field]
                        else:
                            payload[field] = None if field == "inputEventAssessments" else []
                    with self.subTest(anr_type=anr_type, field=field, missing=missing, profile="manager"):
                        self.blocked(validate_manager_report, manager, groups=[_group(reconstruction)])
                    with self.subTest(anr_type=anr_type, field=field, missing=missing, profile="workspace"):
                        self.assertEqual(self.workspace(workspace, reconstruction)["status"], "blocked")

    def test_noninput_and_unknown_types_cannot_invent_input_event_assessments(self):
        for anr_type in sorted(ANR_TYPE_STRATEGIES.keys() - INPUT_TYPES):
            reconstruction = _reconstruction(anr_type=anr_type)
            for profile in ("manager", "workspace"):
                payload = _manager_payload(anr_type=anr_type) if profile == "manager" else _workspace_payload(anr_type=anr_type)
                payload["inputEventAssessments"] = [deepcopy(payload["anrModel"][0])]
                with self.subTest(anr_type=anr_type, profile=profile):
                    if profile == "manager":
                        self.blocked(validate_manager_report, payload, groups=[_group(reconstruction)])
                    else:
                        self.assertEqual(self.workspace(payload, reconstruction)["status"], "blocked")

    def test_explicit_manager_type_cannot_disagree_with_the_current_group(self):
        for anr_type in ANR_TYPE_STRATEGIES:
            reconstruction = _reconstruction(anr_type=anr_type)
            payload = _manager_payload(anr_type=anr_type)
            payload["anrType"] = "broadcast_timeout" if anr_type != "broadcast_timeout" else "no_focus_window"
            with self.subTest(anr_type=anr_type):
                self.blocked(validate_manager_report, payload, groups=[_group(reconstruction)])

    def test_optional_model_and_plan_containers_are_typed_when_encoded(self):
        for array, field, value in (("anrModel", "phases", "blocking"),
                                    ("simulationSteps", "actions", {"step": 1}),
                                    ("simulationSteps", "preconditions", "app running"),
                                    ("simulationSteps", "captureChecks", 7),
                                    ("simulationSteps", "missingParameters", {"entry": "unknown"})):
            manager = _manager_payload()
            manager[array][0][field] = value
            workspace = _workspace_payload()
            workspace[array][0][field] = value
            with self.subTest(array=array, field=field, profile="manager"):
                self.blocked(validate_manager_report, manager, groups=[_group()])
            with self.subTest(array=array, field=field, profile="workspace"):
                self.assertEqual(self.workspace(workspace)["status"], "blocked")

    def test_unknown_phases_and_unexecuted_scoped_collection_actions_are_allowed(self):
        for profile in ("manager", "workspace"):
            payload = _manager_payload() if profile == "manager" else _workspace_payload()
            payload["anrModel"][0]["phases"] = [{
                "phase": "blocking", "summary": "Continuous blocking interval is unknown.",
                "certainty": "unknown", "supportingEventIds": [],
            }]
            payload["simulationSteps"][0]["actions"] = [{
                "step": 1, "action": "Collect matching lifecycle logs.", "groupId": "anr-test",
                "evidenceRefs": [], "metadata": {
                    **CONSERVATIVE_FLAGS, "validation": "unverified", "executed": False,
                },
            }]
            with self.subTest(profile=profile):
                if profile == "manager":
                    result = self.readiness(validate_manager_report, payload, groups=[_group()])
                else:
                    result = self.workspace(payload)
                self.assertIn(result["status"], DELIVERABLE_STATUSES)

    def test_explicit_workspace_model_and_plan_scope_must_match_the_incident(self):
        for array in ("inputEventAssessments", "anrModel", "simulationSteps"):
            payload = _workspace_payload()
            payload[array][0]["groupId"] = "other-anr"
            with self.subTest(array=array):
                self.assertEqual(self.workspace(payload)["status"], "blocked")


class UnsupportedTimingAdversarialTests(AcceptanceAssertions):
    def cross_clock_reconstruction(self):
        reconstruction = _reconstruction()
        reconstruction["events"].append({
            "eventId": "evt-monotonic", "rawLine": "unmapped monotonic observation",
            "groupId": "anr-test", "clockDomain": "monotonic:unmapped",
            "timestamp": None, "deltaTSeconds": None, "certainty": "observed",
        })
        return reconstruction

    def test_uncalibrated_crossclock_asserted_delta_or_duration_cannot_be_delivered(self):
        for field, value in (("deltaTSeconds", 5.0), ("durationMs", 5000)):
            reconstruction = self.cross_clock_reconstruction()
            for profile in ("manager", "workspace"):
                payload = _manager_payload() if profile == "manager" else _workspace_payload()
                payload["anrModel"][0].update({
                    "summary": "The referenced observations establish a measured continuous blocking interval.",
                    "supportingEventIds": ["evt-one", "evt-monotonic"], "uncertainties": [], field: value,
                })
                with self.subTest(field=field, profile=profile):
                    if profile == "manager":
                        self.held(validate_manager_report, payload, groups=[_group(reconstruction)])
                    else:
                        self.held(validate_workspace_content, _workspace_text(payload), reconstruction, expected_group_id="anr-test")

    def test_a_single_stack_snapshot_does_not_prove_a_continuous_duration(self):
        reconstruction = _reconstruction()
        reconstruction["events"][0].update(eventType="trace_snapshot", rawLine="main thread blocked at the dump instant")
        for profile in ("manager", "workspace"):
            payload = _manager_payload(reference="evt-one") if profile == "manager" else _workspace_payload(reference="evt-one")
            payload["anrModel"][0].update(durationMs=5000, uncertainties=[])
            with self.subTest(profile=profile):
                if profile == "manager":
                    self.held(validate_manager_report, payload, groups=[_group(reconstruction)])
                else:
                    self.held(validate_workspace_content, _workspace_text(payload), reconstruction, expected_group_id="anr-test")

    def test_unknown_timing_and_disclosed_crossclock_gaps_remain_deliverable(self):
        reconstruction = self.cross_clock_reconstruction()
        for profile in ("manager", "workspace"):
            payload = _manager_payload() if profile == "manager" else _workspace_payload()
            payload["anrModel"][0].update({
                "supportingEventIds": ["evt-one", "evt-monotonic"],
                "deltaTSeconds": None, "durationMs": None,
                "uncertainties": ["Clock domains are not calibrated; no ordering or blocking duration is established."],
            })
            before = deepcopy((payload, reconstruction))
            with self.subTest(profile=profile):
                if profile == "manager":
                    result = self.readiness(validate_manager_report, payload, groups=[_group(reconstruction)])
                else:
                    result = self.workspace(payload, reconstruction)
                self.assertIn(result["status"], DELIVERABLE_STATUSES)
                self.assertEqual((payload, reconstruction), before)


if __name__ == "__main__":
    unittest.main()
