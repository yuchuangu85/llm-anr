from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

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

    def test_missing_required_citation_source_fails_the_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(FORMAL_DIR / "corpus.json", root / "corpus.json")
            source = json.loads((FORMAL_DIR / "formal_a17_no_focus_binder.json").read_text())
            source["expected"]["requiredCitationSourceKinds"].append("kernel_log")
            (root / "case.json").write_text(json.dumps(source), encoding="utf-8")

            aggregate = run_formal_eval_directory(root)

        self.assertEqual(aggregate.total, 1)
        self.assertFalse(aggregate.cases[0].passed)
        self.assertEqual(aggregate.cases[0].missing_citation_sources, ["kernel_log"])
        self.assertIn("case_expectations_failed", aggregate.gate_failures)

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
