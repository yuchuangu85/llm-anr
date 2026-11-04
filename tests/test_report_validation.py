from copy import deepcopy
import unittest

from anr_evidence.report_validation import (
    FINAL_REPORT_SECTIONS, analysis_digest, strict_json_loads, validate_workspace_content,
)
from tests.acceptance_helpers import final_analysis, report_payload


class ReportValidationTests(unittest.TestCase):
    def setUp(self):
        self.report = {
            "groupId": "anr-test", "anrType": "no_focus_window", "anchor": None,
            "events": [{"eventId": "evt-one", "rawLine": "am_anr observed", "clockDomain": "unknown", "timestamp": None}],
            "evidenceCompleteness": {"complete": False, "missingSources": ["trace"]},
        }

    def text(self, payload=None):
        parts = []
        for slot, boundary in (("trace", "EventLog 事件日志"), ("eventlog", "Logcat 系统日志"), ("logcat-anrmanager", "完整性检查")):
            parts.append(f"<!-- AI_ANALYSIS_SLOT:{slot} -->\n本来源证据缺失，不能确定阻塞函数。\n### {boundary}\n")
        parts.append("<!-- AI_ANALYSIS_SLOT:final-anr -->\n" + final_analysis(payload or report_payload()))
        return "\n".join(parts)

    def validate(self, payload=None, text=None):
        return validate_workspace_content(text or self.text(payload), self.report, expected_group_id="anr-test")

    def test_explicit_missing_evidence_is_deliverable_with_limitations(self):
        before = deepcopy(self.report)
        result = self.validate()
        self.assertEqual(result["status"], "ready_with_limitations")
        self.assertEqual(result["blockers"], [])
        self.assertFalse(result["rootCauseConfirmed"])
        self.assertEqual(self.report, before)

    def test_four_sentences_do_not_pass(self):
        text = self.text().split("<!-- AI_ANALYSIS_SLOT:final-anr -->")[0] + "<!-- AI_ANALYSIS_SLOT:final-anr -->\n完成。"
        self.assertEqual(self.validate(text=text)["status"], "blocked")

    def test_fake_headings_inside_code_do_not_satisfy_sections(self):
        text = self.text().replace("## 时间线\n", "```text\n## 时间线\n```\n")
        result = self.validate(text=text)
        self.assertIn("MISSING_REPORT_SECTION", {issue["code"] for issue in result["blockers"]})

    def test_duplicate_slot_is_rejected_but_quoted_marker_is_ignored(self):
        self.assertEqual(self.validate(text=self.text() + "\n<!-- AI_ANALYSIS_SLOT:trace -->\n重复")["status"], "blocked")
        quoted = "```text\n<!-- AI_ANALYSIS_SLOT:trace -->\n```\n" + self.text()
        self.assertNotEqual(self.validate(text=quoted)["status"], "blocked")

    def test_missing_foreign_and_ambiguous_references_block(self):
        for ref in ("evt-foreign", "missing"):
            with self.subTest(ref=ref):
                result = self.validate(report_payload(reference=ref))
                self.assertIn("INVALID_EVIDENCE_REF", {item["code"] for item in result["blockers"]})
        self.report["events"].append({"eventId": "evt-two", "rawLine": "am_anr observed"})
        self.assertEqual(self.validate(report_payload(reference="am_anr observed"))["status"], "blocked")

    def test_current_reference_does_not_promote_model_to_verified(self):
        payload = report_payload(reference="evt-one")
        self.assertNotEqual(self.validate(payload)["status"], "blocked")
        for field, value in (("executed", True), ("certainty", "observed"), ("validation", "verified")):
            wrong = deepcopy(payload)
            wrong["simulationSteps"][0][field] = value
            with self.subTest(field=field):
                self.assertEqual(self.validate(wrong)["status"], "blocked")

    def test_group_identity_and_conservative_flags_are_hard_rules(self):
        for key, value in (("groupId", "other-anr"), ("finalJudgment", True), ("notRootCauseYet", False), ("requiresHumanConfirmation", False)):
            payload = report_payload()
            payload[key] = value
            with self.subTest(key=key):
                self.assertEqual(self.validate(payload)["status"], "blocked")

    def test_empty_arrays_can_express_unknown_cause_but_not_empty_model(self):
        self.assertNotEqual(self.validate()["status"], "blocked")
        payload = report_payload()
        payload["anrModel"] = []
        self.assertEqual(self.validate(payload)["status"], "blocked")

    def test_finite_unique_json_is_required(self):
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e9999}', '{} trailing'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                strict_json_loads(text)
        self.assertEqual(strict_json_loads('{"a":null}'), {"a": None})

    def test_json_template_or_malformed_fields_are_rejected(self):
        payload = report_payload()
        payload["primaryRootCauseDescription"] = "<真实根因>"
        self.assertEqual(self.validate(payload)["status"], "blocked")
        payload = report_payload()
        payload["sourceAnalyses"]["trace"]["keyEvidence"] = "not-an-array"
        self.assertEqual(self.validate(payload)["status"], "blocked")

    def test_analysis_digest_ignores_blank_line_and_indentation_only_edits(self):
        self.assertEqual(analysis_digest({"trace": "结论\n\n证据"}), analysis_digest({"trace": "  结论  \n证据\n"}))
        self.assertNotEqual(analysis_digest({"trace": "原结论"}), analysis_digest({"trace": "新结论"}))

    def test_required_sections_include_current_incident_contract(self):
        self.assertIn("ANR 发生模型", FINAL_REPORT_SECTIONS)
        self.assertIn("模拟操作步骤", FINAL_REPORT_SECTIONS)

    def test_no_focus_branch_cannot_assert_a_published_ack_wait(self):
        payload = report_payload()
        payload["inputEventAssessments"][0]["mechanism"] = "input_ack_timeout"
        self.assertEqual(self.validate(payload)["status"], "blocked")

    def test_native_sequence_cannot_be_labeled_as_a_java_or_log_sequence(self):
        payload = report_payload()
        payload["inputEventAssessments"][0].update(nativeSeq="12", seqDomain="java")
        self.assertEqual(self.validate(payload)["status"], "blocked")

    def test_declared_job_completion_is_not_callback_return_proof(self):
        payload = report_payload()
        payload["anrType"] = self.report["anrType"] = "job_scheduler_timeout"
        payload["inputEventAssessments"] = []
        payload["anrModel"][0].update(completionEvidence="jobFinished", provesCallbackReturned=True)
        self.assertEqual(self.validate(payload)["status"], "blocked")

    def test_hint_ids_come_from_extracted_inventory_not_arbitrary_uppercase_text(self):
        payload = report_payload()
        payload["primaryRootCauseHintId"] = "MADE_UP_HINT"
        self.assertEqual(self.validate(payload, text="MADE_UP_HINT\n" + self.text(payload))["status"], "blocked")
        self.report["sourceHintIds"] = ["MADE_UP_HINT"]
        self.assertNotEqual(self.validate(payload)["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
