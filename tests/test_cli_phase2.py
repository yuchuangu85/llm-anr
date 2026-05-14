from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from anr_evidence import extract_evidence_package, load_package_from_fixture

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


class CliPhase2Tests(unittest.TestCase):
    def test_cli_normalize_from_fixture_input(self) -> None:
        completed = subprocess.run(
            [sys.executable, '-m', 'anr_evidence', '--normalize', 'tests/fixtures/nfw_01.json'],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=ENV,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase2-evidence-normalization')
        self.assertEqual(payload['classification']['detectedType'], 'no_focus_window')

    def test_cli_normalize_from_phase1_package_input(self) -> None:
        phase1 = extract_evidence_package(load_package_from_fixture(ROOT / 'tests/fixtures/idt_01.json'))
        with tempfile.TemporaryDirectory() as tmpdir:
            phase1_path = Path(tmpdir) / 'phase1.json'
            phase1_path.write_text(json.dumps(phase1, ensure_ascii=False), encoding='utf-8')
            completed = subprocess.run(
                [sys.executable, '-m', 'anr_evidence', '--normalize', str(phase1_path)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env=ENV,
            )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase2-evidence-normalization')
        self.assertEqual(payload['classification']['detectedType'], 'input_dispatching_timeout')

    def test_cli_rejects_phase1_input_without_normalize_flag(self) -> None:
        phase1 = extract_evidence_package(load_package_from_fixture(ROOT / 'tests/fixtures/nfw_01.json'))
        with tempfile.TemporaryDirectory() as tmpdir:
            phase1_path = Path(tmpdir) / 'phase1.json'
            phase1_path.write_text(json.dumps(phase1, ensure_ascii=False), encoding='utf-8')
            completed = subprocess.run(
                [sys.executable, '-m', 'anr_evidence', str(phase1_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=ENV,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('Use --normalize', completed.stderr or completed.stdout)


if __name__ == '__main__':
    unittest.main()
