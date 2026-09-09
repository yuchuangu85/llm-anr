from __future__ import annotations

import json
import tempfile
import unittest

from anr_evidence import (
    analyze_normalized_package,
    extract_evidence_package,
    load_package_from_fixture,
    normalize_evidence_package,
)
from tests.helpers import REPO_ROOT, run_cli, write_json_fixture


class CliPhase4Tests(unittest.TestCase):
    def test_cli_report_from_fixture_input(self) -> None:
        completed = run_cli('--report', 'tests/fixtures/nfw_01.json')
        self.assertIn('# ANR 辅助分析报告草稿', completed.stdout)
        self.assertIn('## 6. 保守版候选结论', completed.stdout)
        self.assertIn('## 7. 候选因果链草稿', completed.stdout)
        self.assertIn('## 8. 修复建议草稿（需人工确认）', completed.stdout)
        self.assertIn('## 9. 证据时间线', completed.stdout)

    def test_cli_report_from_phase3_input(self) -> None:
        phase3 = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_package_from_fixture(REPO_ROOT / 'tests/fixtures/idt_01.json'))))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_json_fixture(tmpdir, 'phase3.json', phase3)
            completed = run_cli('--report', str(path))
        self.assertIn('# ANR 辅助分析报告草稿', completed.stdout)
        self.assertIn('input_dispatching_timeout', completed.stdout)
        self.assertIn('Top Conclusion', completed.stdout)

    def test_cli_rejects_phase3_input_without_report_flag(self) -> None:
        phase3 = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_package_from_fixture(REPO_ROOT / 'tests/fixtures/nfw_01.json'))))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_json_fixture(tmpdir, 'phase3.json', phase3)
            completed = run_cli(str(path), check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('Use --hypothesize, --root-cause, --remediate, --deliver, or --report', completed.stderr or completed.stdout)

if __name__ == '__main__':
    unittest.main()
