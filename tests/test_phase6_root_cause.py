from __future__ import annotations

import json
import unittest

from anr_evidence import (
    analyze_normalized_package,
    extract_evidence_package,
    generate_causal_draft,
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


class Phase6RootCauseTests(unittest.TestCase):
    def test_all_phase5_fixtures_generate_root_cause_reports(self) -> None:
        for fixture_name in FIXTURES:
            with self.subTest(fixture=fixture_name):
                phase5 = generate_causal_draft(
                    analyze_normalized_package(
                        normalize_evidence_package(
                            extract_evidence_package(load_fixture(fixture_name))
                        )
                    )
                )
                phase6 = generate_root_cause_report(phase5)
                self.assertEqual(phase6['metadata']['phase'], 'phase6-root-cause-report-v1')
                self.assertEqual(phase6['metadata']['schemaVersion'], 'phase6-root-cause-v1')
                self.assertFalse(phase6['metadata']['finalJudgment'])
                self.assertEqual(phase6['classification'], phase5['classification'])
                self.assertEqual(phase6['anchors'], phase5['anchors'])
                self.assertIn('topConclusion', phase6)
                self.assertIn('candidateConclusions', phase6)
                self.assertTrue(phase6['candidateConclusions'])
                for conclusion in phase6['candidateConclusions']:
                    for field in ('conclusionId', 'rank', 'score', 'confidenceLevel', 'statement', 'signalCategory', 'supportingEvidenceRefs', 'supportingSnippets', 'whyNotFinal', 'unresolvedQuestions', 'tentative'):
                        self.assertIn(field, conclusion)
                    self.assertTrue(conclusion['tentative'])

    def test_root_cause_conclusion_carries_trace_dominant_block_hint_context(self) -> None:
        phase6 = generate_root_cause_report(
            generate_causal_draft(
                analyze_normalized_package(
                    normalize_evidence_package(
                        extract_evidence_package(load_fixture('nfw_01.json'))
                    )
                )
            )
        )
        self.assertEqual(phase6['topConclusion']['traceDominantBlockHint'], 'focus_window_wait')
        self.assertIn('focus_window_wait', phase6['topConclusion']['statement'])
        self.assertIn('focus_window_wait', ' '.join(phase6['topConclusion']['whyNotFinal']))
        self.assertIn('focus_window_wait', ' '.join(phase6['topConclusion']['unresolvedQuestions']))

    def test_root_cause_report_stays_conservative(self) -> None:
        phase6 = generate_root_cause_report(
            generate_causal_draft(
                analyze_normalized_package(
                    normalize_evidence_package(
                        extract_evidence_package(load_fixture('nfw_01.json'))
                    )
                )
            )
        )
        self.assertFalse(phase6['metadata']['finalJudgment'])
        self.assertEqual(phase6['metadata']['conclusionMode'], 'conservative')
        why_not_final = ' '.join(phase6['topConclusion']['whyNotFinal'])
        self.assertIn('不是最终根因裁决', why_not_final)

    def test_fallback_cases_produce_low_confidence_top_conclusion(self) -> None:
        phase6 = generate_root_cause_report(
            generate_causal_draft(
                analyze_normalized_package(
                    normalize_evidence_package(
                        extract_evidence_package(load_fixture('amb_01.json'))
                    )
                )
            )
        )
        self.assertEqual(phase6['topConclusion']['confidenceLevel'], 'low')

    def test_root_cause_report_is_deterministic(self) -> None:
        first = generate_root_cause_report(
            generate_causal_draft(
                analyze_normalized_package(
                    normalize_evidence_package(
                        extract_evidence_package(load_fixture('idt_01.json'))
                    )
                )
            )
        )
        second = generate_root_cause_report(
            generate_causal_draft(
                analyze_normalized_package(
                    normalize_evidence_package(
                        extract_evidence_package(load_fixture('idt_01.json'))
                    )
                )
            )
        )
        self.assertEqual(
            json.dumps(first, sort_keys=True, ensure_ascii=False),
            json.dumps(second, sort_keys=True, ensure_ascii=False),
        )


if __name__ == '__main__':
    unittest.main()
