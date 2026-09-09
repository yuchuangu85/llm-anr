from __future__ import annotations

import hashlib
import json
import unittest

from anr_evidence.compact_evidence import build_compact_evidence_view
from anr_evidence.evidence_slice import EvidenceSlice


class CompactEvidenceViewTests(unittest.TestCase):
    def test_hard_utf8_budget_keeps_whole_records_and_reports_truncation(self) -> None:
        slices = [
            EvidenceSlice(
                source="event_log", timestamp_iso=None, delta_t_seconds=0.0,
                tag="am_anr", content="关键证据😀", importance="critical",
                group_id="g", line_index=0,
            ),
            EvidenceSlice(
                source="logcat", timestamp_iso=None, delta_t_seconds=1.0,
                tag=None, content="second evidence", importance="contextual",
                group_id="g", line_index=1,
            ),
        ]
        full = build_compact_evidence_view(slices, max_bytes=16 * 1024)
        first_record = full.content.splitlines(keepends=True)[0]
        result = build_compact_evidence_view(slices, max_bytes=len(first_record))

        self.assertEqual(result.content, first_record)
        self.assertLessEqual(len(result.content), len(first_record))
        self.assertEqual(result.metadata["retainedBytes"], len(result.content))
        self.assertEqual(result.metadata["sha256"], hashlib.sha256(result.content).hexdigest())
        self.assertTrue(result.metadata["truncated"])
        self.assertEqual(result.metadata["droppedCount"], 1)
        self.assertTrue(result.metadata["recordBoundaryOnly"])
        decoded = [json.loads(line) for line in result.content.decode("utf-8").splitlines()]
        self.assertEqual(decoded[0]["content"], "关键证据😀")

    def test_invalid_budget_fails_closed(self) -> None:
        for budget in (0, -1, True, 1.5):
            with self.subTest(budget=budget):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    build_compact_evidence_view([], max_bytes=budget)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
