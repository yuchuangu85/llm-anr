"""Tests for anr_evidence.weighting."""

from __future__ import annotations

import unittest

from anr_evidence.weighting import (
    EVENT_LOG_TAG_WEIGHTS,
    ImportanceLevel,
    KERNEL_SIGNAL_WEIGHTS,
    LOGCAT_SIGNAL_WEIGHTS,
    filter_by_importance,
    get_weights_for_source,
    resolve_tag,
)


class WeightingTests(unittest.TestCase):
    def test_critical_tags(self) -> None:
        self.assertEqual(EVENT_LOG_TAG_WEIGHTS["am_anr"], ImportanceLevel.CRITICAL)
        self.assertEqual(EVENT_LOG_TAG_WEIGHTS["am_kill"], ImportanceLevel.CRITICAL)
        self.assertEqual(EVENT_LOG_TAG_WEIGHTS["wm_focus"], ImportanceLevel.CRITICAL)
        self.assertEqual(EVENT_LOG_TAG_WEIGHTS["input_focus"], ImportanceLevel.CRITICAL)

    def test_contextual_tags(self) -> None:
        self.assertEqual(EVENT_LOG_TAG_WEIGHTS["am_proc_good"], ImportanceLevel.CONTEXTUAL)
        self.assertEqual(EVENT_LOG_TAG_WEIGHTS["am_pre_boot"], ImportanceLevel.CONTEXTUAL)

    def test_event_log_tag_count(self) -> None:
        self.assertEqual(len(EVENT_LOG_TAG_WEIGHTS), 55)

    def test_logcat_pattern_count(self) -> None:
        self.assertEqual(len(LOGCAT_SIGNAL_WEIGHTS), 14)

    def test_kernel_pattern_count(self) -> None:
        self.assertEqual(len(KERNEL_SIGNAL_WEIGHTS), 13)

    def test_critical_less_than_warning(self) -> None:
        self.assertLess(ImportanceLevel.CRITICAL, ImportanceLevel.WARNING)
        self.assertLess(ImportanceLevel.WARNING, ImportanceLevel.CONTEXTUAL)

    def test_get_weights_for_source(self) -> None:
        self.assertEqual(len(get_weights_for_source("event_log")), 55)
        self.assertEqual(len(get_weights_for_source("logcat")), 14)
        self.assertEqual(len(get_weights_for_source("kernel_log")), 13)
        self.assertEqual(len(get_weights_for_source("unknown")), 0)

    def test_resolve_tag(self) -> None:
        patterns = frozenset({"am_anr", "wm_task_moved", "battery_level"})
        self.assertEqual(resolve_tag("04-12 10:00:05.000 I am_anr: ...", patterns), "am_anr")
        self.assertIsNone(resolve_tag("no match here", patterns))

    def test_filter_by_importance_drops_contextual(self) -> None:
        lines = [
            "04-12 10:00:01 I am_anr: trigger",
            "04-12 10:00:02 I am_proc_good: cleanup",
            "04-12 10:00:03 I am_meminfo: pressure",
        ]
        patterns = frozenset({"am_anr", "am_proc_good", "am_meminfo"})
        weights = {"am_anr": ImportanceLevel.CRITICAL, "am_proc_good": ImportanceLevel.CONTEXTUAL, "am_meminfo": ImportanceLevel.WARNING}
        result = filter_by_importance(lines, weights, patterns, min_level=ImportanceLevel.WARNING)
        self.assertIn(lines[0], result)
        self.assertNotIn(lines[1], result)
        self.assertIn(lines[2], result)
