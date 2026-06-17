"""Tests for the structured AnrManager block parser + derived hints."""

from __future__ import annotations

import unittest

from anr_evidence import parse_anrmanager_block


def _hint(summary: dict, hint_id: str) -> dict | None:
    for hint in summary.get("derivedHints", []):
        if hint["id"] == hint_id:
            return hint
    return None


class AnrManagerParserTests(unittest.TestCase):
    def test_parses_full_classic_block(self) -> None:
        lines = [
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: ----- Output from /proc/pressure/memory -----",
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: some avg10=0.50 avg60=0.10 avg300=0.05 total=1234567",
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: full avg10=0.10 avg60=0.05 avg300=0.01 total=987",
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: ----- End output from /proc/pressure/memory -----",
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: CPU usage from 6404ms to 0ms ago:",
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager:   45% 1674/system_server: 28% user + 17% kernel",
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager:   12% 2378/com.android.launcher: 9% user + 3% kernel",
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: 36% TOTAL: 19% user + 14% kernel + 3% iowait",
            "07-02 10:14:00.883  1000  1674  2963 I AnrManager: dumpAnrDebugInfo end: AnrDumpRecord{ Input dispatching timed out ProcessRecord{2378:com.android.launcher/u0a63} }",
            "07-02 10:14:00.884  1000  1674  2963 I AnrManager: addErrorToDropBox app = ProcessRecord{2378:com.android.launcher/u0a63} tracesFile = /data/anr/anr_2024-07-02-10-13-57-639",
        ]
        summary = parse_anrmanager_block(lines)

        self.assertEqual(summary["anrReason"], "Input dispatching timed out")
        self.assertEqual(summary["anrPackage"], "com.android.launcher")
        self.assertEqual(summary["anrPid"], 2378)
        self.assertEqual(summary["tracesFilePath"], "/data/anr/anr_2024-07-02-10-13-57-639")
        self.assertEqual(summary["cpuWindow"], {"fromMsAgo": 6404, "toMsAgo": 0})
        self.assertEqual(summary["cpuTotal"]["totalPct"], 36.0)
        self.assertEqual(summary["cpuTotal"]["iowaitPct"], 3.0)
        # Top CPU sorted by totalPct
        self.assertEqual(summary["cpuTopProcesses"][0]["pid"], 1674)
        self.assertEqual(summary["cpuTopProcesses"][0]["processName"], "system_server")
        self.assertEqual(summary["cpuTopProcesses"][1]["pid"], 2378)
        # PSI
        self.assertEqual(summary["memoryPressure"]["some"]["avg10"], 0.50)
        self.assertEqual(summary["memoryPressure"]["full"]["avg10"], 0.10)

        # Reason classified
        rh = _hint(summary, "ANR_REASON_CLASSIFIED")
        self.assertIsNotNone(rh)
        self.assertEqual(rh["anrType"], "input_dispatching_timeout")

    def test_parses_legacy_full_flow_with_multiple_cpu_windows(self) -> None:
        lines = [
            "10-14 15:37:54.983 I/AnrManager( 1377): startAnrDump",
            "10-14 15:37:54.995 I/AnrManager( 1377): dumpAnrDebugInfo begin: AnrDumpRecord{ Input dispatching timed out ProcessRecord{dca6624 9545:com.android.launcher/u0a92} IsCompleted:false }",
            "10-14 15:38:05.086 I/AnrManager( 1377): ANR in com.android.launcher (com.android.launcher/.uioverrides.QuickstepLauncher), time=3686312",
            "10-14 15:38:05.086 I/AnrManager( 1377): Reason: Input dispatching timed out (421152a com.android.launcher/com.android.launcher.uioverrides.QuickstepLauncher (server) is not responding. Waited 5000ms)",
            "10-14 15:38:05.086 I/AnrManager( 1377): Load: 19.77 / 17.39 / 17.22",
            "10-14 15:38:05.086 I/AnrManager( 1377): ----- Output from /proc/pressure/memory -----",
            "10-14 15:38:05.086 I/AnrManager( 1377): some avg10=3.69 avg60=2.41 avg300=1.43 total=129243245",
            "10-14 15:38:05.086 I/AnrManager( 1377): full avg10=1.17 avg60=0.77 avg300=0.41 total=30487110",
            "10-14 15:38:05.086 I/AnrManager( 1377): ----- End output from /proc/pressure/memory -----",
            "10-14 15:38:05.086 I/AnrManager( 1377): ----- Output from /proc/pressure/cpu -----",
            "10-14 15:38:05.086 I/AnrManager( 1377): some avg10=8.96 avg60=13.13 avg300=15.25 total=29542821278",
            "10-14 15:38:05.086 I/AnrManager( 1377): full avg10=0.00 avg60=0.00 avg300=0.00 total=0",
            "10-14 15:38:05.086 I/AnrManager( 1377): ----- End output from /proc/pressure/cpu -----",
            "10-14 15:38:05.086 I/AnrManager( 1377): CPU usage from 22470ms to 0ms ago (2022-10-14 15:37:32.428 to 2022-10-14 15:37:54.897):",
            "10-14 15:38:05.086 I/AnrManager( 1377):   69% 1377/system_server: 43% user + 26% kernel / faults: 106196 minor 30 major",
            "10-14 15:38:05.086 I/AnrManager( 1377):   32% 9545/com.android.launcher: 30% user + 1.7% kernel / faults: 16453 minor 29 major",
            "10-14 15:38:05.086 I/AnrManager( 1377): 60% TOTAL: 36% user + 23% kernel + 1.1% iowait + 0.3% softirq",
            "10-14 15:38:05.086 I/AnrManager( 1377): CPU usage from 127ms to 1058ms later (2022-10-14 15:37:55.025 to 2022-10-14 15:37:55.955):",
            "10-14 15:38:05.086 I/AnrManager( 1377):   90% 1377/system_server: 47% user + 43% kernel / faults: 1702 minor",
            "10-14 15:38:05.086 I/AnrManager( 1377):   +0% 5393/SoundPool_66: 0% user + 0% kernel",
            "10-14 15:38:05.086 I/AnrManager( 1377):   93% 9545/com.android.launcher: 90% user + 2.3% kernel",
            "10-14 15:38:05.086 I/AnrManager( 1377): 67% TOTAL: 44% user + 22% kernel + 0.2% softirq",
            "10-14 15:38:05.086 I/AnrManager( 1377): dumpAnrDebugInfo end: AnrDumpRecord{ Input dispatching timed out ProcessRecord{dca6624 9545:com.android.launcher/u0a92} IsCompleted:true }",
            "10-14 15:38:05.087 I/AnrManager( 1377): addErrorToDropBox app = ProcessRecord{dca6624 9545:com.android.launcher/u0a92} mTracesFile = /data/anr/anr_2022-10-14-15-37-56-091",
            "10-14 15:38:05.090 I/AnrManager( 1377):  controller = null",
        ]

        summary = parse_anrmanager_block(lines)

        self.assertEqual(summary["load"]["load1"], 19.77)
        self.assertEqual(summary["memoryPressure"]["some"]["avg10"], 3.69)
        self.assertEqual(summary["pressure"]["cpu"]["some"]["avg10"], 8.96)
        self.assertEqual(summary["anrPackage"], "com.android.launcher")
        self.assertEqual(summary["anrPid"], 9545)
        self.assertIn("Input dispatching timed out", summary["anrReason"])
        self.assertEqual(summary["tracesFilePath"], "/data/anr/anr_2022-10-14-15-37-56-091")
        self.assertEqual(len(summary["cpuWindows"]), 2)
        self.assertEqual(summary["cpuWindows"][0]["total"]["totalPct"], 60.0)
        self.assertEqual(summary["cpuWindows"][0]["total"]["iowaitPct"], 1.1)
        self.assertEqual(summary["cpuWindows"][1]["direction"], "later")
        self.assertEqual(summary["cpuTotal"]["totalPct"], 67.0)
        self.assertEqual(summary["cpuTotal"]["softirqPct"], 0.2)
        self.assertIsNone(summary["cpuTotal"]["iowaitPct"])
        self.assertEqual(summary["cpuTopProcesses"][0]["processName"], "com.android.launcher")
        self.assertEqual(summary["cpuTopProcesses"][0]["totalPct"], 93.0)
        self.assertIsNotNone(_hint(summary, "SYSTEM_SERVER_CPU_HIGH"))
        app_hint = _hint(summary, "ANR_PROCESS_CPU_HIGH")
        self.assertIsNotNone(app_hint)
        self.assertEqual(app_hint["thresholdPct"], 85)
        self.assertTrue(app_hint["requiresMemoryCorrelation"])
        self.assertEqual(app_hint["suspectedIssue"], "app_load_high_possible_memory_leak")
        self.assertIn("应用自身负载过高", app_hint["message"])
        self.assertIn("内存泄漏", app_hint["message"])

    def test_target_cpu_over_85_in_two_pid_threadtime_marks_app_load_high(self) -> None:
        lines = [
            "04-22 02:34:38.785  1485  7148 I AnrManager: ANR in com.tcl.android.launcher (com.tcl.android.launcher/.uioverrides.TclQuickstepLauncher), time=17659496",
            "04-22 02:34:38.785  1485  7148 I AnrManager: Reason: Input dispatching timed out (Application does not have a focused window).",
            "04-22 02:34:38.785  1485  7148 I AnrManager:   114% 6039/com.tcl.android.launcher: 92% user + 22% kernel / faults: 17606 minor 15 major",
            "04-22 02:34:38.785  1485  7148 I AnrManager: 72% TOTAL: 43% user + 26% kernel + 0% iowait",
        ]

        summary = parse_anrmanager_block(lines)

        self.assertEqual(summary["anrPackage"], "com.tcl.android.launcher")
        self.assertEqual(summary["cpuTotal"]["totalPct"], 72.0)
        self.assertIsNone(_hint(summary, "SYSTEM_CPU_SATURATED"))
        app_hint = _hint(summary, "ANR_PROCESS_CPU_HIGH")
        self.assertIsNotNone(app_hint)
        self.assertEqual(app_hint["process"]["totalPct"], 114.0)
        self.assertEqual(app_hint["process"]["processName"], "com.tcl.android.launcher")
        self.assertIn("CPU=114.0% (>85%)", app_hint["message"])
        self.assertIn("大概率为内存泄漏或内存膨胀", app_hint["message"])

    def test_processes_over_90_are_collected_and_target_gets_critical_hint(self) -> None:
        lines = [
            "06-09 13:12:54.321  1302  8310 I AnrManager: ANR in com.tcl.android.launcher, time=66595997",
            "06-09 13:12:54.321  1302  8310 I AnrManager: Reason: Input dispatching timed out (37e4ea9 Taskbar is not responding. Waited 8000ms for MotionEvent).",
            "06-09 13:12:54.321  1302  8310 I AnrManager:   95% 999/vendor.tcl.ipel@1.0-service: 95% user + 0% kernel",
            "06-09 13:12:54.321  1302  8310 I AnrManager:   93% 9373/com.tcl.android.launcher: 60% user + 33% kernel / faults: 1528 minor 35 major",
            "06-09 13:12:54.321  1302  8310 I AnrManager:   91% 8089/com.google.android.apps.nbu.files: 64% user + 27% kernel",
            "06-09 13:12:54.321  1302  8310 I AnrManager: 99% TOTAL: 52% user + 42% kernel + 0.7% iowait + 3.1% irq + 1.2% softirq",
        ]

        summary = parse_anrmanager_block(lines)

        over_90_names = [proc["processName"] for proc in summary["highCpuProcessesOver90"]]
        self.assertEqual(
            over_90_names,
            ["vendor.tcl.ipel@1.0-service", "com.tcl.android.launcher", "com.google.android.apps.nbu.files"],
        )
        high_hint = _hint(summary, "HIGH_CPU_PROCESS_OVER_90")
        self.assertIsNotNone(high_hint)
        self.assertEqual(high_hint["processCount"], 3)
        critical_hint = _hint(summary, "ANR_PROCESS_CPU_CRITICAL")
        self.assertIsNotNone(critical_hint)
        self.assertTrue(critical_hint["requiresMemoryCorrelation"])
        self.assertEqual(critical_hint["process"]["processName"], "com.tcl.android.launcher")
        self.assertIn("目标进程", critical_hint["message"])

    def test_target_cpu_at_or_below_85_does_not_emit_app_overload_hint(self) -> None:
        lines = [
            "04-22 02:34:38.785  1485  7148 I AnrManager: ANR in com.demo",
            "04-22 02:34:38.785  1485  7148 I AnrManager:   85% 6039/com.demo: 60% user + 25% kernel",
            "04-22 02:34:38.785  1485  7148 I AnrManager: 70% TOTAL: 43% user + 26% kernel",
        ]

        summary = parse_anrmanager_block(lines)

        self.assertIsNone(_hint(summary, "ANR_PROCESS_CPU_HIGH"))

    def test_cpu_saturation_hint(self) -> None:
        lines = [
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: CPU usage from 6404ms to 0ms ago:",
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager:   80% 9999/badapp: 60% user + 20% kernel",
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: 95% TOTAL: 70% user + 25% kernel",
        ]
        summary = parse_anrmanager_block(lines)
        hint = _hint(summary, "SYSTEM_CPU_SATURATED")
        self.assertIsNotNone(hint)
        self.assertEqual(hint["confidence"], "strong")
        self.assertEqual(hint["topProcess"]["processName"], "badapp")
        self.assertIn("整机/任务负载重", hint["message"])

    def test_io_pressure_hint(self) -> None:
        lines = [
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: 45% TOTAL: 10% user + 10% kernel + 25% iowait",
        ]
        summary = parse_anrmanager_block(lines)
        hint = _hint(summary, "SYSTEM_IO_PRESSURE")
        self.assertIsNotNone(hint)
        self.assertEqual(hint["iowaitPct"], 25.0)

    def test_memory_pressure_hint(self) -> None:
        lines = [
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: some avg10=35.0 avg60=20.0 avg300=15.0 total=99",
        ]
        summary = parse_anrmanager_block(lines)
        hint = _hint(summary, "SYSTEM_MEMORY_PRESSURE")
        self.assertIsNotNone(hint)
        self.assertEqual(hint["psiSome"]["avg10"], 35.0)

    def test_low_signal_block_emits_no_hints(self) -> None:
        lines = [
            "07-02 10:14:00.882  1000  1674  2963 I AnrManager: 5% TOTAL: 3% user + 2% kernel + 0% iowait",
        ]
        summary = parse_anrmanager_block(lines)
        self.assertEqual(summary["derivedHints"], [])

    def test_anr_reason_classification_variants(self) -> None:
        reasons_to_types = {
            "Input dispatching timed out": "input_dispatching_timeout",
            "no focused window": "no_focus_window",
            "Broadcast of Intent": "broadcast_timeout",
            "Executing service com.foo/.Bar": "service_timeout",
            "Provider com.x.provider not responding": "provider_timeout",
            "something exotic": "unknown",
        }
        for reason, expected in reasons_to_types.items():
            lines = [
                f"07-02 10:14:00.883  1000  1674  2963 I AnrManager: dumpAnrDebugInfo end: AnrDumpRecord{{ {reason} ProcessRecord{{42:com.demo/u0a99}} }}",
            ]
            summary = parse_anrmanager_block(lines)
            hint = _hint(summary, "ANR_REASON_CLASSIFIED")
            self.assertIsNotNone(hint, f"no classified hint for reason={reason!r}")
            self.assertEqual(hint["anrType"], expected, f"reason={reason!r}")

    def test_empty_block_returns_well_formed_dict(self) -> None:
        summary = parse_anrmanager_block([])
        self.assertEqual(summary["anrReason"], None)
        self.assertEqual(summary["cpuTopProcesses"], [])
        self.assertEqual(summary["derivedHints"], [])


if __name__ == "__main__":
    unittest.main()
