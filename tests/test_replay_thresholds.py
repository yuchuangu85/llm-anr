from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anr_evidence import archive_replay_session, compare_replay_sessions, evaluate_replay_diff

ROOT = Path(__file__).resolve().parent.parent


class ReplayThresholdTests(unittest.TestCase):
    def test_threshold_evaluation_detects_rule_regression(self) -> None:
        diff = {
            "caseCountBefore": 1,
            "caseCountAfter": 1,
            "totalElapsedMsDelta": 0,
            "totalArtifactBytesDelta": 0,
            "ruleTotalsDiff": {
                "mainThreadCaptured": {"before": 1, "after": 0, "delta": -1},
            },
            "caseDiffs": [
                {
                    "id": "case-1",
                    "phaseChanged": False,
                    "elapsedMsDelta": 0,
                    "artifactBytesDelta": 0,
                    "ruleSignalChanges": {
                        "mainThreadCaptured": {"before": True, "after": False},
                    },
                }
            ],
        }
        evaluation = evaluate_replay_diff(diff)
        self.assertFalse(evaluation['passed'])
        kinds = {failure['kind'] for failure in evaluation['failures']}
        self.assertIn('rule-total-regressed', kinds)
        self.assertIn('case-rule-regressed', kinds)
