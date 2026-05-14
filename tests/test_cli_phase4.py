from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from anr_evidence import (
    analyze_normalized_package,
    extract_evidence_package,
    load_package_from_fixture,
    normalize_evidence_package,
)

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


class CliPhase4Tests(unittest.TestCase):
    def test_cli_report_from_fixture_input(self) -> None:
        completed = subprocess.run(
            [sys.executable, '-m', 'anr_evidence', '--report', 'tests/fixtures/nfw_01.json'],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=ENV,
        )
        self.assertIn('# ANR 辅助分析报告草稿', completed.stdout)
        self.assertIn('## 6. 保守版候选结论', completed.stdout)
        self.assertIn('## 7. 候选因果链草稿', completed.stdout)
        self.assertIn('## 8. 修复建议草稿（需人工确认）', completed.stdout)
        self.assertIn('## 9. 证据时间线', completed.stdout)

    def test_cli_report_from_phase3_input(self) -> None:
        phase3 = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_package_from_fixture(ROOT / 'tests/fixtures/idt_01.json'))))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'phase3.json'
            path.write_text(json.dumps(phase3, ensure_ascii=False), encoding='utf-8')
            completed = subprocess.run(
                [sys.executable, '-m', 'anr_evidence', '--report', str(path)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env=ENV,
            )
        self.assertIn('# ANR 辅助分析报告草稿', completed.stdout)
        self.assertIn('input_dispatching_timeout', completed.stdout)
        self.assertIn('Top Conclusion', completed.stdout)

    def test_cli_rejects_phase3_input_without_report_flag(self) -> None:
        phase3 = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_package_from_fixture(ROOT / 'tests/fixtures/nfw_01.json'))))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'phase3.json'
            path.write_text(json.dumps(phase3, ensure_ascii=False), encoding='utf-8')
            completed = subprocess.run(
                [sys.executable, '-m', 'anr_evidence', str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=ENV,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('Use --hypothesize, --root-cause, --remediate, --deliver, or --report', completed.stderr or completed.stdout)

if __name__ == '__main__':
    unittest.main()
