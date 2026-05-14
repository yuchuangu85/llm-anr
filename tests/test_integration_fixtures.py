from __future__ import annotations

import unittest

from anr_evidence import extract_evidence_package
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


class IntegrationFixtureTests(unittest.TestCase):
    def test_all_fixtures_meet_expected_contract(self) -> None:
        for fixture_name in FIXTURES:
            with self.subTest(fixture=fixture_name):
                fixture = load_fixture(fixture_name)
                expected = fixture['expected']
                result = extract_evidence_package(fixture)

                self.assertEqual(result['metadata']['status'], expected['status'])
                self.assertEqual(result['classification']['detectedType'], expected['detected_type'])
                self.assertEqual(result['classification']['supported'], expected['supported'])
                self.assertEqual(result['classification']['fallbackMode'], expected['fallback_mode'])
                self.assertEqual(result['anchors']['primaryAnchor']['sourceKind'], expected['primary_anchor_source'])

                for top_level_key in ('metadata', 'classification', 'anchors', 'sources', 'evidence', 'warnings'):
                    self.assertIn(top_level_key, result)

                p0_ids = {item['id'] for item in result['evidence'] if item['tier'] == 'P0'}
                self.assertTrue(set(expected['required_p0_ids']).issubset(p0_ids))

                warning_codes = {warning['code'] for warning in result['warnings']}
                for warning_code in expected['required_warning_codes']:
                    self.assertIn(warning_code, warning_codes)

                for item in result['evidence']:
                    provenance = item['provenance']
                    for key in ('sourceKind', 'sourcePath', 'extractionRule', 'timeWindow', 'anchorUsed', 'tier', 'extractionMode', 'warningFlags'):
                        self.assertIn(key, provenance)
                    if item['id'] in {'event_am_anr', 'event_pre_window', 'logcat_anchor_window', 'kernel_anchor_window'}:
                        self.assertIsNotNone(provenance['anchorUsed'])

    def test_noisy_fixture_preserves_recall(self) -> None:
        result = extract_evidence_package(load_fixture('noisy_01.json'))
        p0_ids = {item['id'] for item in result['evidence'] if item['tier'] == 'P0'}
        self.assertTrue({'trace_core', 'event_am_anr', 'event_pre_window', 'logcat_anchor_window', 'kernel_anchor_window'}.issubset(p0_ids))
        self.assertGreaterEqual(len(result['evidence']), 5)


if __name__ == '__main__':
    unittest.main()
