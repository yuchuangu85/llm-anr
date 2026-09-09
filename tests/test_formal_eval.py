from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from anr_evidence.formal_eval import (
    FORMAL_CASE_SCHEMA_VERSION,
    FORMAL_CORPUS_SCHEMA_VERSION,
    run_formal_eval_directory,
)


FORMAL_DIR = Path(__file__).parent / "fixtures" / "formal_eval"


class FormalEvalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.aggregate = run_formal_eval_directory(FORMAL_DIR)

    def test_synthetic_cases_verify_mechanisms_ontology_and_immutable_citations(self) -> None:
        self.assertEqual(self.aggregate.total, 2)
        self.assertEqual(self.aggregate.failed, 0)
        for case in self.aggregate.cases:
            with self.subTest(case=case.case_id):
                self.assertTrue(case.passed, case.notes)
                self.assertFalse(case.missing_mechanisms)
                self.assertFalse(case.ontology_errors)
                self.assertFalse(case.missing_citation_sources)
                self.assertGreaterEqual(case.verified_evidence_ref_count, 2)
                self.assertGreater(case.verified_citation_source_counts.get("event_log", 0), 0)
                self.assertGreater(case.verified_citation_source_counts.get("trace", 0), 0)
                self.assertEqual(case.model_calls, 0)
                self.assertEqual(case.ontology["evidenceStatus"], "observed")
                self.assertEqual(len(case.ontology["provenanceRefs"]), 2)

    def test_cross_version_and_production_corpus_gate_fails_closed(self) -> None:
        self.assertFalse(self.aggregate.qualified)
        self.assertEqual(self.aggregate.qualification_status, "blocked")
        self.assertEqual(self.aggregate.release_coverage, {"14": 0, "15": 0, "16": 0, "17": 2})
        self.assertEqual(self.aggregate.per_release_metrics["17"]["total"], 2)
        self.assertEqual(self.aggregate.per_release_metrics["17"]["passed"], 2)
        self.assertGreaterEqual(self.aggregate.per_release_metrics["17"]["verifiedEvidenceRefs"], 4)
        self.assertEqual(self.aggregate.per_release_metrics["14"]["total"], 0)
        self.assertIn("insufficient_release_coverage:14:0<1", self.aggregate.gate_failures)
        self.assertIn("insufficient_release_coverage:15:0<1", self.aggregate.gate_failures)
        self.assertIn("insufficient_release_coverage:16:0<1", self.aggregate.gate_failures)
        self.assertIn("non_production_source_classes:synthetic", self.aggregate.gate_failures)

    def test_frozen_schema_scores_each_complete_output_domain_and_rule_pair(self) -> None:
        expected_domains = {
            "trigger", "blocker", "evidence_status", "citations",
            "contradictions", "high_confidence_violations",
        }
        self.assertEqual(set(self.aggregate.labeling_scope["fullyLabeledOutputDomains"]), expected_domains)
        self.assertTrue(self.aggregate.labeling_scope["notFullyLabeledOutputDomains"])
        self.assertEqual(set(self.aggregate.output_dimension_metrics), expected_domains)
        for domain, metrics in self.aggregate.output_dimension_metrics.items():
            with self.subTest(domain=domain):
                self.assertEqual(metrics["completeLabelCases"], self.aggregate.total)
                self.assertEqual(metrics["completeFailCases"], 0)
                self.assertEqual(metrics["unlabeledObservedOutputs"], 0)
        for rule_id, coverage in self.aggregate.rule_coverage.items():
            with self.subTest(rule=rule_id):
                self.assertEqual(coverage["positiveCases"], coverage["positivePassed"])
                self.assertEqual(coverage["negativeCases"], coverage["negativePassed"])

    def test_complete_labels_reject_an_extra_observed_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = json.loads((FORMAL_DIR / "corpus.json").read_text())
            (root / "corpus.json").write_text(json.dumps(manifest), encoding="utf-8")
            source = json.loads((FORMAL_DIR / "formal_a17_no_focus_binder.json").read_text())
            source["expected"]["outputLabels"]["citations"]["allowedIds"] = ["event_log", "trace"]
            (root / "case.json").write_text(json.dumps(source), encoding="utf-8")
            aggregate = run_formal_eval_directory(root)

        dimension = aggregate.cases[0].output_dimensions["citations"]
        self.assertFalse(aggregate.cases[0].passed)
        self.assertEqual(dimension.unexpected_observed_ids, ["logcat"])
        self.assertFalse(dimension.passed)
        self.assertIn("labelled_output_expectations_failed:citations", aggregate.gate_failures)

    def test_partial_labels_record_unlabelled_outputs_without_counting_them_as_correct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = json.loads((FORMAL_DIR / "corpus.json").read_text())
            manifest["labelingScope"]["fullyLabeledOutputDomains"].remove("citations")
            manifest["labelingScope"]["notFullyLabeledOutputDomains"].append("citations")
            (root / "corpus.json").write_text(json.dumps(manifest), encoding="utf-8")
            source = json.loads((FORMAL_DIR / "formal_a17_no_focus_binder.json").read_text())
            labels = source["expected"]["outputLabels"]["citations"]
            labels["coverage"] = "partial"
            labels["requiredIds"] = ["event_log", "trace"]
            labels["forbiddenIds"] = []
            labels["allowedIds"] = []
            (root / "case.json").write_text(json.dumps(source), encoding="utf-8")
            aggregate = run_formal_eval_directory(root)

        dimension = aggregate.cases[0].output_dimensions["citations"]
        self.assertIsNone(dimension.passed)
        self.assertEqual(dimension.unlabeled_observed_ids, ["logcat"])
        self.assertEqual(aggregate.output_dimension_metrics["citations"]["unlabeledObservedOutputs"], 1)
        self.assertIn("output_domain_not_fully_labeled:citations", aggregate.gate_failures)

    def test_rule_coverage_requires_a_real_negative_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = json.loads((FORMAL_DIR / "corpus.json").read_text())
            manifest["ruleCoverage"]["main_thread_binder_reply_wait"]["negativeCaseIds"] = ["missing-case"]
            (root / "corpus.json").write_text(json.dumps(manifest), encoding="utf-8")
            shutil.copy(FORMAL_DIR / "formal_a17_no_focus_binder.json", root / "case.json")
            aggregate = run_formal_eval_directory(root)

        self.assertIn(
            "rule_coverage_unknown_negative_case:main_thread_binder_reply_wait:missing-case",
            aggregate.gate_failures,
        )

    @staticmethod
    def _productionize(case: dict[str, object]) -> None:
        corpus = case["corpus"]
        assert isinstance(corpus, dict)
        corpus.update({
            "sourceClass": "licensed_deidentified",
            "sourceLicense": "internal licensed/deidentified evaluation agreement",
            "deidentificationStatus": "verified",
            "sourceLicenseRecord": {
                "recordId": "license-record-001",
                "approvedBy": "privacy-reviewer-001",
                "evidenceSha256": "a" * 64,
            },
            "deidentificationValidation": {
                "rulesetVersion": "anr-redaction-v1",
                "validationId": "redaction-validation-001",
                "validatorId": "privacy-validator-001",
                "viewCommitment": "b" * 64,
            },
            "manualAnnotation": {
                "status": "complete",
                "labelSetRevision": "labels-r1",
                "annotatorIds": ["annotator-001"],
                "reviewerIds": ["reviewer-001"],
                "labeledOutputDomains": [
                    "trigger", "blocker", "evidence_status", "citations",
                    "contradictions", "high_confidence_violations",
                ],
                "missingOutputDomains": [],
                "unsupportedOutputDomains": [],
            },
        })

    @staticmethod
    def _write_single_case(root: Path, manifest: dict[str, object], case: dict[str, object]) -> None:
        case_path = root / "case.json"
        case_path.write_text(json.dumps(case), encoding="utf-8")
        manifest["caseSha256"] = {"case.json": hashlib.sha256(case_path.read_bytes()).hexdigest()}
        (root / "corpus.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_acceptance_metrics_measure_deterministic_gates_and_leave_model_goals_na(self) -> None:
        metrics = self.aggregate.acceptance_metrics
        self.assertEqual(metrics["citationIntegrity"], {
            "status": "measured",
            "supportedCases": 2,
            "completeCases": 2,
            "integrityRate": 1.0,
            "targetMinimum": 1.0,
        })
        self.assertEqual(metrics["highConfidenceViolations"]["status"], "measured")
        self.assertEqual(metrics["highConfidenceViolations"]["violationCount"], 0)
        self.assertEqual(metrics["adversarialTriggerFalsePositives"]["negativeCases"], 2)
        self.assertEqual(metrics["adversarialTriggerFalsePositives"]["falsePositiveCases"], 0)
        self.assertEqual(metrics["modelQualityGoals"]["macroF1"]["status"], "N/A")
        self.assertEqual(metrics["modelQualityGoals"]["top3Accuracy"]["status"], "N/A")

    def test_acceptance_blocks_a_high_confidence_contract_violation(self) -> None:
        with patch(
            "anr_evidence.formal_eval._collect_high_confidence_violation_ids",
            return_value=["input_lifecycle.unexpected_high_confidence_eligibility"],
        ):
            aggregate = run_formal_eval_directory(FORMAL_DIR)

        metric = aggregate.acceptance_metrics["highConfidenceViolations"]
        self.assertEqual(metric["violationCount"], 2)
        self.assertIn("input_lifecycle.unexpected_high_confidence_eligibility", metric["violationIds"])
        self.assertIn("high_confidence_violations_above_max:2>0", aggregate.gate_failures)

    def test_acceptance_blocks_adversarial_trigger_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            shutil.copytree(FORMAL_DIR, root)
            manifest_path = root / "corpus.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["ruleCoverage"]["trigger:input.no_focused_window"]["negativeCaseIds"] = [
                "formal-a17-no-focus-binder"
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            aggregate = run_formal_eval_directory(root)

        metric = aggregate.acceptance_metrics["adversarialTriggerFalsePositives"]
        self.assertEqual(metric["falsePositiveCases"], 1)
        self.assertEqual(
            metric["rules"]["trigger:input.no_focused_window"]["falsePositiveCaseIds"],
            ["formal-a17-no-focus-binder"],
        )
        self.assertIn("adversarial_trigger_false_positives:1>0", aggregate.gate_failures)

    def test_acceptance_reports_na_and_blocks_required_measurement_without_evaluation_samples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            shutil.copytree(FORMAL_DIR, root)
            paths = sorted(root.glob("formal_*.json"))
            for path in paths:
                case = json.loads(path.read_text())
                case["corpus"]["split"] = "development"
                path.write_text(json.dumps(case), encoding="utf-8")
            manifest_path = root / "corpus.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["caseSha256"] = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in paths
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            aggregate = run_formal_eval_directory(root)

        metrics = aggregate.acceptance_metrics
        self.assertEqual(metrics["citationIntegrity"]["status"], "N/A")
        self.assertEqual(metrics["highConfidenceViolations"]["status"], "N/A")
        self.assertEqual(metrics["adversarialTriggerFalsePositives"]["status"], "N/A")
        self.assertIn("acceptance_measurement_not_available:citation_integrity", aggregate.gate_failures)
        self.assertIn("acceptance_measurement_not_available:high_confidence_violations", aggregate.gate_failures)
        self.assertIn("acceptance_measurement_not_available:adversarial_trigger_false_positives", aggregate.gate_failures)

    def test_governance_reports_synthetic_support_without_promoting_it_to_production(self) -> None:
        governance = self.aggregate.corpus_governance
        self.assertEqual(governance["sampleCountsBySplit"], {"evaluation": 2})
        self.assertEqual(governance["sampleCountsBySourceClassAndSplit"]["evaluation"], {"synthetic": 2})
        self.assertFalse(governance["splitLeakage"]["incidentSourceId"])
        self.assertFalse(governance["splitLeakage"]["deviceSourceId"])
        self.assertTrue(all(
            item["observed"] >= item["minimum"]
            for item in governance["supportByClass"].values()
        ))
        self.assertEqual(governance["statisticalThresholds"]["status"], "disabled_by_manifest")
        self.assertIn("non_production_source_classes:synthetic", self.aggregate.gate_failures)

    def test_production_case_requires_licence_record_and_redaction_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = json.loads((FORMAL_DIR / "corpus.json").read_text())
            source = json.loads((FORMAL_DIR / "formal_a17_no_focus_binder.json").read_text())
            self._productionize(source)
            corpus = source["corpus"]
            assert isinstance(corpus, dict)
            corpus.pop("sourceLicenseRecord")
            corpus.pop("deidentificationValidation")
            self._write_single_case(root, manifest, source)
            aggregate = run_formal_eval_directory(root)

        errors = aggregate.cases[0].fixture_errors
        self.assertIn("licensed_deidentified cases require corpus.sourceLicenseRecord", errors)
        self.assertIn("licensed_deidentified cases require corpus.deidentificationValidation", errors)

    def test_production_case_rejects_unverified_redaction_ruleset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = json.loads((FORMAL_DIR / "corpus.json").read_text())
            source = json.loads((FORMAL_DIR / "formal_a17_no_focus_binder.json").read_text())
            self._productionize(source)
            validation = source["corpus"]["deidentificationValidation"]
            assert isinstance(validation, dict)
            validation["rulesetVersion"] = "unverified-redaction-ruleset"
            self._write_single_case(root, manifest, source)
            aggregate = run_formal_eval_directory(root)

        self.assertIn(
            "corpus.deidentificationValidation.rulesetVersion must equal anr-redaction-v1",
            aggregate.cases[0].fixture_errors,
        )

    def test_production_case_requires_complete_human_label_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = json.loads((FORMAL_DIR / "corpus.json").read_text())
            source = json.loads((FORMAL_DIR / "formal_a17_no_focus_binder.json").read_text())
            self._productionize(source)
            annotation = source["corpus"]["manualAnnotation"]
            assert isinstance(annotation, dict)
            annotation["labeledOutputDomains"].remove("citations")
            annotation["unsupportedOutputDomains"] = ["blocker"]
            self._write_single_case(root, manifest, source)
            aggregate = run_formal_eval_directory(root)

        errors = aggregate.cases[0].fixture_errors
        self.assertTrue(any("manual labels missing required domains" in error for error in errors))
        self.assertTrue(any("manual labels mark required domains incomplete" in error for error in errors))
        governance = aggregate.corpus_governance
        self.assertEqual(governance["unsupportedManualLabels"], [{
            "caseId": "formal-a17-no-focus-binder", "domains": ["blocker"],
        }])

    def test_development_and_evaluation_source_overlap_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            shutil.copytree(FORMAL_DIR, root)
            binder_path = root / "formal_a17_no_focus_binder.json"
            render_path = root / "formal_a17_unresponsive_render.json"
            binder = json.loads(binder_path.read_text())
            render = json.loads(render_path.read_text())
            binder["corpus"]["split"] = "development"
            binder["corpus"]["sampleIdentity"]["incidentSourceId"] = render["corpus"]["sampleIdentity"]["incidentSourceId"]
            binder_path.write_text(json.dumps(binder), encoding="utf-8")
            manifest_path = root / "corpus.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["caseSha256"] = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (binder_path, render_path)
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            aggregate = run_formal_eval_directory(root)

        self.assertIn(
            "development_evaluation_leakage:incidentSourceId:synthetic-incident-window-render",
            aggregate.gate_failures,
        )
        self.assertEqual(
            aggregate.corpus_governance["splitLeakage"]["incidentSourceId"],
            ["synthetic-incident-window-render"],
        )

    def test_development_cases_do_not_fill_evaluation_coverage_or_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            shutil.copytree(FORMAL_DIR, root)
            binder_path = root / "formal_a17_no_focus_binder.json"
            render_path = root / "formal_a17_unresponsive_render.json"
            binder = json.loads(binder_path.read_text())
            render = json.loads(render_path.read_text())
            self._productionize(binder)
            render["corpus"]["split"] = "development"
            binder_path.write_text(json.dumps(binder), encoding="utf-8")
            render_path.write_text(json.dumps(render), encoding="utf-8")
            manifest_path = root / "corpus.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["caseSha256"] = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (binder_path, render_path)
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            aggregate = run_formal_eval_directory(root)

        self.assertEqual(aggregate.source_class_counts, {"licensed_deidentified": 1})
        self.assertEqual(aggregate.release_coverage["17"], 1)
        self.assertEqual(
            aggregate.corpus_governance["sampleCountsBySourceClassAndSplit"],
            {"development": {"synthetic": 1}, "evaluation": {"licensed_deidentified": 1}},
        )
        self.assertIn("insufficient_class_support:trigger:input.window_unresponsive:0<1", aggregate.gate_failures)
        self.assertNotIn("non_production_source_classes:synthetic", aggregate.gate_failures)

    def test_minimum_class_support_and_unimplemented_enabled_thresholds_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = json.loads((FORMAL_DIR / "corpus.json").read_text())
            manifest["realCorpusAdmission"]["minimumSupportByClass"]["trigger:input.no_focused_window"] = 2
            manifest["realCorpusAdmission"]["statisticalThresholdsEnabled"] = True
            source = json.loads((FORMAL_DIR / "formal_a17_no_focus_binder.json").read_text())
            self._write_single_case(root, manifest, source)
            aggregate = run_formal_eval_directory(root)

        self.assertIn("insufficient_class_support:trigger:input.no_focused_window:1<2", aggregate.gate_failures)
        self.assertIn("statistical_threshold_metrics_unsupported", aggregate.gate_failures)
        self.assertEqual(
            aggregate.corpus_governance["statisticalThresholds"]["status"],
            "blocked_prerequisites",
        )

    def test_missing_required_citation_source_fails_the_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(FORMAL_DIR / "corpus.json", root / "corpus.json")
            source = json.loads((FORMAL_DIR / "formal_a17_no_focus_binder.json").read_text())
            source["expected"]["requiredCitationSourceKinds"].append("kernel_log")
            citation_labels = source["expected"]["outputLabels"]["citations"]
            citation_labels["requiredIds"].append("kernel_log")
            citation_labels["allowedIds"].append("kernel_log")
            (root / "case.json").write_text(json.dumps(source), encoding="utf-8")

            aggregate = run_formal_eval_directory(root)

        self.assertEqual(aggregate.total, 1)
        self.assertFalse(aggregate.cases[0].passed)
        self.assertEqual(aggregate.cases[0].missing_citation_sources, ["kernel_log"])
        self.assertIn("case_expectations_failed", aggregate.gate_failures)
        self.assertEqual(aggregate.acceptance_metrics["citationIntegrity"]["integrityRate"], 0.0)
        self.assertIn("citation_integrity_below_target:0.000000<1.000000", aggregate.gate_failures)

    def test_frozen_manifest_detects_case_content_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            shutil.copytree(FORMAL_DIR, root)
            case_path = root / "formal_a17_no_focus_binder.json"
            case_path.write_text(case_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

            aggregate = run_formal_eval_directory(root)

        self.assertFalse(aggregate.qualified)
        self.assertIn("case_sha256_mismatch:formal_a17_no_focus_binder.json", aggregate.corpus_errors)

    def test_release_metadata_mismatch_is_rejected_before_evidence_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(FORMAL_DIR / "corpus.json", root / "corpus.json")
            source = json.loads((FORMAL_DIR / "formal_a17_no_focus_binder.json").read_text())
            source["corpus"]["androidRelease"] = 16
            (root / "case.json").write_text(json.dumps(source), encoding="utf-8")

            aggregate = run_formal_eval_directory(root)

        case = aggregate.cases[0]
        self.assertFalse(case.passed)
        self.assertIn("package.android_release must equal corpus.androidRelease", case.fixture_errors)
        self.assertEqual(case.verified_evidence_ref_count, 0)

    def test_resource_budget_overrun_fails_the_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = json.loads((FORMAL_DIR / "corpus.json").read_text())
            manifest["executionBudget"]["maxInputBytesPerCase"] = 1
            (root / "corpus.json").write_text(json.dumps(manifest), encoding="utf-8")
            shutil.copy(FORMAL_DIR / "formal_a17_no_focus_binder.json", root / "case.json")

            aggregate = run_formal_eval_directory(root)

        case = aggregate.cases[0]
        self.assertFalse(case.passed)
        self.assertTrue(any("maxInputBytesPerCase exceeded" in error for error in case.budget_errors))

    def test_fixture_schema_constants_are_versioned(self) -> None:
        manifest = json.loads((FORMAL_DIR / "corpus.json").read_text())
        case = json.loads((FORMAL_DIR / "formal_a17_no_focus_binder.json").read_text())
        self.assertEqual(manifest["schemaVersion"], FORMAL_CORPUS_SCHEMA_VERSION)
        self.assertEqual(case["schemaVersion"], FORMAL_CASE_SCHEMA_VERSION)
        for filename, digest in manifest["caseSha256"].items():
            self.assertEqual(
                digest,
                hashlib.sha256((FORMAL_DIR / filename).read_bytes()).hexdigest(),
            )


if __name__ == "__main__":
    unittest.main()
