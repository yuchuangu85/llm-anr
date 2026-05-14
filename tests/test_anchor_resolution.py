from __future__ import annotations

import unittest

from anr_evidence.extractor import collect_anchor_candidates, resolve_anchor
from tests.helpers import load_fixture


class AnchorResolutionTests(unittest.TestCase):
    def test_event_anchor_has_highest_precedence(self) -> None:
        fixture = load_fixture('clock_skew_01.json')
        candidates = collect_anchor_candidates(fixture)
        resolved = resolve_anchor(candidates)
        self.assertEqual(resolved['primary_anchor']['sourceKind'], 'event_log')
        warning_codes = {warning['code'] for warning in resolved['warnings']}
        self.assertIn('anchor-mismatch', warning_codes)

    def test_trace_beats_logcat_and_kernel_when_event_missing(self) -> None:
        fixture = {
            'package_id': 'TRACE-FIRST',
            'sources': {
                'trace': {'path': 'trace.txt', 'content': '04-12 18:00:03.000 main tid=1 ANR marker'},
                'logcat': {'path': 'logcat.txt', 'content': '04-12 18:00:04.000 E InputDispatcher Input dispatching timed out'},
                'kernel_log': {'path': 'kernel.txt', 'content': '04-12 18:00:05.000 binder: backlog'},
            },
        }
        candidates = collect_anchor_candidates(fixture)
        resolved = resolve_anchor(candidates)
        self.assertEqual(resolved['primary_anchor']['sourceKind'], 'trace')


if __name__ == '__main__':
    unittest.main()
