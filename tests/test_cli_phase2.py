from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch

from anr_evidence import extract_evidence_package, load_package_from_fixture
from anr_evidence.cli import _transform_payload
from tests.helpers import REPO_ROOT, run_cli, write_json_fixture


class CliPhase2Tests(unittest.TestCase):
    def test_normalize_does_not_compute_later_phases(self) -> None:
        raw = load_package_from_fixture(REPO_ROOT / 'tests/fixtures/nfw_01.json')

        with patch('anr_evidence.pipeline.analyze_normalized_package', side_effect=AssertionError('phase 3 must not run')):
            result = _transform_payload(
                raw,
                normalize=True,
                analyze=False,
                hypothesize=False,
                report=False,
                root_cause=False,
                remediate=False,
                deliver=False,
            )

        self.assertEqual(result['metadata']['phase'], 'phase2-evidence-normalization')
    def test_cli_normalize_from_fixture_input(self) -> None:
        completed = run_cli('--normalize', 'tests/fixtures/nfw_01.json')
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase2-evidence-normalization')
        self.assertEqual(payload['classification']['detectedType'], 'no_focus_window')

    def test_cli_normalize_from_phase1_package_input(self) -> None:
        phase1 = extract_evidence_package(load_package_from_fixture(REPO_ROOT / 'tests/fixtures/idt_01.json'))
        with tempfile.TemporaryDirectory() as tmpdir:
            phase1_path = write_json_fixture(tmpdir, 'phase1.json', phase1)
            completed = run_cli('--normalize', str(phase1_path))
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase2-evidence-normalization')
        self.assertEqual(payload['classification']['detectedType'], 'input_dispatching_timeout')

    def test_cli_rejects_phase1_input_without_normalize_flag(self) -> None:
        phase1 = extract_evidence_package(load_package_from_fixture(REPO_ROOT / 'tests/fixtures/nfw_01.json'))
        with tempfile.TemporaryDirectory() as tmpdir:
            phase1_path = write_json_fixture(tmpdir, 'phase1.json', phase1)
            completed = run_cli(str(phase1_path), check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('Use --normalize', completed.stderr or completed.stdout)


if __name__ == '__main__':
    unittest.main()
