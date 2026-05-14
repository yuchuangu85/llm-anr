from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from anr_evidence import extract_evidence_package, load_package_from_fixture, normalize_evidence_package

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


class CliPhase3Tests(unittest.TestCase):
    def test_cli_analyze_from_fixture_input(self) -> None:
        completed = subprocess.run(
            [sys.executable, '-m', 'anr_evidence', '--analyze', 'tests/fixtures/nfw_01.json'],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=ENV,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase3-assisted-analysis')
        self.assertEqual(payload['classification']['detectedType'], 'no_focus_window')

    def test_cli_analyze_from_phase2_input(self) -> None:
        phase2 = normalize_evidence_package(extract_evidence_package(load_package_from_fixture(ROOT / 'tests/fixtures/idt_01.json')))
        with tempfile.TemporaryDirectory() as tmpdir:
            phase2_path = Path(tmpdir) / 'phase2.json'
            phase2_path.write_text(json.dumps(phase2, ensure_ascii=False), encoding='utf-8')
            completed = subprocess.run(
                [sys.executable, '-m', 'anr_evidence', '--analyze', str(phase2_path)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env=ENV,
            )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase3-assisted-analysis')
        self.assertEqual(payload['classification']['detectedType'], 'input_dispatching_timeout')

    def test_cli_rejects_phase2_input_without_analyze_flag(self) -> None:
        phase2 = normalize_evidence_package(extract_evidence_package(load_package_from_fixture(ROOT / 'tests/fixtures/nfw_01.json')))
        with tempfile.TemporaryDirectory() as tmpdir:
            phase2_path = Path(tmpdir) / 'phase2.json'
            phase2_path.write_text(json.dumps(phase2, ensure_ascii=False), encoding='utf-8')
            completed = subprocess.run(
                [sys.executable, '-m', 'anr_evidence', str(phase2_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=ENV,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('Use --analyze', completed.stderr or completed.stdout)


if __name__ == '__main__':
    unittest.main()
