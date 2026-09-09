from __future__ import annotations

import json
import tempfile
import unittest

from anr_evidence import (
    analyze_normalized_package,
    extract_evidence_package,
    generate_causal_draft,
    load_package_from_fixture,
    normalize_evidence_package,
)
from tests.helpers import REPO_ROOT, run_cli, write_json_fixture


class CliPhase6Tests(unittest.TestCase):
    def test_cli_root_cause_from_fixture_input(self) -> None:
        completed = run_cli('--root-cause', 'tests/fixtures/nfw_01.json')
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase6-root-cause-report-v1')
        self.assertEqual(payload['classification']['detectedType'], 'no_focus_window')

    def test_cli_root_cause_from_phase5_input(self) -> None:
        phase5 = generate_causal_draft(
            analyze_normalized_package(
                normalize_evidence_package(
                    extract_evidence_package(load_package_from_fixture(REPO_ROOT / 'tests/fixtures/idt_01.json'))
                )
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_json_fixture(tmpdir, 'phase5.json', phase5)
            completed = run_cli('--root-cause', str(path))
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase6-root-cause-report-v1')
        self.assertEqual(payload['classification']['detectedType'], 'input_dispatching_timeout')

    def test_cli_rejects_phase5_input_without_root_cause_flag(self) -> None:
        phase5 = generate_causal_draft(
            analyze_normalized_package(
                normalize_evidence_package(
                    extract_evidence_package(load_package_from_fixture(REPO_ROOT / 'tests/fixtures/nfw_01.json'))
                )
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_json_fixture(tmpdir, 'phase5.json', phase5)
            completed = run_cli(str(path), check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('Use --root-cause, --remediate, --deliver, or --report', completed.stderr or completed.stdout)


if __name__ == '__main__':
    unittest.main()
