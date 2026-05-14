"""Tests for anr_evidence.context_flooding."""

from __future__ import annotations

import unittest

from anr_evidence.context_flooding import TruncationConfig, truncate_evidence
from anr_evidence.evidence_slice import EvidenceSlice


class ContextFloodingTests(unittest.TestCase):
    def _make_slices(self, n_critical: int, n_warning: int, n_contextual: int) -> list[EvidenceSlice]:
        slices = []
        for i in range(n_critical):
            slices.append(EvidenceSlice(
                source="event_log", timestamp_iso=None, delta_t_seconds=float(-i),
                tag="am_anr", content=f"critical {i}", importance="critical",
                group_id="g1", line_index=i,
            ))
        for i in range(n_warning):
            slices.append(EvidenceSlice(
                source="event_log", timestamp_iso=None, delta_t_seconds=float(-i - 10),
                tag="am_meminfo", content=f"warning {i}", importance="warning",
                group_id="g1", line_index=i + n_critical,
            ))
        for i in range(n_contextual):
            slices.append(EvidenceSlice(
                source="event_log", timestamp_iso=None, delta_t_seconds=float(-i - 20),
                tag="am_proc_good", content=f"contextual {i}", importance="contextual",
                group_id="g1", line_index=i + n_critical + n_warning,
            ))
        return slices

    def test_criticals_always_retained(self) -> None:
        slices = self._make_slices(10, 0, 0)
        config = TruncationConfig(max_total_lines=5, min_importance="warning")
        result = truncate_evidence(slices, config)
        self.assertEqual(len(result.retained_slices), 10)

    def test_contextual_dropped_when_min_is_warning(self) -> None:
        slices = self._make_slices(5, 5, 5)
        config = TruncationConfig(max_total_lines=100, min_importance="warning")
        result = truncate_evidence(slices, config)
        retained_importances = {s.importance for s in result.retained_slices}
        self.assertNotIn("contextual", retained_importances)
        self.assertEqual(len(result.dropped_slices), 5)

    def test_overflow_truncation(self) -> None:
        slices = self._make_slices(50, 50, 0)
        config = TruncationConfig(max_total_lines=60, min_importance="warning")
        result = truncate_evidence(slices, config)
        total = len(result.retained_slices) + len(result.dropped_slices)
        self.assertEqual(total, 100)
        self.assertLessEqual(len(result.retained_slices), 60)

    def test_all_contextual_dropped_with_min_critical(self) -> None:
        slices = self._make_slices(3, 3, 3)
        config = TruncationConfig(max_total_lines=100, min_importance="critical")
        result = truncate_evidence(slices, config)
        retained_importances = {s.importance for s in result.retained_slices}
        self.assertEqual(retained_importances, {"critical"})
