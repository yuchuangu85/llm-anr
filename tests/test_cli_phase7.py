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
    generate_root_cause_report,
    load_package_from_fixture,
    normalize_evidence_package,
)

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}


class CliPhase7Tests(unittest.TestCase):
    def test_cli_remediate_from_fixture_input(self) -> None:
        completed = subprocess.run(
            [sys.executable, '-m', 'anr_evidence', '--remediate', 'tests/fixtures/nfw_01.json'],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=ENV,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase7-remediation-draft')
        self.assertFalse(payload['metadata']['finalAdvice'])

    def test_cli_remediate_from_phase6_input(self) -> None:
        phase6 = generate_root_cause_report(
            generate_causal_draft(
                analyze_normalized_package(
                    normalize_evidence_package(
                        extract_evidence_package(load_package_from_fixture(ROOT / 'tests/fixtures/idt_01.json'))
                    )
                )
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'phase6.json'
            path.write_text(json.dumps(phase6, ensure_ascii=False), encoding='utf-8')
            completed = subprocess.run(
                [sys.executable, '-m', 'anr_evidence', '--remediate', str(path)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env=ENV,
            )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase7-remediation-draft')

    def test_cli_rejects_phase6_input_without_remediate_flag(self) -> None:
        phase6 = generate_root_cause_report(
            generate_causal_draft(
                analyze_normalized_package(
                    normalize_evidence_package(
                        extract_evidence_package(load_package_from_fixture(ROOT / 'tests/fixtures/nfw_01.json'))
                    )
                )
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / 'phase6.json'
            path.write_text(json.dumps(phase6, ensure_ascii=False), encoding='utf-8')
            completed = subprocess.run(
                [sys.executable, '-m', 'anr_evidence', str(path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=ENV,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('Use --remediate, --deliver, or --report', completed.stderr or completed.stdout)


if __name__ == '__main__':
    unittest.main()
