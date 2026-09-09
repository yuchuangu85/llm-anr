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


class CliPhase5Tests(unittest.TestCase):
    def test_cli_hypothesize_from_fixture_input(self) -> None:
        completed = run_cli('--hypothesize', 'tests/fixtures/nfw_01.json')
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase5-causal-draft')
        self.assertEqual(payload['classification']['detectedType'], 'no_focus_window')

    def test_cli_hypothesize_from_phase3_input(self) -> None:
        phase3 = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_package_from_fixture(REPO_ROOT / 'tests/fixtures/idt_01.json'))))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_json_fixture(tmpdir, 'phase3.json', phase3)
            completed = run_cli('--hypothesize', str(path))
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase5-causal-draft')
        self.assertEqual(payload['classification']['detectedType'], 'input_dispatching_timeout')

    def test_cli_rejects_phase3_input_without_hypothesize_flag(self) -> None:
        phase3 = analyze_normalized_package(normalize_evidence_package(extract_evidence_package(load_package_from_fixture(REPO_ROOT / 'tests/fixtures/nfw_01.json'))))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_json_fixture(tmpdir, 'phase3.json', phase3)
            completed = run_cli(str(path), check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('Use --hypothesize, --root-cause, --remediate, --deliver, or --report', completed.stderr or completed.stdout)


if __name__ == '__main__':
    unittest.main()
