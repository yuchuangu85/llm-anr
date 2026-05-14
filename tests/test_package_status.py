from __future__ import annotations

import unittest

from anr_evidence import extract_evidence_package
from tests.helpers import load_fixture


class PackageStatusTests(unittest.TestCase):
    def test_complete_when_supported_and_all_sources_present(self) -> None:
        result = extract_evidence_package(load_fixture('nfw_01.json'))
        self.assertEqual(result['metadata']['status'], 'complete')

    def test_partial_when_supported_and_source_missing(self) -> None:
        result = extract_evidence_package(load_fixture('miss_trace_01.json'))
        self.assertEqual(result['metadata']['status'], 'partial')

    def test_degraded_when_type_unknown(self) -> None:
        result = extract_evidence_package(load_fixture('unk_01.json'))
        self.assertEqual(result['metadata']['status'], 'degraded')

    def test_degraded_when_source_is_unreadable(self) -> None:
        fixture = load_fixture('nfw_01.json')
        fixture['sources']['trace']['readable'] = False
        result = extract_evidence_package(fixture)
        self.assertEqual(result['metadata']['status'], 'degraded')


if __name__ == '__main__':
    unittest.main()
