from __future__ import annotations

import json
import unittest

from anr_evidence import (
    analyze_normalized_package,
    extract_evidence_package,
    generate_causal_draft,
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


class Phase5HypothesisTests(unittest.TestCase):
    def test_all_phase3_fixtures_generate_causal_drafts(self) -> None:
        for fixture_name in FIXTURES:
            with self.subTest(fixture=fixture_name):
                phase3 = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture(fixture_name))))
                phase5 = generate_causal_draft(phase3)
                self.assertEqual(phase5['metadata']['phase'], 'phase5-causal-draft')
                self.assertEqual(phase5['metadata']['schemaVersion'], 'phase5-causal-draft-v1')
                self.assertEqual(phase5['metadata']['status'], phase3['metadata']['status'])
                self.assertEqual(phase5['classification'], phase3['classification'])
                self.assertEqual(phase5['anchors'], phase3['anchors'])
                self.assertIn('candidateChains', phase5)
                self.assertTrue(phase5['candidateChains'])
                for chain in phase5['candidateChains']:
                    for field in ('chainId', 'title', 'rank', 'score', 'confidenceLevel', 'signalCategory', 'evidenceRefs', 'evidenceSnippets', 'rationale', 'limitations', 'notRootCauseYet'):
                        self.assertIn(field, chain)
                    self.assertTrue(chain['notRootCauseYet'])
                    self.assertIsInstance(chain['evidenceSnippets'], list)

    def test_trace_dominant_block_hint_is_carried_into_candidate_chains(self) -> None:
        phase5 = generate_causal_draft(
            analyze_normalized_package(
                normalize_evidence_package(
                    extract_evidence_package(load_fixture('nfw_01.json'))
                )
            )
        )
        first_chain = phase5['candidateChains'][0]
        self.assertEqual(first_chain['traceDominantBlockHint'], 'focus_window_wait')
        self.assertGreaterEqual(first_chain['traceSuspiciousThreadTotal'], 1)
        self.assertIn('focus_window_wait', first_chain['rationale'])
        self.assertIn('focus_window_wait', ' '.join(first_chain['limitations']))

    def test_hypothesis_is_explicitly_non_final(self) -> None:
        phase5 = generate_causal_draft(analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('nfw_01.json')))))
        self.assertTrue(all(chain['notRootCauseYet'] for chain in phase5['candidateChains']))
        limitations = ' '.join(' '.join(chain['limitations']) for chain in phase5['candidateChains'])
        self.assertIn('不是最终根因结论', limitations)

    def test_hypothesis_contains_readable_snippet_summaries(self) -> None:
        phase5 = generate_causal_draft(
            analyze_normalized_package(
                normalize_evidence_package(
                    extract_evidence_package(load_fixture('nfw_01.json'))
                )
            )
        )
        first_chain = phase5['candidateChains'][0]
        self.assertTrue(first_chain['evidenceSnippets'])
        first_snippet = first_chain['evidenceSnippets'][0]
        for field in ('rank', 'score', 'recordRef', 'timestamp', 'sourceKind', 'recordType', 'summary'):
            self.assertIn(field, first_snippet)

    def test_chain_and_snippet_ordering_are_deterministic(self) -> None:
        phase5 = generate_causal_draft(
            analyze_normalized_package(
                normalize_evidence_package(
                    extract_evidence_package(load_fixture('nfw_01.json'))
                )
            )
        )
        ranks = [chain['rank'] for chain in phase5['candidateChains']]
        self.assertEqual(ranks, sorted(ranks))
        scores = [chain['score'] for chain in phase5['candidateChains']]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for chain in phase5['candidateChains']:
            snippet_ranks = [snippet['rank'] for snippet in chain['evidenceSnippets']]
            snippet_scores = [snippet['score'] for snippet in chain['evidenceSnippets']]
            self.assertEqual(snippet_ranks, sorted(snippet_ranks))
            self.assertEqual(snippet_scores, sorted(snippet_scores, reverse=True))

    def test_fallback_cases_stay_low_confidence(self) -> None:
        phase5 = generate_causal_draft(analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('amb_01.json')))))
        self.assertTrue(all(chain['confidenceLevel'] == 'low' for chain in phase5['candidateChains']))

    def test_hypothesis_generation_is_deterministic(self) -> None:
        first = generate_causal_draft(analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('idt_01.json')))))
        second = generate_causal_draft(analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_fixture('idt_01.json')))))
        self.assertEqual(
            json.dumps(first, sort_keys=True, ensure_ascii=False),
            json.dumps(second, sort_keys=True, ensure_ascii=False),
        )


if __name__ == '__main__':
    unittest.main()
