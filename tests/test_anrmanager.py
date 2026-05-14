"""Tests for AnrManager logcat block extraction."""

from __future__ import annotations

import unittest

from anr_evidence.log_filter import extract_anrmanager_block, extract_anrmanager_blocks, find_anrmanager_anchor, parse_log_timestamp


class AnrManagerTests(unittest.TestCase):
    def setUp(self):
        self.sample = "\n".join([
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: ----- Output from /proc/pressure/memory -----",
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: some avg10=0.00 avg60=0.00 avg300=0.00 total=0",
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: ----- End output from /proc/pressure/memory -----",
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: CPU usage from 6404ms to 0ms ago:",
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager:   45% 1674/system_server: 28% user + 17% kernel",
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: 36% TOTAL: 19% user + 14% kernel",
            "07-02 10:14:00.883  1000  1674  2963 I AnrManager: dumpAnrDebugInfo end: AnrDumpRecord{ Input dispatching timed out ProcessRecord{2378:com.android.launcher/u0a63} }",
            "07-02 10:14:00.884  1000  1674  2963 I AnrManager: addErrorToDropBox app = ProcessRecord{2378:com.android.launcher/u0a63} tracesFile = /data/anr/anr_2024-07-02-10-13-57-639",
        ])

    def test_find_anchor_by_package(self) -> None:
        line, ts, idx = find_anrmanager_anchor(self.sample, package_name="com.android.launcher")
        self.assertIsNotNone(line)
        self.assertIn("dumpAnrDebugInfo end", line)
        self.assertIn("com.android.launcher", line)
        self.assertEqual(idx, 6)

    def test_find_anchor_no_match(self) -> None:
        line, ts, idx = find_anrmanager_anchor(self.sample, package_name="com.nonexistent")
        self.assertIsNone(line)

    def test_extract_full_block(self) -> None:
        result = extract_anrmanager_block(self.sample, package_name="com.android.launcher")
        self.assertEqual(len(result.lines), 8)
        self.assertIn("----- Output from /proc/pressure/memory -----", result.lines[0])
        self.assertIn("addErrorToDropBox", result.lines[-1])
        self.assertIn("dumpAnrDebugInfo end", result.matched_anchor)

    def test_extract_anchor_aware_block_from_repeated_package_anrs(self) -> None:
        content = "\n".join([
            "04-12 10:00:04.900 I/AnrManager( 1377): startAnrDump",
            "04-12 10:00:05.000 I/AnrManager( 1377): ANR in com.demo",
            "04-12 10:00:05.000 I/AnrManager( 1377): Reason: first reason",
            "04-12 10:00:05.001 I/AnrManager( 1377): dumpAnrDebugInfo end: AnrDumpRecord{ first ProcessRecord{100:com.demo/u0a1} }",
            "04-12 10:00:05.002 I/AnrManager( 1377): addErrorToDropBox app = ProcessRecord{100:com.demo/u0a1} mTracesFile = /data/anr/anr_first",
            "04-12 10:00:05.003 I/AnrManager( 1377):  controller = null",
            "04-12 10:01:04.900 I/AnrManager( 1377): startAnrDump",
            "04-12 10:01:05.000 I/AnrManager( 1377): ANR in com.demo",
            "04-12 10:01:05.000 I/AnrManager( 1377): Reason: second reason",
            "04-12 10:01:05.001 I/AnrManager( 1377): dumpAnrDebugInfo end: AnrDumpRecord{ second ProcessRecord{100:com.demo/u0a1} }",
            "04-12 10:01:05.002 I/AnrManager( 1377): addErrorToDropBox app = ProcessRecord{100:com.demo/u0a1} mTracesFile = /data/anr/anr_second",
            "04-12 10:01:05.003 I/AnrManager( 1377):  controller = null",
        ])

        blocks = extract_anrmanager_blocks(content, package_name="com.demo")
        first = extract_anrmanager_block(
            content,
            package_name="com.demo",
            anchor_dt=parse_log_timestamp("04-12 10:00:05.000 am_anr ANR in com.demo"),
        )
        second = extract_anrmanager_block(
            content,
            package_name="com.demo",
            anchor_dt=parse_log_timestamp("04-12 10:01:05.000 am_anr ANR in com.demo"),
        )

        self.assertEqual(len(blocks), 2)
        self.assertIn("first reason", "\n".join(first.lines))
        self.assertNotIn("second reason", "\n".join(first.lines))
        self.assertIn("second reason", "\n".join(second.lines))
        self.assertNotIn("first reason", "\n".join(second.lines))
        self.assertEqual(second.metadata["anchorDeltaMs"], 1)

    def test_extract_anchor_aware_block_does_not_reuse_wrong_anr(self) -> None:
        result = extract_anrmanager_block(
            self.sample,
            package_name="com.android.launcher",
            anchor_dt=parse_log_timestamp("07-02 10:30:00.000 am_anr ANR in com.android.launcher"),
            max_delta_seconds=10,
        )

        self.assertEqual(result.lines, [])
        self.assertEqual(result.warnings[0]["code"], "missing-anrmanager-for-anchor")

    def test_extract_full_legacy_flow_with_interleaved_lines(self) -> None:
        content = "\n".join([
            "10-14 15:37:54.983 I/AnrManager( 1377): startAnrDump",
            "10-14 15:37:54.995 I/AnrManager( 1377): dumpAnrDebugInfo begin: AnrDumpRecord{ Input dispatching timed out ProcessRecord{dca6624 9545:com.android.launcher/u0a92} IsCompleted:false }",
            "10-14 15:37:54.996 D/OtherTag( 1377): interleaved line must not terminate AnrManager extraction",
            "10-14 15:37:55.024 I/AnrManager( 1377): dumpStackTraces begin!",
            "10-14 15:38:05.016 W/InputDispatcher( 1377): unrelated input warning",
            "10-14 15:38:05.016 I/AnrManager( 1377): dumpStackTraces end!",
            "10-14 15:38:05.086 I/AnrManager( 1377): ANR in com.android.launcher (com.android.launcher/.uioverrides.QuickstepLauncher), time=3686312",
            "10-14 15:38:05.086 I/AnrManager( 1377): Reason: Input dispatching timed out",
            "10-14 15:38:05.086 I/AnrManager( 1377): Load: 19.77 / 17.39 / 17.22",
            "10-14 15:38:05.086 I/AnrManager( 1377): CPU usage from 22470ms to 0ms ago (2022-10-14 15:37:32.428 to 2022-10-14 15:37:54.897):",
            "10-14 15:38:05.086 I/AnrManager( 1377): 60% TOTAL: 36% user + 23% kernel + 1.1% iowait + 0.3% softirq",
            "10-14 15:38:05.086 I/AnrManager( 1377): CPU usage from 127ms to 1058ms later (2022-10-14 15:37:55.025 to 2022-10-14 15:37:55.955):",
            "10-14 15:38:05.086 I/AnrManager( 1377):   90% 1377/system_server: 47% user + 43% kernel / faults: 1702 minor",
            "10-14 15:38:05.086 I/AnrManager( 1377):   93% 9545/com.android.launcher: 90% user + 2.3% kernel",
            "10-14 15:38:05.086 I/AnrManager( 1377): 67% TOTAL: 44% user + 22% kernel + 0.2% softirq",
            "10-14 15:38:05.086 I/AnrManager( 1377): dumpAnrDebugInfo end: AnrDumpRecord{ Input dispatching timed out ProcessRecord{dca6624 9545:com.android.launcher/u0a92} IsCompleted:true }",
            "10-14 15:38:05.087 I/AnrManager( 1377): addErrorToDropBox app = ProcessRecord{dca6624 9545:com.android.launcher/u0a92} mTracesFile = /data/anr/anr_2022-10-14-15-37-56-091",
            "10-14 15:38:05.090 I/AnrManager( 1377):  controller = null",
        ])

        result = extract_anrmanager_block(content, package_name="com.android.launcher")

        self.assertIn("startAnrDump", result.lines[0])
        self.assertIn("controller = null", result.lines[-1])
        self.assertIn("dumpAnrDebugInfo end", result.matched_anchor)
        self.assertTrue(any("60% TOTAL" in line for line in result.lines))
        self.assertTrue(any("67% TOTAL" in line for line in result.lines))
        self.assertFalse(any("OtherTag" in line for line in result.lines))

    def test_extract_empty_content(self) -> None:
        result = extract_anrmanager_block("", package_name="com.foo")
        self.assertEqual(len(result.lines), 0)
        self.assertEqual(result.warnings[0]["code"], "empty-logcat")

    def test_extract_missing_package(self) -> None:
        result = extract_anrmanager_block(self.sample, package_name="com.wrong")
        self.assertEqual(len(result.lines), 0)
        self.assertEqual(result.warnings[0]["code"], "missing-anrmanager")

    def test_anrmanager_critical_in_weights(self) -> None:
        from anr_evidence.weighting import LOGCAT_SIGNAL_WEIGHTS, ImportanceLevel
        self.assertEqual(LOGCAT_SIGNAL_WEIGHTS["anrmanager"], ImportanceLevel.CRITICAL)
