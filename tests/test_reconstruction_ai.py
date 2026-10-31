"""Tests for the independent, untrusted reconstruction AI layer."""

from __future__ import annotations

from copy import deepcopy
import json
import unittest

from anr_evidence.reconstruction_ai import attach_ai_analysis


def _base_report() -> dict:
    return {
        "groupId": "group-1",
        "events": [
            {
                "eventId": "event-1",
                "rawLine": "10-31 10:00:00.000  100  101 I InputDispatcher: waiting for focus",
                "evidenceRefs": ["logcat.txt:12"],
                "timestamp": "10-31 10:00:00.000",
                "deltaTSeconds": -5.0,
                "certainty": "observed",
            },
            {
                "eventId": "event-2",
                "rawLine": "10-31 10:00:05.000  100  102 I am_anr: com.example.app",
                "evidenceRefs": ["events.txt:34"],
                "timestamp": "10-31 10:00:05.000",
                "deltaTSeconds": 0.0,
                "certainty": "observed",
            },
        ],
        "warnings": [{"code": "evidence-gap", "message": "Trace unavailable."}],
        "finalJudgment": False,
        "notRootCauseYet": True,
        "requiresHumanConfirmation": True,
        "metadata": {"sourceFiles": ["logcat.txt", "events.txt"]},
    }


def _fenced(payload: object) -> str:
    return "```json\n" + json.dumps(payload) + "\n```"


class ReconstructionAiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = _base_report()

    def assert_base_unchanged(self, result: dict) -> None:
        for key, value in self.report.items():
            self.assertEqual(result[key], value, key)

    def test_no_ai_has_absent_status_and_empty_layer(self) -> None:
        for analyses in (None, {}):
            with self.subTest(analyses=analyses):
                result = attach_ai_analysis(self.report, analyses)
                self.assertEqual(result["aiStatus"], "absent")
                for key in ("aiClaims", "aiAnalyses", "aiWarnings"):
                    self.assertEqual(result[key], [])
                self.assert_base_unchanged(result)

    def test_plain_text_is_preserved_verbatim_and_never_becomes_events(self) -> None:
        text = "  Hypothesis: the main thread waited five seconds.\r\n\nNo evidence yet.  "
        analyses = {"trace": text, "eventlog": "", "final-anr": "\nSummary\n"}
        result = attach_ai_analysis(self.report, analyses)
        self.assertEqual(result["aiAnalyses"], [
            {"slot": slot, "text": body, "certainty": "inferred", "validation": "unverified"}
            for slot, body in analyses.items()
        ])
        self.assertEqual(result["aiClaims"], [])
        self.assertEqual(result["aiWarnings"], [])
        self.assertEqual(result["aiStatus"], "unverified")
        self.assert_base_unchanged(result)

    def test_all_candidate_collections_are_separate_from_observed_events(self) -> None:
        payload = {
            "candidateChains": [{"summary": "Possible upstream chain."}],
            "candidateConclusions": [{"statement": "Possible focus issue."}],
            "events": [{"eventId": "invented", "timestamp": "imagined", "certainty": "observed"}],
        }
        for external in (False, True):
            with self.subTest(external=external):
                result = (
                    attach_ai_analysis(self.report, ai_report=payload) if external
                    else attach_ai_analysis(self.report, {"final-anr": _fenced(payload)})
                )
                self.assertEqual(result["aiStatus"], "has_candidates")
                self.assertEqual([claim["kind"] for claim in result["aiClaims"]], list(payload))
                for claim in result["aiClaims"]:
                    self.assertEqual(claim["certainty"], "inferred")
                    self.assertEqual(claim["validation"], "unverified")
                    self.assertEqual(claim["resolvedEventIds"], [])
                self.assert_base_unchanged(result)

    def test_fences_in_all_slots_and_external_report_are_combined(self) -> None:
        payload = {"candidateChains": [{"summary": "candidate"}]}
        slots = ("trace", "eventlog", "logcat-anrmanager", "final-anr")
        analyses = {slot: _fenced(payload) for slot in slots}
        result = attach_ai_analysis(self.report, analyses, payload)
        self.assertEqual(len(result["aiAnalyses"]), 4)
        self.assertEqual(len(result["aiClaims"]), 5)
        self.assertEqual(result["aiClaims"][-1]["source"], "ai_report")

    def test_invalid_json_is_warned_without_losing_text_or_later_candidates(self) -> None:
        for invalid in ('{"candidateChains": [}', '{"a": 1,}', "", '{"a": NaN}',
                        '{"a": Infinity}', '{"a": -Infinity}', '{"a": 1e9999}'):
            with self.subTest(invalid=invalid):
                text = "```json\n" + invalid + "\n```\n" + _fenced({"events": [{"summary": "candidate"}]})
                result = attach_ai_analysis(self.report, {"final-anr": text})
                self.assertEqual(result["aiAnalyses"][0]["text"], text)
                self.assertEqual(len(result["aiClaims"]), 1)
                self.assertEqual(result["aiWarnings"][0]["code"], "ai-invalid-json")
                self.assert_base_unchanged(result)

    def test_unterminated_json_fence_is_a_warning(self) -> None:
        text = '```json\n{"events": [{"summary": "candidate"}]}'
        result = attach_ai_analysis(self.report, {"final-anr": text})
        self.assertEqual(result["aiClaims"], [])
        self.assertEqual(result["aiWarnings"][0]["code"], "ai-invalid-json")
        self.assertEqual(result["aiAnalyses"][0]["text"], text)

    def test_json_root_must_be_an_object(self) -> None:
        for value in ([], [1], "text", None, 7, True):
            with self.subTest(value=value):
                result = attach_ai_analysis(self.report, {"final-anr": _fenced(value)})
                self.assertEqual(result["aiClaims"], [])
                self.assertEqual(result["aiWarnings"][0]["code"], "ai-invalid-report")
                self.assertEqual(result["aiStatus"], "unverified")

    def test_json_inside_non_json_fences_is_not_interpreted(self) -> None:
        text = '````markdown\n' + _fenced({"events": [{"summary": "not a claim"}]}) + '\n````'
        result = attach_ai_analysis(self.report, {"trace": text})
        self.assertEqual(result["aiClaims"], [])
        self.assertEqual(result["aiWarnings"], [])

    def test_case_insensitive_indented_crlf_and_long_fences(self) -> None:
        text = '  ````JSON\r\n{"events": [{"summary": "candidate"}]}\r\n  ````\r\n'
        result = attach_ai_analysis(self.report, {"trace": text})
        self.assertEqual(len(result["aiClaims"]), 1)
        self.assertEqual(result["aiWarnings"], [])

    def test_exact_id_and_raw_line_references_stay_inferred(self) -> None:
        raw_line = self.report["events"][1]["rawLine"]
        payload = {"candidateChains": [{
            "summary": "Possible causal link, not proven.",
            "evidenceRefs": ["event-1"],
            "evidence": [raw_line],
            "supportingEventIds": ["event-1", "event-2"],
            "certainty": "observed",
            "validation": "verified",
            "resolvedEventIds": ["forged"],
        }]}
        result = attach_ai_analysis(self.report, ai_report=payload)
        claim = result["aiClaims"][0]
        self.assertEqual(claim["certainty"], "inferred")
        self.assertEqual(claim["validation"], "references_resolved")
        self.assertEqual(claim["resolvedEventIds"], ["event-1", "event-2"])
        self.assertIn("does not verify", claim["validationNote"])
        self.assertEqual(claim["evidence"], [raw_line])
        self.assertEqual(result["aiWarnings"], [])
        self.assert_base_unchanged(result)

    def test_each_reference_alias_is_validated(self) -> None:
        for field in ("evidenceRefs", "evidence", "supportingEventIds"):
            with self.subTest(field=field):
                result = attach_ai_analysis(self.report, ai_report={"events": [{field: ["event-1"]}]})
                claim = result["aiClaims"][0]
                self.assertEqual(claim["resolvedEventIds"], ["event-1"])
                self.assertEqual(claim["validation"], "references_resolved")

    def test_fake_partial_and_non_string_references_are_invalid(self) -> None:
        raw_line = self.report["events"][0]["rawLine"]
        references = [
            "missing-event", "event-1 ", "event", raw_line[1:], raw_line + " ",
            "InputDispatcher: waiting for focus", "logcat.txt:12", "", 1, True,
            None, {}, [], {"eventId": "event-1"},
        ]
        for reference in references:
            with self.subTest(reference=reference):
                result = attach_ai_analysis(self.report, ai_report={"candidateChains": [{"evidence": [reference]}]})
                claim = result["aiClaims"][0]
                self.assertEqual(claim["validation"], "invalid_references")
                self.assertEqual(claim["resolvedEventIds"], [])
                self.assertEqual(claim["certainty"], "inferred")
                self.assertEqual(result["aiWarnings"][0]["code"], "ai-invalid-references")
                self.assert_base_unchanged(result)

    def test_duplicate_raw_lines_are_ambiguous_but_ids_still_resolve(self) -> None:
        duplicate = deepcopy(self.report["events"][0])
        duplicate["eventId"] = "event-3"
        self.report["events"].append(duplicate)
        result = attach_ai_analysis(self.report, ai_report={"events": [
            {"evidence": [duplicate["rawLine"]]}, {"evidence": ["event-1"]},
        ]})
        self.assertEqual(result["aiClaims"][0]["validation"], "invalid_references")
        self.assertEqual(result["aiClaims"][1]["validation"], "references_resolved")

    def test_one_bad_reference_prevents_all_resolved_status(self) -> None:
        result = attach_ai_analysis(self.report, ai_report={"candidateChains": [{
            "evidenceRefs": ["event-1"], "supportingEventIds": ["forged"],
        }]})
        self.assertEqual(result["aiClaims"][0]["validation"], "invalid_references")
        self.assertEqual(result["aiClaims"][0]["resolvedEventIds"], ["event-1"])

    def test_empty_reference_lists_are_unverified(self) -> None:
        result = attach_ai_analysis(self.report, ai_report={"events": [{
            "evidenceRefs": [], "evidence": [], "supportingEventIds": [],
        }]})
        self.assertEqual(result["aiClaims"][0]["validation"], "unverified")
        self.assertEqual(result["aiWarnings"], [])

    def test_cross_group_report_is_rejected_without_contaminating_local_text(self) -> None:
        payload = {"groupId": "group-2", "events": [{"evidenceRefs": ["event-1"]}]}
        result = attach_ai_analysis(self.report, {"trace": "Local text."}, payload)
        self.assertEqual(result["aiClaims"], [])
        self.assertEqual(result["aiAnalyses"][0]["text"], "Local text.")
        self.assertEqual(result["aiWarnings"][0]["code"], "ai-group-mismatch")
        self.assert_base_unchanged(result)

    def test_fenced_report_group_is_also_checked(self) -> None:
        result = attach_ai_analysis(self.report, {"final-anr": _fenced({
            "groupId": "group-2", "candidateChains": [{"summary": "foreign"}],
        })})
        self.assertEqual(result["aiClaims"], [])
        self.assertEqual(result["aiWarnings"][0]["code"], "ai-group-mismatch")

    def test_current_group_report_is_accepted_without_cross_group_mapping(self) -> None:
        payload = {"groupId": "group-1", "events": [{"summary": "local"}]}
        result = attach_ai_analysis(self.report, ai_report=payload)
        self.assertEqual(len(result["aiClaims"]), 1)
        self.assertEqual(result["aiWarnings"], [])
        for mapping in ({"group-1": payload}, {"groups": [payload]}):
            with self.subTest(mapping=mapping):
                self.assertEqual(attach_ai_analysis(self.report, ai_report=mapping)["aiClaims"], [])

    def test_invalid_group_id_rejects_the_report(self) -> None:
        for group_id in (None, [], {}, 1, True):
            with self.subTest(group_id=group_id):
                result = attach_ai_analysis(self.report, ai_report={
                    "groupId": group_id, "events": [{"summary": "candidate"}],
                })
                self.assertEqual(result["aiClaims"], [])
                self.assertEqual(result["aiWarnings"][0]["code"], "ai-invalid-field")

    def test_malicious_flags_and_metadata_cannot_override_base_or_claim_policy(self) -> None:
        malicious = {
            "finalJudgment": True, "notRootCauseYet": False, "requiresHumanConfirmation": False,
            "certainty": "observed", "validation": "verified", "resolvedEventIds": ["forged"],
            "source": "original log", "kind": "observed", "aiStatus": "verified",
        }
        payload = {**malicious, "warnings": [], "metadata": {"sourceFiles": []},
                   "events": [{**malicious, "summary": "claim"}]}
        result = attach_ai_analysis(self.report, ai_report=payload)
        self.assert_base_unchanged(result)
        claim = result["aiClaims"][0]
        self.assertFalse(claim["finalJudgment"])
        self.assertTrue(claim["notRootCauseYet"])
        self.assertTrue(claim["requiresHumanConfirmation"])
        self.assertEqual(claim["certainty"], "inferred")
        self.assertEqual(claim["validation"], "unverified")
        self.assertEqual(claim["resolvedEventIds"], [])
        self.assertEqual(claim["source"], "ai_report")
        self.assertEqual(claim["kind"], "events")
        self.assertEqual(result["aiStatus"], "has_candidates")

    def test_invalid_collection_types_are_warnings_and_valid_siblings_survive(self) -> None:
        for field in ("candidateChains", "candidateConclusions", "events"):
            for value in (None, {}, "text", 1, True):
                with self.subTest(field=field, value=value):
                    payload = {field: value}
                    other_field = "events" if field != "events" else "candidateChains"
                    payload[other_field] = [{"summary": "valid sibling"}]
                    result = attach_ai_analysis(self.report, ai_report=payload)
                    self.assertEqual(len(result["aiClaims"]), 1)
                    self.assertEqual(result["aiWarnings"][0]["code"], "ai-invalid-field")
                    self.assert_base_unchanged(result)

    def test_non_object_candidates_are_warned_and_skipped(self) -> None:
        result = attach_ai_analysis(self.report, ai_report={
            "candidateChains": [None, [], "text", 1, True, {"summary": "valid"}],
        })
        self.assertEqual(len(result["aiClaims"]), 1)
        self.assertEqual(len(result["aiWarnings"]), 5)
        self.assertTrue(all(w["code"] == "ai-invalid-claim" for w in result["aiWarnings"]))

    def test_invalid_reference_field_types_are_not_coerced(self) -> None:
        for field in ("evidenceRefs", "evidence", "supportingEventIds"):
            for value in (None, {}, "event-1", 1, True):
                with self.subTest(field=field, value=value):
                    result = attach_ai_analysis(self.report, ai_report={"events": [{field: value}]})
                    self.assertEqual(result["aiClaims"][0]["validation"], "invalid_references")
                    self.assertEqual(result["aiWarnings"][0]["code"], "ai-invalid-references")

    def test_invalid_analysis_container_and_slot_types_are_warnings(self) -> None:
        for analyses in ([], "text", 1, True):
            with self.subTest(analyses=analyses):
                result = attach_ai_analysis(self.report, analyses)
                self.assertEqual(result["aiAnalyses"], [])
                self.assertEqual(result["aiWarnings"][0]["code"], "ai-invalid-analyses")
        result = attach_ai_analysis(self.report, {"trace": None, 3: "text", "final-anr": "valid"})
        self.assertEqual([item["slot"] for item in result["aiAnalyses"]], ["final-anr"])
        self.assertEqual(len(result["aiWarnings"]), 2)

    def test_invalid_external_report_type_and_numbers_are_warnings(self) -> None:
        for payload in ([], "text", 1, True):
            with self.subTest(payload=payload):
                result = attach_ai_analysis(self.report, ai_report=payload)
                self.assertEqual(result["aiClaims"], [])
                self.assertEqual(result["aiWarnings"][0]["code"], "ai-invalid-report")
        for number in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(number=number):
                result = attach_ai_analysis(self.report, ai_report={"events": [{"deltaT": number}]})
                self.assertEqual(result["aiClaims"], [])
                self.assertEqual(result["aiWarnings"][0]["code"], "ai-invalid-json")

    def test_invalid_flag_types_are_warned_and_ignored(self) -> None:
        result = attach_ai_analysis(self.report, ai_report={
            "finalJudgment": [], "notRootCauseYet": "false", "requiresHumanConfirmation": None,
            "events": [{"summary": "candidate"}],
        })
        self.assertEqual(len(result["aiWarnings"]), 3)
        self.assertEqual(len(result["aiClaims"]), 1)
        self.assert_base_unchanged(result)

    def test_ai_only_reports_remain_candidates_without_creating_evidence(self) -> None:
        self.report["events"] = []
        result = attach_ai_analysis(self.report, ai_report={"events": [
            {"eventId": "ai-1", "rawLine": "AI invented event", "certainty": "observed"},
            {"supportingEventIds": ["ai-1"], "evidence": ["AI invented event"]},
        ]})
        self.assertEqual(result["events"], [])
        self.assertEqual(result["aiStatus"], "has_candidates")
        self.assertEqual([claim["certainty"] for claim in result["aiClaims"]], ["inferred", "inferred"])
        self.assertEqual(result["aiClaims"][0]["validation"], "unverified")
        self.assertEqual(result["aiClaims"][1]["validation"], "invalid_references")
        self.assert_base_unchanged(result)

    def test_ai_claims_cannot_validate_other_claims_or_other_sources(self) -> None:
        analyses = {"trace": _fenced({"events": [{"eventId": "ai-event", "rawLine": "invented"}]})}
        payload = {"candidateChains": [{"evidence": ["ai-event", "invented"]}]}
        result = attach_ai_analysis(self.report, analyses, payload)
        self.assertEqual(result["aiClaims"][-1]["validation"], "invalid_references")
        self.assertEqual(result["aiClaims"][-1]["resolvedEventIds"], [])

    def test_inputs_and_nested_objects_are_never_modified_or_shared(self) -> None:
        analyses = {"final-anr": _fenced({"candidateChains": [{"evidence": ["event-1"]}]})}
        payload = {"events": [{"evidenceRefs": ["event-2"], "details": {"items": ["value"]}}]}
        before = deepcopy((self.report, analyses, payload))
        result = attach_ai_analysis(self.report, analyses, payload)
        self.assertEqual((self.report, analyses, payload), before)
        self.assertIsNot(result, self.report)
        self.assertIsNot(result["events"], self.report["events"])
        result["events"][0]["evidenceRefs"].append("changed")
        result["warnings"][0]["message"] = "changed"
        result["metadata"]["sourceFiles"].clear()
        result["aiClaims"][-1]["evidenceRefs"].append("changed")
        result["aiClaims"][-1]["details"]["items"].clear()
        result["aiAnalyses"][0]["text"] = "changed"
        self.assertEqual((self.report, analyses, payload), before)

    def test_refresh_replaces_stale_ai_layer_and_is_idempotent(self) -> None:
        payload = {"events": [{"evidence": ["event-1"]}]}
        first = attach_ai_analysis(self.report, ai_report=payload)
        self.assertEqual(attach_ai_analysis(first, ai_report=payload), first)
        refreshed = attach_ai_analysis(first)
        self.assertEqual(refreshed["aiStatus"], "absent")
        self.assertEqual(refreshed["aiClaims"], [])
        self.assertEqual(refreshed["aiAnalyses"], [])
        self.assertEqual(refreshed["aiWarnings"], [])
        self.assert_base_unchanged(refreshed)

    def test_empty_external_report_is_unverified(self) -> None:
        result = attach_ai_analysis(self.report, ai_report={})
        self.assertEqual(result["aiStatus"], "unverified")
        self.assertEqual(result["aiClaims"], [])
        self.assertEqual(result["aiWarnings"], [])


if __name__ == "__main__":
    unittest.main()
