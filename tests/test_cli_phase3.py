from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch

from anr_evidence import extract_evidence_package, load_package_from_fixture, normalize_evidence_package
from anr_evidence.cli import _transform_payload
from tests.helpers import REPO_ROOT, run_cli, write_json_fixture


class CliPhase3Tests(unittest.TestCase):
    def test_analyze_does_not_compute_later_phases(self) -> None:
        phase2 = normalize_evidence_package(extract_evidence_package(load_package_from_fixture(REPO_ROOT / 'tests/fixtures/nfw_01.json')))

        with patch('anr_evidence.pipeline.generate_causal_draft', side_effect=AssertionError('phase 5 must not run')):
            result = _transform_payload(
                phase2,
                normalize=False,
                analyze=True,
                hypothesize=False,
                report=False,
                root_cause=False,
                remediate=False,
                deliver=False,
            )

        self.assertEqual(result['metadata']['phase'], 'phase3-assisted-analysis')
    def test_cli_analyze_from_fixture_input(self) -> None:
        completed = run_cli('--analyze', 'tests/fixtures/nfw_01.json')
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase3-assisted-analysis')
        self.assertEqual(payload['classification']['detectedType'], 'no_focus_window')

    def test_cli_analyze_from_phase2_input(self) -> None:
        phase2 = normalize_evidence_package(extract_evidence_package(load_package_from_fixture(REPO_ROOT / 'tests/fixtures/idt_01.json')))
        with tempfile.TemporaryDirectory() as tmpdir:
            phase2_path = write_json_fixture(tmpdir, 'phase2.json', phase2)
            completed = run_cli('--analyze', str(phase2_path))
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase3-assisted-analysis')
        self.assertEqual(payload['classification']['detectedType'], 'input_dispatching_timeout')

    def test_cli_rejects_phase2_input_without_analyze_flag(self) -> None:
        phase2 = normalize_evidence_package(extract_evidence_package(load_package_from_fixture(REPO_ROOT / 'tests/fixtures/nfw_01.json')))
        with tempfile.TemporaryDirectory() as tmpdir:
            phase2_path = write_json_fixture(tmpdir, 'phase2.json', phase2)
            completed = run_cli(str(phase2_path), check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('Use --analyze', completed.stderr or completed.stdout)


if __name__ == '__main__':
    unittest.main()
