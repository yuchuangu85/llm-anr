from __future__ import annotations

import unittest

from anr_evidence import extract_baseline_package, extract_evidence_package
from tests.helpers import load_fixture


class TemplateAdditiveTests(unittest.TestCase):
    def test_templates_never_remove_baseline_p0(self) -> None:
        for fixture_name in ('nfw_01.json', 'idt_01.json'):
            with self.subTest(fixture=fixture_name):
                fixture = load_fixture(fixture_name)
                baseline = extract_baseline_package(fixture)
                enriched = extract_evidence_package(fixture)
                baseline_p0 = {item['id'] for item in baseline['evidence'] if item['tier'] == 'P0'}
                enriched_p0 = {item['id'] for item in enriched['evidence'] if item['tier'] == 'P0'}
                self.assertTrue(baseline_p0.issubset(enriched_p0))


if __name__ == '__main__':
    unittest.main()
