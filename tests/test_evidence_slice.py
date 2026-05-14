"""Tests for anr_evidence.evidence_slice."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anr_evidence.evidence_slice import (
    EvidenceSlice,
    annotate_slices_with_tags,
    build_evidence_slices,
    filter_slices_by_delta_t,
    filter_slices_by_importance,
    filter_slices_by_source,
    group_slices_by_source,
    read_ess_jsonl,
    write_ess_jsonl,
)


class EvidenceSliceTests(unittest.TestCase):
    def setUp(self):
        self.groups = [{
            "id": "anr-20260412-100005-000",
            "anchor": {"timestamp": "04-12 10:00:05.000", "sourceKind": "event_log", "line": "am_anr"},
            "trace": {"lines": ["04-12 10:00:04.000 main tid=1"], "warnings": []},
            "eventLog": {"lines": ["04-12 10:00:03.000 I am_anr: trigger", "04-12 10:00:02.000 I am_proc_died: ..."], "warnings": []},
            "logcat": {"lines": ["04-12 10:00:06.000 W ActivityManager: timeout"], "warnings": []},
        }]

    def test_build_evidence_slices(self) -> None:
        slices = build_evidence_slices(self.groups)
        self.assertEqual(len(slices), 4)
        self.assertTrue(any(s.delta_t_seconds is not None for s in slices))
        self.assertEqual(slices[0].source, "trace")

    def test_annotate_slices_with_tags(self) -> None:
        slices = build_evidence_slices(self.groups)
        annotated = annotate_slices_with_tags(slices)
        tagged = [s for s in annotated if s.tag is not None]
        self.assertGreater(len(tagged), 0)

    def test_jsonl_roundtrip(self) -> None:
        slices = build_evidence_slices(self.groups)
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w+", delete=False) as f:
            tmp = Path(f.name)
        try:
            write_ess_jsonl(slices, tmp)
            loaded = read_ess_jsonl(tmp)
            self.assertEqual(len(loaded), len(slices))
            self.assertEqual(loaded[0].source, slices[0].source)
        finally:
            tmp.unlink(missing_ok=True)

    def test_filter_slices_by_importance(self) -> None:
        slices = build_evidence_slices(self.groups)
        annotated = annotate_slices_with_tags(slices)
        filtered = filter_slices_by_importance(annotated, min_importance="warning")
        for s in filtered:
            self.assertIn(s.importance, ("critical", "warning"))

    def test_filter_slices_by_delta_t(self) -> None:
        slices = build_evidence_slices(self.groups)
        filtered = filter_slices_by_delta_t(slices, min_delta_t=-2.0, max_delta_t=2.0)
        for s in filtered:
            if s.delta_t_seconds is not None:
                self.assertGreaterEqual(s.delta_t_seconds, -2.0)
                self.assertLessEqual(s.delta_t_seconds, 2.0)

    def test_filter_slices_by_source(self) -> None:
        slices = build_evidence_slices(self.groups)
        filtered = filter_slices_by_source(slices, source_kinds=["trace"])
        self.assertTrue(all(s.source == "trace" for s in filtered))

    def test_group_slices_by_source(self) -> None:
        slices = build_evidence_slices(self.groups)
        grouped = group_slices_by_source(slices)
        self.assertIn("trace", grouped)
        self.assertIn("event_log", grouped)
        self.assertIn("logcat", grouped)
