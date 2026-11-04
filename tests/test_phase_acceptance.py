from __future__ import annotations

from copy import deepcopy
import unittest
from unittest.mock import patch

from anr_evidence import render_analysis_report, render_final_delivery
from anr_evidence.analyzer import analyze_normalized_package
from anr_evidence.anr_strategy import ANR_TYPE_STRATEGIES
from anr_evidence.delivery import DeliveryError
from anr_evidence.extractor import extract_evidence_package
from anr_evidence.hypothesis import generate_causal_draft
from anr_evidence.normalizer import normalize_evidence_package
from anr_evidence.remediation import generate_remediation_drafts
from anr_evidence.report_validation import DELIVERABLE_STATUSES, validate_phase_report
from anr_evidence.root_cause import generate_root_cause_report
from tests.helpers import load_fixture


def _phases(fixture_name: str = "nfw_01.json", *, anr_type: str | None = None) -> list[dict]:
    normalized = normalize_evidence_package(extract_evidence_package(load_fixture(fixture_name)))
    if anr_type is not None:
        normalized["classification"]["detectedType"] = anr_type
    analysis = analyze_normalized_package(normalized)
    causal = generate_causal_draft(analysis)
    root_cause = generate_root_cause_report(causal)
    remediation = generate_remediation_drafts(root_cause)
    return [analysis, causal, root_cause, remediation]


def _acceptance(status: str) -> dict:
    return {
        "status": status,
        "policyVersion": "phase-test-v1",
        "blockers": [{"code": "TEST_BLOCKER", "message": "Invalid carried evidence."}] if status == "blocked" else [],
        "reviewItems": [{"code": "TEST_REVIEW", "message": "Review a critical claim."}] if status == "needs_review" else [],
        "warnings": [{"code": "TEST_LIMIT", "message": "Candidate only; simulation unexecuted."}],
        "checks": {},
    }


class PhaseAcceptanceTests(unittest.TestCase):
    def assert_acceptance_shape(self, result: dict) -> None:
        self.assertIn(result["status"], {"blocked", "needs_review", "ready_with_limitations", "ready"})
        self.assertTrue(result["policyVersion"])
        for field in ("blockers", "reviewItems", "warnings", "checks"):
            self.assertIn(field, result)
        for field in ("blockers", "reviewItems", "warnings"):
            self.assertIsInstance(result[field], list)

    def assert_delivery_rejected(self, package: object) -> DeliveryError:
        original = deepcopy(package)
        with self.assertRaises(DeliveryError) as rejected:
            render_final_delivery(package)
        self.assertTrue(rejected.exception.code.startswith("DELIVERY_"))
        self.assertIn(rejected.exception.code, str(rejected.exception))
        self.assertEqual(package, original)
        return rejected.exception

    def test_typed_phase7_packages_validate_without_workspace_or_markdown_slots(self) -> None:
        for anr_type in ANR_TYPE_STRATEGIES:
            fixture = {
                "input_dispatching_timeout": "idt_01.json", "no_focus_window": "nfw_01.json",
            }.get(anr_type, "unk_01.json")
            package = _phases(fixture, anr_type=anr_type)[-1]
            with self.subTest(anr_type=anr_type):
                original = deepcopy(package)
                result = validate_phase_report(package)
                self.assert_acceptance_shape(result)
                self.assertIn(result["status"], DELIVERABLE_STATUSES, result)
                self.assertFalse(result["blockers"])
                self.assertFalse(result["reviewItems"])
                self.assertEqual(package, original)
                delivery = render_final_delivery(package)
                self.assertIn("# ANR 分析交付稿", delivery)
                self.assertIn("## 九、交付验收", delivery)
                self.assertIn(f"Candidate readiness: `{result['status']}`", delivery)
                self.assertIn(result["policyVersion"], delivery)
                self.assertIn("## ANR 发生模型", delivery)
                self.assertIn("## 模拟操作步骤", delivery)
                self.assertEqual(package, original)

    def test_missing_evidence_is_deliverable_with_explicit_limitations(self) -> None:
        for fixture in ("miss_trace_01.json", "miss_kernel_01.json", "unk_01.json"):
            package = _phases(fixture)[-1]
            with self.subTest(fixture=fixture):
                result = validate_phase_report(package)
                self.assertEqual(result["status"], "ready_with_limitations", result)
                self.assertTrue(result["warnings"])
                report = render_final_delivery(package)
                self.assertIn("ready_with_limitations", report)
                self.assertIn("## 七、全局限制", report)
                self.assertIn("不是最终定责结论", report)

    def test_legacy_phase7_uses_carried_snippets_and_preserves_fallbacks(self) -> None:
        for fixture in ("nfw_01.json", "unk_01.json"):
            package = _phases(fixture)[-1]
            package.pop("inputAnr")
            package.pop("incidentScenario")
            self.assertNotIn("normalizedRecords", package)
            self.assertNotIn("timeline", package)
            self.assertTrue(package["candidateChains"][0]["evidenceSnippets"])
            original = deepcopy(package)
            with self.subTest(fixture=fixture):
                result = validate_phase_report(package)
                self.assertEqual(result["status"], "ready_with_limitations", result)
                report = render_final_delivery(package)
                self.assertIn("## ANR 发生模型", report)
                self.assertIn("## 模拟操作步骤", report)
                self.assertEqual(package, original)

    def test_arbitrary_or_malformed_phase7_is_rejected_without_mutation(self) -> None:
        valid = _phases()[-1]
        cases: list[object] = [None, [], "phase7-remediation-draft", {}]
        for field, value in (
            ("metadata", []), ("classification", []), ("anchors", []),
            ("topConclusion", []), ("remediationDrafts", {}),
            ("globalLimitations", {}), ("warnings", {}),
            ("candidateChains", {}), ("candidateConclusions", {}),
            ("traceInsights", []), ("inputAnr", []), ("incidentScenario", []),
        ):
            package = deepcopy(valid)
            package[field] = value
            cases.append(package)
        package = deepcopy(valid)
        package["remediationDrafts"] = ["not a draft object"]
        cases.append(package)
        package = deepcopy(valid)
        package["topConclusion"]["supportingSnippets"] = [None]
        cases.append(package)
        for index, package in enumerate(cases):
            with self.subTest(case=index):
                self.assert_delivery_rejected(package)

    def test_conservative_boolean_flags_require_exact_values(self) -> None:
        valid = _phases()[-1]
        mutations = (
            ("finalAdvice", valid["metadata"], "finalAdvice", (True, "false", 0, None)),
            ("finalJudgment", valid["metadata"], "finalJudgment", (True, "false", 0)),
            ("notRootCauseYet", valid["candidateChains"][0], "notRootCauseYet", (False, "true", 1, None)),
            ("tentative", valid["topConclusion"], "tentative", (False, "true", 1, None)),
            ("requiresHumanConfirmation", valid["remediationDrafts"][0], "requiresHumanConfirmation", (False, "true", 1, None)),
        )
        for label, target, field, values in mutations:
            for value in values:
                with self.subTest(flag=label, value=value):
                    package, copied_target = deepcopy((valid, target))
                    copied_target[field] = value
                    self.assert_delivery_rejected(package)

    def test_executed_or_verified_simulation_cannot_be_delivered(self) -> None:
        for field, value in (("executed", True), ("validation", "verified")):
            package = _phases()[-1]
            package["incidentScenario"]["simulationSteps"][0][field] = value
            with self.subTest(field=field):
                self.assert_delivery_rejected(package)

    def test_unresolvable_supporting_reference_is_rejected(self) -> None:
        package = _phases()[-1]
        package["topConclusion"]["supportingEvidenceRefs"].append("norm-not-carried")
        self.assert_delivery_rejected(package)

    def test_invalid_reference_cannot_be_laundered_by_an_extra_snippet(self) -> None:
        normalized = normalize_evidence_package(extract_evidence_package(load_fixture("nfw_01.json")))
        analysis, _, _, valid = _phases()
        for field, evidence in (("timeline", analysis["timeline"]), ("normalizedRecords", normalized["normalizedRecords"])):
            with self.subTest(carried=field):
                package = deepcopy(valid)
                package[field] = deepcopy(evidence)
                package["topConclusion"]["supportingEvidenceRefs"].append("norm-forged")
                package["topConclusion"]["supportingSnippets"].append({
                    "recordRef": "norm-forged", "sourceKind": "event_log", "recordType": "event_marker",
                    "timestamp": "04-12 10:00:00.000", "summary": "Not in the carried trusted evidence.",
                })
                self.assert_delivery_rejected(package)

    def test_core_gate_rejects_blocked_and_needs_review_with_machine_codes(self) -> None:
        package = _phases()[-1]
        for status in ("blocked", "needs_review"):
            acceptance = _acceptance(status)
            with self.subTest(status=status), patch(
                "anr_evidence.report_validation.validate_phase_report", return_value=acceptance,
            ) as validate:
                error = self.assert_delivery_rejected(package)
                validate.assert_called_once_with(package)
                self.assertEqual(error.code, f"DELIVERY_{status.upper()}")
                self.assertEqual(error.acceptance, acceptance)
                issues = acceptance["blockers"] + acceptance["reviewItems"]
                self.assertIn(issues[0]["code"], str(error))

    def test_unknown_or_malformed_acceptance_results_fail_closed(self) -> None:
        package = _phases()[-1]
        cases = [None, [], {}, _acceptance("unrecognized")]
        for field, value in (("warnings", None), ("checks", None), ("policyVersion", "")):
            malformed = _acceptance("ready")
            malformed[field] = value
            cases.append(malformed)
        for field in ("blockers", "reviewItems"):
            contradictory = _acceptance("ready")
            contradictory[field] = [{"code": "NOT_READY", "message": "Unresolved issue."}]
            cases.append(contradictory)
        for acceptance in cases:
            with self.subTest(acceptance=acceptance), patch(
                "anr_evidence.report_validation.validate_phase_report", return_value=acceptance,
            ):
                for draft in (False, True):
                    with self.assertRaises(DeliveryError) as rejected:
                        render_final_delivery(package, draft=draft)
                    self.assertEqual(rejected.exception.code, "DELIVERY_INVALID_ACCEPTANCE")

    def test_acceptance_summary_does_not_change_core_result_or_input(self) -> None:
        package = _phases()[-1]
        acceptance = _acceptance("ready_with_limitations")
        acceptance["warnings"] *= 5
        original = deepcopy((package, acceptance))
        with patch("anr_evidence.report_validation.validate_phase_report", return_value=acceptance) as validate:
            report = render_final_delivery(package)
        validate.assert_called_once_with(package)
        self.assertIn("warnings: `5`", report)
        self.assertIn("TEST_LIMIT", report)
        self.assertEqual((package, acceptance), original)

    def test_explicit_failed_draft_runs_validation_but_never_claims_ready(self) -> None:
        package = _phases()[-1]
        package["metadata"]["finalAdvice"] = True
        original = deepcopy(package)
        self.assert_delivery_rejected(package)
        report = render_final_delivery(package, draft=True)
        self.assertIn("# ANR 分析草稿（非交付稿）", report)
        self.assertIn("Candidate readiness: `not_ready (draft)`", report)
        self.assertIn("Validation result（仅诊断）: `blocked`", report)
        self.assertNotIn("# ANR 分析交付稿", report)
        self.assertNotIn("`finalAdvice` 仍为 false", report)
        self.assertEqual(package, original)

    def test_explicit_draft_is_not_ready_even_if_the_core_allows_delivery(self) -> None:
        package = _phases()[-1]
        for status in ("ready", "ready_with_limitations", "needs_review"):
            with self.subTest(status=status), patch(
                "anr_evidence.report_validation.validate_phase_report", return_value=_acceptance(status),
            ) as validate:
                report = render_final_delivery(package, draft=True)
                validate.assert_called_once_with(package)
                self.assertIn("Candidate readiness: `not_ready (draft)`", report)
                self.assertNotIn("# ANR 分析交付稿", report)

    def test_draft_bypass_still_requires_renderable_containers(self) -> None:
        package = _phases()[-1]
        package["warnings"] = [None]
        with self.assertRaises(DeliveryError):
            render_final_delivery(package, draft=True)

    def test_analysis_report_remains_an_ungated_draft(self) -> None:
        package = _phases()[-1]
        package["metadata"]["finalAdvice"] = True
        with patch("anr_evidence.report_validation.validate_phase_report") as validate:
            report = render_analysis_report(package)
        validate.assert_not_called()
        self.assertNotIn("## 九、交付验收", report)


if __name__ == "__main__":
    unittest.main()
