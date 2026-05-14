from __future__ import annotations

import json
import unittest

from anr_evidence import (
    analyze_normalized_package,
    extract_evidence_package,
    generate_causal_draft,
    generate_remediation_drafts,
    generate_root_cause_report,
    normalize_evidence_package,
)
from tests.helpers import load_fixture

FIXTURES = [
    'nfw_01.json',
    'idt_01.json',
    'unk_01.json',
    'amb_01.json',
    'miss_trace_01.json',
    'miss_kernel_01.json',
    'clock_skew_01.json',
    'noisy_01.json',
]


class Phase7RemediationTests(unittest.TestCase):
    def test_all_phase6_fixtures_generate_remediation_drafts(self) -> None:
        for fixture_name in FIXTURES:
            with self.subTest(fixture=fixture_name):
                phase6 = generate_root_cause_report(
                    generate_causal_draft(
                        analyze_normalized_package(
                            normalize_evidence_package(
                                extract_evidence_package(load_fixture(fixture_name))
                            )
                        )
                    )
                )
                phase7 = generate_remediation_drafts(phase6)
                self.assertEqual(phase7['metadata']['phase'], 'phase7-remediation-draft')
                self.assertEqual(phase7['metadata']['schemaVersion'], 'phase7-remediation-v1')
                self.assertFalse(phase7['metadata']['finalAdvice'])
                self.assertEqual(phase7['classification'], phase6['classification'])
                self.assertEqual(phase7['anchors'], phase6['anchors'])
                self.assertIn('candidateChains', phase7)
                self.assertIn('candidateConclusions', phase7)
                self.assertIn('remediationDrafts', phase7)
                self.assertTrue(phase7['remediationDrafts'])
                for draft in phase7['remediationDrafts']:
                    for field in ('draftId', 'rank', 'priority', 'title', 'derivedFromConclusionId', 'derivedFromSignalCategory', 'actionDraft', 'supportingEvidenceRefs', 'supportingSnippets', 'whyGated', 'requiresHumanConfirmation'):
                        self.assertIn(field, draft)
                    self.assertTrue(draft['requiresHumanConfirmation'])

    def test_remediation_drafts_are_explicitly_gated(self) -> None:
        phase7 = generate_remediation_drafts(
            generate_root_cause_report(
                generate_causal_draft(
                    analyze_normalized_package(
                        normalize_evidence_package(
                            extract_evidence_package(load_fixture('nfw_01.json'))
                        )
                    )
                )
            )
        )
        why_gated = ' '.join(' '.join(d['whyGated']) for d in phase7['remediationDrafts'])
        self.assertIn('不是最终根因裁决', why_gated)
        self.assertIn('requiresHumanConfirmation', str(phase7['remediationDrafts'][0]))

    def test_remediation_drafts_are_deterministic(self) -> None:
        first = generate_remediation_drafts(
            generate_root_cause_report(
                generate_causal_draft(
                    analyze_normalized_package(
                        normalize_evidence_package(
                            extract_evidence_package(load_fixture('idt_01.json'))
                        )
                    )
                )
            )
        )
        second = generate_remediation_drafts(
            generate_root_cause_report(
                generate_causal_draft(
                    analyze_normalized_package(
                        normalize_evidence_package(
                            extract_evidence_package(load_fixture('idt_01.json'))
                        )
                    )
                )
            )
        )
        self.assertEqual(json.dumps(first, sort_keys=True, ensure_ascii=False), json.dumps(second, sort_keys=True, ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
