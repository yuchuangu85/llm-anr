from __future__ import annotations

import unittest

from anr_evidence import AiContextOptions, build_ai_context, extract_evidence_package, infer_anr_type, parse_anrmanager_block, strategy_for_package
from anr_evidence.constants import SUPPORTED_TYPES


def _package(reason: str, *, trace: str = "", logcat_extra: str = "", kernel: str = "", meminfo: str = "") -> dict:
    sources = {
        "event_log": {"path": "events.log", "content": f"04-12 10:00:00.000 am_anr ANR in com.demo: {reason}"},
        "trace": {"path": "trace.txt", "content": trace or f"04-12 10:00:00.100 main tid=1 Blocked in {reason}"},
        "logcat": {"path": "logcat.txt", "content": f"04-12 10:00:00.050 E ActivityManager {reason} {logcat_extra}"},
        "kernel_log": {"path": "kernel.txt", "content": kernel or "04-12 10:00:00.060 sched: normal"},
    }
    if meminfo:
        sources["meminfo"] = {"path": "meminfo.txt", "content": meminfo}
    return {"package_id": "EXPANSION", "provided_type": None, "sources": sources}


class AnrTypeExpansionTests(unittest.TestCase):
    def test_supported_trigger_types_include_expansion_plan(self) -> None:
        for anr_type in (
            "broadcast_timeout",
            "service_timeout",
            "content_provider_timeout",
            "job_scheduler_timeout",
            "system_watchdog_swt",
        ):
            self.assertIn(anr_type, SUPPORTED_TYPES)
            strategy = strategy_for_package({"provided_type": anr_type, "sources": {}})
            self.assertEqual(strategy.anr_type, anr_type)
            self.assertGreater(strategy.event_before_seconds, 0)
            self.assertTrue(strategy.analysis_focus)

    def test_trigger_type_patterns_infer_new_types(self) -> None:
        cases = {
            "BroadcastQueue Timeout of Broadcast of Intent": "broadcast_timeout",
            "Timeout executing service com.demo/.SyncService": "service_timeout",
            "timeout publishing content providers com.demo": "content_provider_timeout",
            "JobService timeout in onStartJob": "job_scheduler_timeout",
            "system_server Watchdog timeout detected": "system_watchdog_swt",
        }
        for reason, expected in cases.items():
            with self.subTest(reason=reason):
                package = _package(reason)
                self.assertEqual(infer_anr_type(package), expected)
                extracted = extract_evidence_package(package)
                self.assertEqual(extracted["classification"]["triggerType"], expected)
                self.assertEqual(extracted["classification"]["detectedType"], expected)
                self.assertTrue(extracted["classification"]["supported"])

    def test_root_cause_pattern_hints_are_additive_and_do_not_replace_trigger(self) -> None:
        package = _package(
            "Timeout executing service com.demo/.SyncService",
            trace="04-12 10:00:00.100 main tid=1 Blocked\n- waiting to lock <0x1> held by tid=2\nworker tid=2 Blocked\n- waiting to lock <0x2> held by tid=1",
            logcat_extra="Long monitor contention observed",
        )
        extracted = extract_evidence_package(package)

        self.assertEqual(extracted["classification"]["triggerType"], "service_timeout")
        self.assertIn("deadlock", extracted["classification"]["rootCausePatternHints"])
        self.assertNotEqual(extracted["classification"]["rootCausePatternHints"], ["service_timeout"])

    def test_unknown_trigger_can_still_emit_memory_and_high_load_hints(self) -> None:
        package = _package(
            "unknown reason",
            trace="04-12 10:00:00.100 main tid=1 Runnable under high load",
            logcat_extra="CPU usage from 5000ms to 0ms ago: 95% TOTAL; PSI memory.some avg10=35; Out of memory",
            kernel="04-12 10:00:00.060 lowmemorykiller: kill com.other\n04-12 10:00:00.061 sched: high load",
        )
        extracted = extract_evidence_package(package)

        self.assertEqual(extracted["classification"]["triggerType"], "unknown")
        self.assertFalse(extracted["classification"]["supported"])
        self.assertEqual(extracted["classification"]["fallbackMode"], "unknown_type")
        self.assertIn("memory_leak_oom_pressure", extracted["classification"]["rootCausePatternHints"])
        self.assertIn("high_load_anr", extracted["classification"]["rootCausePatternHints"])

    def test_ai_context_surfaces_group_root_cause_hints_as_candidates(self) -> None:
        package = _package(
            "Input dispatching timed out",
            trace="04-12 10:00:00.100 main tid=1 Blocked\n- waiting to lock <0x1> held by tid=2\nworker tid=2 Blocked\n- waiting to lock <0x2> held by tid=1",
            logcat_extra="CPU usage from 5000ms to 0ms ago: 95% TOTAL",
        )
        result = build_ai_context(package, AiContextOptions(anr_type="input_dispatching_timeout"))

        self.assertIn("deadlock", result.groups[0]["rootCausePatternHints"])
        self.assertIn("high_load_anr", result.groups[0]["rootCausePatternHints"])
        self.assertIn("Root-cause pattern hints (candidate only)", result.cache_markdown)
        self.assertIn("非最终根因", result.cache_markdown)
        self.assertIn("## 根因模式提示约束", result.ai_prompt_markdown)

    def test_anrmanager_reason_classifies_expanded_types(self) -> None:
        cases = {
            "BroadcastQueue Timeout of Broadcast of Intent": "broadcast_timeout",
            "Timeout executing service com.demo/.SyncService": "service_timeout",
            "timeout publishing content providers": "content_provider_timeout",
            "JobService timeout in onStartJob": "job_scheduler_timeout",
            "Watchdog timeout in system_server": "system_watchdog_swt",
        }
        for reason, expected in cases.items():
            with self.subTest(reason=reason):
                summary = parse_anrmanager_block([
                    "04-12 10:00:00.000  1000  1674  2963 I AnrManager: ANR in com.demo",
                    f"04-12 10:00:00.001  1000  1674  2963 I AnrManager: Reason: {reason}",
                ])
                classified = next(h for h in summary["derivedHints"] if h["id"] == "ANR_REASON_CLASSIFIED")
                self.assertEqual(classified.get("triggerType", classified["anrType"]), expected)


if __name__ == "__main__":
    unittest.main()
