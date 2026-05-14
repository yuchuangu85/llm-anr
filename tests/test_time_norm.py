"""Tests for anr_evidence.time_norm."""

from __future__ import annotations

import unittest
from datetime import datetime

from anr_evidence.time_norm import TimeNormalizedLine, compute_delta_t, compute_delta_t_for_group
from anr_evidence.log_filter import parse_log_timestamp


class TimeNormTests(unittest.TestCase):
    def test_compute_delta_t_precision(self) -> None:
        anchor = datetime(2026, 4, 12, 10, 0, 5, 0)
        lines = [
            "04-12 10:00:02.000 I am_proc_died: [0,1234]",
            "04-12 10:00:05.000 I am_anr: [0,1234,com.foo]",
            "04-12 10:00:08.500 I wm_task_moved: task=1",
        ]
        result = compute_delta_t(lines, anchor, source_kind="event_log")
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result[0].delta_t_seconds, -3.0, places=1)
        self.assertAlmostEqual(result[1].delta_t_seconds, 0.0, places=1)
        self.assertAlmostEqual(result[2].delta_t_seconds, 3.5, places=1)
        self.assertEqual(result[0].source_kind, "event_log")

    def test_unparseable_timestamps(self) -> None:
        anchor = datetime(2026, 4, 12, 10, 0, 0, 0)
        lines = ["no timestamp here", "04-12 10:00:01.000 valid"]
        result = compute_delta_t(lines, anchor, source_kind="logcat")
        self.assertIsNone(result[0].delta_t_seconds)
        self.assertIsNone(result[0].timestamp_iso)
        self.assertIsNotNone(result[1].delta_t_seconds)
        self.assertEqual(result[1].source_kind, "logcat")

    def test_compute_delta_t_for_group(self) -> None:
        group = {
            "id": "anr-test",
            "anchor": {"timestamp": "04-12 10:00:05.000", "sourceKind": "event_log", "line": "am_anr"},
            "trace": {"lines": ["04-12 10:00:04.000 main tid=1"], "warnings": []},
            "eventLog": {"lines": ["04-12 10:00:03.000 am_proc_died"], "warnings": []},
            "logcat": {"lines": ["04-12 10:00:05.500 W ActivityManager: timeout"], "warnings": []},
        }
        result = compute_delta_t_for_group(group)
        self.assertIn("trace", result)
        self.assertAlmostEqual(result["trace"][0].delta_t_seconds, -1.0, places=1)
        self.assertAlmostEqual(result["eventLog"][0].delta_t_seconds, -2.0, places=1)
        self.assertAlmostEqual(result["logcat"][0].delta_t_seconds, 0.5, places=1)

    def test_group_without_anchor(self) -> None:
        group = {"id": "orphan", "anchor": None, "trace": {"lines": ["some content"]}}
        result = compute_delta_t_for_group(group)
        self.assertIsNone(result["trace"][0].delta_t_seconds)
