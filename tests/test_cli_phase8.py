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
    generate_causal_draft,
    generate_remediation_drafts,
    generate_root_cause_report,
    load_package_from_fixture,
    normalize_evidence_package,
)

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}


class CliPhase8Tests(unittest.TestCase):
    def test_cli_deliver_from_fixture_input(self) -> None:
        completed = subprocess.run(
            [sys.executable, '-m', 'anr_evidence', '--deliver', 'tests/fixtures/nfw_01.json'],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=ENV,
        )
        self.assertIn('# ANR 分析交付稿', completed.stdout)
        self.assertIn('## 五、主线程关键信息', completed.stdout)
        self.assertIn('## 六、修复建议草稿（需人工确认）', completed.stdout)

    def test_cli_deliver_from_phase7_input(self) -> None:
        phase7 = generate_remediation_drafts(
            generate_root_cause_report(
                generate_causal_draft(
                    analyze_normalized_package(
                        normalize_evidence_package(
                            extract_evidence_package(load_package_from_fixture(ROOT / 'tests/fixtures/idt_01.json'))
                        )
                    )
                )
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'phase7.json'
            path.write_text(json.dumps(phase7, ensure_ascii=False), encoding='utf-8')
            completed = subprocess.run(
                [sys.executable, '-m', 'anr_evidence', '--deliver', str(path)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env=ENV,
            )
        self.assertIn('# ANR 分析交付稿', completed.stdout)
        self.assertIn('输入分发', completed.stdout)

    def test_cli_rejects_phase7_input_without_deliver_flag(self) -> None:
        phase7 = generate_remediation_drafts(
            generate_root_cause_report(
                generate_causal_draft(
                    analyze_normalized_package(
                        normalize_evidence_package(
                            extract_evidence_package(load_package_from_fixture(ROOT / 'tests/fixtures/nfw_01.json'))
                        )
                    )
                )
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'phase7.json'
            path.write_text(json.dumps(phase7, ensure_ascii=False), encoding='utf-8')
            completed = subprocess.run(
                [sys.executable, '-m', 'anr_evidence', str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=ENV,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('Use --deliver or --report', completed.stderr or completed.stdout)


if __name__ == '__main__':
    unittest.main()
