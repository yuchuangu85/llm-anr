from __future__ import annotations

import json
import tempfile
import unittest

from anr_evidence import (
    analyze_normalized_package,
    extract_evidence_package,
    generate_causal_draft,
    generate_root_cause_report,
    load_package_from_fixture,
    normalize_evidence_package,
)
from tests.helpers import REPO_ROOT, run_cli, write_json_fixture


class CliPhase7Tests(unittest.TestCase):
    def test_cli_remediate_from_fixture_input(self) -> None:
        completed = run_cli('--remediate', 'tests/fixtures/nfw_01.json')
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase7-remediation-draft')
        self.assertFalse(payload['metadata']['finalAdvice'])

    def test_cli_remediate_from_phase6_input(self) -> None:
        phase6 = generate_root_cause_report(
            generate_causal_draft(
                analyze_normalized_package(
                    normalize_evidence_package(
                        extract_evidence_package(load_package_from_fixture(REPO_ROOT / 'tests/fixtures/idt_01.json'))
                    )
                )
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_json_fixture(tmpdir, 'phase6.json', phase6)
            completed = run_cli('--remediate', str(path))
        payload = json.loads(completed.stdout)
        self.assertEqual(payload['metadata']['phase'], 'phase7-remediation-draft')

    def test_cli_rejects_phase6_input_without_remediate_flag(self) -> None:
        phase6 = generate_root_cause_report(
            generate_causal_draft(
                analyze_normalized_package(
                    normalize_evidence_package(
                        extract_evidence_package(load_package_from_fixture(REPO_ROOT / 'tests/fixtures/nfw_01.json'))
                    )
                )
            )
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_json_fixture(tmpdir, 'phase6.json', phase6)
            completed = run_cli(str(path), check=False)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('Use --remediate, --deliver, or --report', completed.stderr or completed.stdout)


if __name__ == '__main__':
    unittest.main()
