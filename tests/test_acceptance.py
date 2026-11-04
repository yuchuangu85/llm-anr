from copy import deepcopy
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

from anr_evidence import AiContextOptions, build_ai_context_artifacts, load_package_from_fixture
from anr_evidence.acceptance import finalize_workspace_reports, validate_workspace_reports
from anr_evidence.ai_context import _merge_analyses
from anr_evidence.reconstruction import refresh_reconstruction
from tests.acceptance_helpers import fill_workspace, report_payload, final_analysis


ROOT = Path(__file__).resolve().parent.parent


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = load_package_from_fixture(ROOT / "tests/fixtures/nfw_01.json")
        self.index = build_ai_context_artifacts(self.package, AiContextOptions(out_dir=self.root))
        self.group = self.index["groups"][0]

    def prepare(self):
        fill_workspace(self.group)
        refresh_reconstruction(self.root)

    def check(self):
        return validate_workspace_reports(self.root)["groups"][0]["deliveryReadiness"]

    def test_pending_workspace_is_readonly_blocked_and_still_refreshable(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.check()["status"], "blocked")
        self.assertEqual(before, {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
        self.assertEqual(refresh_reconstruction(self.root)["groupCount"], 1)

    def test_analysis_edit_needs_refresh_but_finalize_performs_it(self):
        fill_workspace(self.group)
        self.assertIn("RECONSTRUCTION_OUTDATED", {x["code"] for x in self.check()["blockers"]})
        result = finalize_workspace_reports(self.root)
        self.assertTrue(result["deliverable"])
        path = Path(result["groups"][0]["artifactPaths"]["acceptance"])
        receipt = json.loads(path.read_text())
        self.assertEqual(receipt["status"], "ready_with_limitations")
        self.assertFalse(receipt["rootCauseConfirmed"])
        self.assertIn("analysisDigest", receipt)
        index = json.loads((self.root / "index.json").read_text())
        self.assertTrue(index["groups"][0]["analysisComplete"])
        self.assertEqual(index["groups"][0]["deliveryReadiness"]["status"], "ready_with_limitations")

    def test_changed_analysis_invalidates_old_receipt(self):
        self.prepare()
        finalize_workspace_reports(self.root)
        path = Path(self.group["artifactPaths"]["analysis"])
        path.write_text(path.read_text().replace("证据不足", "新结论证据不足", 1))
        result = self.check()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("RECONSTRUCTION_OUTDATED", {x["code"] for x in result["blockers"]})
        self.assertNotEqual(result["receiptStatus"], "current")

    def test_manual_evidence_edit_cannot_be_approved_by_finalize(self):
        self.prepare()
        path = Path(self.group["artifactPaths"]["logcat"])
        path.write_text(path.read_text() + "\nmodified evidence\n")
        result = finalize_workspace_reports(self.root)
        self.assertFalse(result["deliverable"])
        self.assertIn("EVIDENCE_CHANGED", {x["code"] for x in result["groups"][0]["deliveryReadiness"]["blockers"]})

    def test_regeneration_keeps_old_analysis_but_cannot_revalidate_it(self):
        self.prepare()
        altered = deepcopy(self.package)
        altered["sources"]["logcat"]["content"] += "\n04-12 10:00:04.000 WindowManager: Focus request com.demo windowToken=new"
        build_ai_context_artifacts(altered, AiContextOptions(out_dir=self.root))
        result = finalize_workspace_reports(self.root)
        self.assertFalse(result["deliverable"])
        self.assertIn("STALE_ANALYSIS", {x["code"] for x in result["groups"][0]["deliveryReadiness"]["blockers"]})

    def test_missing_or_modified_artifact_invalidates_acceptance(self):
        self.prepare()
        finalize_workspace_reports(self.root)
        path = Path(self.group["artifactPaths"]["reconstruction"])
        path.write_text(path.read_text() + "\nUnreviewed mutation\n")
        self.assertIn("ARTIFACT_MISMATCH", {x["code"] for x in self.check()["blockers"]})
        path.unlink()
        self.assertIn("MISSING_ARTIFACT", {x["code"] for x in self.check()["blockers"]})

    def test_fenced_fake_heading_and_review_cannot_override_hard_errors(self):
        self.prepare()
        path = Path(self.group["artifactPaths"]["analysis"])
        path.write_text(path.read_text().replace("## 时间线\n", "```text\n## 时间线\n```\n"))
        result = finalize_workspace_reports(self.root, review_records={"reviewer": "self", "status": "approved"})
        self.assertFalse(result["deliverable"])

    def test_multi_anr_failure_does_not_hide_individually_accepted_group(self):
        package = deepcopy(self.package)
        for source in package["sources"].values():
            source["content"] += "\n" + source["content"].replace("10:00:", "10:01:")
        groups = build_ai_context_artifacts(package, AiContextOptions(out_dir=self.root))["groups"]
        fill_workspace(groups[0])
        result = finalize_workspace_reports(self.root)
        self.assertFalse(result["deliverable"])
        self.assertEqual(result["readyGroupIds"], [groups[0]["id"]])
        self.assertEqual(result["blockedGroupIds"], [groups[1]["id"]])

    def test_index_traversal_or_symlink_is_rejected(self):
        index_path = self.root / "index.json"
        index = json.loads(index_path.read_text())
        index["groups"][0]["id"] = "../elsewhere"
        index_path.write_text(json.dumps(index))
        with self.assertRaises(ValueError):
            validate_workspace_reports(self.root)

    def test_same_snapshot_recheck_refuses_success_receipt_on_racing_edit(self):
        self.prepare()
        from anr_evidence import acceptance

        original = acceptance._assert_snapshot
        path = Path(self.group["artifactPaths"]["analysis"])

        def race(*args, **kwargs):
            path.write_text(path.read_text() + "\nconcurrent edit\n")
            return original(*args, **kwargs)

        with patch.object(acceptance, "_assert_snapshot", side_effect=race):
            result = finalize_workspace_reports(self.root)
        self.assertFalse(result["deliverable"])
        self.assertIn("WORKSPACE_CHANGED_DURING_FINALIZE", {x["code"] for x in result["groups"][0]["deliveryReadiness"]["blockers"]})

    def test_cli_has_machine_readable_failures_and_explicit_finalize(self):
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        checked = subprocess.run([sys.executable, "-m", "anr_evidence", "--validate-report", str(self.root)], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(checked.returncode, 2)
        self.assertFalse(json.loads(checked.stdout)["deliverable"])
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})
        fill_workspace(self.group)
        finished = subprocess.run([sys.executable, "-m", "anr_evidence", "--finalize", str(self.root)], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(finished.returncode, 0, finished.stdout + finished.stderr)
        self.assertTrue(json.loads(finished.stdout)["deliverable"])

    def test_review_is_bound_to_issue_and_current_content_not_model_self_report(self):
        analyses = fill_workspace(self.group)
        payload = report_payload(self.group["id"])
        payload["reviewRequests"] = [{"summary": "两段证据相互矛盾，需要人工核对归属。", "supportingEventIds": []}]
        path = Path(self.group["artifactPaths"]["analysis"])
        analyses["final-anr"] = final_analysis(payload)
        path.write_text(_merge_analyses(path.read_text(), analyses))
        refresh_reconstruction(self.root)
        result = self.check()
        self.assertEqual(result["status"], "needs_review")
        record = {
            "source": "external-review", "reviewer": "local-auditor", "groupId": self.group["id"],
            "scope": "核对两侧归属，保留为未证实候选。", "rationale": "不据此确定原因，仅交付未知结论。",
            "evidenceRevision": result["evidenceRevision"], "analysisDigest": result["analysisDigest"],
            "issueIds": [result["reviewItems"][0]["issueId"]], "decision": "accepted_with_limitations",
        }
        accepted = finalize_workspace_reports(self.root, review_records={"reviews": [record]})
        self.assertTrue(accepted["deliverable"])
        self.assertEqual(accepted["groups"][0]["deliveryReadiness"]["status"], "ready_with_limitations")
        wrong = deepcopy(record)
        wrong["analysisDigest"] = "previous-body"
        self.assertFalse(finalize_workspace_reports(self.root, review_records={"reviews": [wrong]})["deliverable"])

    def test_safe_refresh_does_not_require_slot_completion_or_receipt(self):
        self.assertEqual(refresh_reconstruction(self.root)["groupCount"], 1)
        self.assertFalse((Path(self.group["artifactPaths"]["analysis"]).parent / "acceptance.json").exists())

    def test_rewritten_analysis_can_explicitly_bind_to_current_evidence_after_regeneration(self):
        self.prepare()
        altered = deepcopy(self.package)
        altered["sources"]["logcat"]["content"] += "\n04-12 10:00:04.000 WindowManager: Focus request com.demo windowToken=new"
        current = build_ai_context_artifacts(altered, AiContextOptions(out_dir=self.root))["groups"][0]
        path = Path(current["artifactPaths"]["analysis"])
        payload = report_payload(current["id"])
        payload["evidenceRevision"] = current["evidenceRevision"]
        payload["evidenceGaps"].append("已核对新焦点请求，但它不证明焦点恢复，原结论仍保持未知。")
        path.write_text(_merge_analyses(path.read_text(), {"final-anr": final_analysis(payload)}))
        self.assertTrue(finalize_workspace_reports(self.root)["deliverable"])

    def test_changing_only_format_or_copying_old_version_does_not_clear_stale_analysis(self):
        self.prepare()
        altered = deepcopy(self.package)
        altered["sources"]["logcat"]["content"] += "\n04-12 10:00:04.000 WindowManager: Focus request com.demo windowToken=new"
        build_ai_context_artifacts(altered, AiContextOptions(out_dir=self.root))
        path = Path(self.group["artifactPaths"]["analysis"])
        path.write_text(path.read_text() + "\n\n")
        self.assertFalse(finalize_workspace_reports(self.root)["deliverable"])

    def test_review_record_is_reused_only_when_its_receipt_still_matches_all_inputs(self):
        analyses = fill_workspace(self.group)
        payload = report_payload(self.group["id"])
        payload["reviewRequests"] = [{"summary": "核验矛盾日志归属。", "supportingEventIds": []}]
        path = Path(self.group["artifactPaths"]["analysis"])
        analyses["final-anr"] = final_analysis(payload)
        path.write_text(_merge_analyses(path.read_text(), analyses))
        refresh_reconstruction(self.root)
        ready = self.check()
        record = {
            "groupId": self.group["id"], "source": "external-review", "reviewer": "auditor",
            "scope": "矛盾归属", "rationale": "两条日志未能建立同一对象关系，降级为未知。",
            "evidenceRevision": ready["evidenceRevision"], "analysisDigest": ready["analysisDigest"],
            "issueIds": [ready["reviewItems"][0]["issueId"]], "decision": "accepted_with_limitations",
        }
        self.assertTrue(finalize_workspace_reports(self.root, review_records={"reviews": [record]})["deliverable"])
        self.assertTrue(validate_workspace_reports(self.root)["deliverable"])

    def test_race_after_atomic_receipt_write_cannot_leave_success_in_index(self):
        self.prepare()
        from anr_evidence import acceptance

        original = acceptance._atomic_json
        analysis = Path(self.group["artifactPaths"]["analysis"])

        def race(path, value):
            original(path, value)
            if path.name == "acceptance.json" and value.get("status") in {"ready", "ready_with_limitations"}:
                analysis.write_text(analysis.read_text() + "\nnew conclusion\n")

        with patch.object(acceptance, "_atomic_json", side_effect=race):
            result = finalize_workspace_reports(self.root)
        self.assertFalse(result["deliverable"])
        index = json.loads((self.root / "index.json").read_text())
        self.assertEqual(index["groups"][0]["deliveryReadiness"]["status"], "blocked")

    def test_parent_index_membership_cannot_be_bypassed_by_group_path(self):
        self.prepare()
        finalize_workspace_reports(self.root)
        index_path = self.root / "index.json"
        index = json.loads(index_path.read_text())
        index["groups"][0]["id"] = "different-index-group"
        index_path.write_text(json.dumps(index))
        result = validate_workspace_reports(Path(self.group["artifactPaths"]["analysis"]).parent)
        self.assertFalse(result["deliverable"])
        self.assertIn("GROUP_NOT_IN_INDEX", {x["code"] for x in result["groups"][0]["deliveryReadiness"]["blockers"]})

    def test_static_completeness_disagreement_cannot_be_finalized(self):
        self.prepare()
        path = Path(self.group["artifactPaths"]["analysis"])
        text = path.read_text().replace("- 完整: `True`", "- 完整: `False`")
        path.write_text(text)
        result = finalize_workspace_reports(self.root)
        self.assertFalse(result["deliverable"])
        self.assertIn("COMPLETENESS_MISMATCH", {x["code"] for x in result["groups"][0]["deliveryReadiness"]["blockers"]})

    def test_invalid_receipt_does_not_authorize_replaying_saved_reviews(self):
        payload = report_payload(self.group["id"])
        payload["reviewRequests"] = [{"summary": "复核矛盾证据归属。", "evidenceRefs": []}]
        fill_workspace(self.group)
        path = Path(self.group["artifactPaths"]["analysis"])
        path.write_text(_merge_analyses(path.read_text(), {"final-anr": final_analysis(payload)}))
        refresh_reconstruction(self.root)
        pending = self.check()
        record = {"groupId": self.group["id"], "source": "external-review", "reviewer": "auditor", "scope": "归属", "rationale": "保留为未知。", "decision": "accepted_with_limitations", "evidenceRevision": pending["evidenceRevision"], "analysisDigest": pending["analysisDigest"], "issueIds": [pending["reviewItems"][0]["issueId"]]}
        outcome = finalize_workspace_reports(self.root, review_records={"reviews": [record]})
        receipt_path = Path(outcome["groups"][0]["artifactPaths"]["acceptance"])
        receipt = json.loads(receipt_path.read_text())
        receipt.update(schemaVersion="not-a-receipt", status="blocked", blockers=[{"code": "DENIED"}])
        receipt_path.write_text(json.dumps(receipt))
        self.assertFalse(validate_workspace_reports(self.root)["deliverable"])


if __name__ == "__main__":
    unittest.main()
