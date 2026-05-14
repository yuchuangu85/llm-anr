from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from anr_evidence import archive_replay_session

ROOT = Path(__file__).resolve().parent.parent


class ReplaySessionTests(unittest.TestCase):
    def test_rule_coverage_expectations_are_evaluated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = Path(tmpdir) / 'manifest.json'
            manifest.write_text(json.dumps({
                "version": 1,
                "cases": [
                    {
                        "id": "nfw-check",
                        "input": str((ROOT / 'tests/fixtures/nfw_01.json').resolve()),
                        "operation": "analyze",
                        "expectedPhase": "phase3-assisted-analysis",
                        "expectedSignals": {
                            "mainThreadCaptured": True,
                            "inputWaitDetected": True,
                            "crossSourceInputConsistency": True
                        }
                    }
                ]
            }, ensure_ascii=False), encoding='utf-8')
            result = archive_replay_session(manifest, tmpdir, label='expect')
            coverage = json.loads((Path(result['sessionDir']) / 'rule-coverage.json').read_text(encoding='utf-8'))
            self.assertEqual(coverage['expectationSummary']['casesWithExpectations'], 1)
            self.assertEqual(coverage['expectationSummary']['matchedCases'], 1)
            self.assertEqual(coverage['expectationSummary']['failedCases'], 0)


if __name__ == '__main__':
    unittest.main()
