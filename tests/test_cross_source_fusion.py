"""Tests for cross-source confidence fusion."""

from __future__ import annotations

import unittest

from anr_evidence import build_ai_context, fuse_cross_source_evidence
from anr_evidence.ai_context import AiContextOptions


class FusionUnitTests(unittest.TestCase):
    def test_promotes_strong_to_critical_for_binder_with_slow_log(self) -> None:
        hints = [{"id": "MAIN_BINDER_WAIT_REPLY", "confidence": "strong"}]
        promoted = fuse_cross_source_evidence(
            hints,
            logcat_text="04-12 10:00:05.050 W BinderProxy slow binder transaction took 5000ms",
        )
        self.assertEqual(promoted[0]["confidence"], "critical")
        self.assertEqual(promoted[0]["confidencePromotedFrom"], "strong")
        self.assertEqual(len(promoted[0]["corroboratingEvidence"]), 1)
        self.assertEqual(promoted[0]["corroboratingEvidence"][0]["source"], "logcat")

    def test_promotes_weak_to_strong_for_native_poll_busy_with_choreographer(self) -> None:
        hints = [{"id": "NATIVE_POLL_BUT_BUSY", "confidence": "weak"}]
        promoted = fuse_cross_source_evidence(
            hints,
            logcat_text="04-12 10:00:05.050 I Choreographer Skipped 35 frames!",
        )
        self.assertEqual(promoted[0]["confidence"], "strong")

    def test_no_promotion_without_evidence(self) -> None:
        hints = [{"id": "MAIN_BINDER_WAIT_REPLY", "confidence": "strong"}]
        promoted = fuse_cross_source_evidence(hints, logcat_text="random unrelated log")
        self.assertEqual(promoted[0]["confidence"], "strong")
        self.assertNotIn("confidencePromotedFrom", promoted[0])
        self.assertNotIn("corroboratingEvidence", promoted[0])

    def test_kernel_log_promotes_io_pressure(self) -> None:
        hints = [{"id": "SYSTEM_IO_PRESSURE", "confidence": "strong"}]
        promoted = fuse_cross_source_evidence(
            hints,
            kernel_log_text="[ 1234.567] INFO: task jbd2/dm-0:567 blocked for more than 120 seconds. hung_task_timeout_secs",
        )
        self.assertEqual(promoted[0]["confidence"], "critical")
        self.assertEqual(promoted[0]["corroboratingEvidence"][0]["source"], "kernelLog")

    def test_critical_does_not_overflow(self) -> None:
        hints = [{"id": "DEADLOCK_CYCLE", "confidence": "critical"}]
        promoted = fuse_cross_source_evidence(
            hints,
            logcat_text="04-12 10:00:05.050 W Watchdog system_server",
        )
        # Already critical -> stays critical (and gets a record of the corroboration)
        self.assertEqual(promoted[0]["confidence"], "critical")
        self.assertIn("corroboratingEvidence", promoted[0])

    def test_unrelated_hint_id_is_passthrough(self) -> None:
        hints = [{"id": "DOES_NOT_EXIST", "confidence": "weak"}]
        promoted = fuse_cross_source_evidence(hints, logcat_text="anything")
        self.assertEqual(promoted, hints)

    def test_multiple_hints_handled_independently(self) -> None:
        hints = [
            {"id": "MAIN_BINDER_WAIT_REPLY", "confidence": "strong"},
            {"id": "MAIN_GC_PAUSED", "confidence": "strong"},
        ]
        promoted = fuse_cross_source_evidence(
            hints,
            logcat_text="04-12 10:00:05.050 I art Background concurrent copying GC freed 12345 objects",
        )
        # Only the GC hint should be promoted
        self.assertEqual(promoted[0]["confidence"], "strong")
        self.assertEqual(promoted[1]["confidence"], "critical")


class FusionIntegrationTests(unittest.TestCase):
    """End-to-end via build_ai_context: fusion fires inside the pipeline."""

    def test_binder_wait_promoted_through_full_pipeline(self) -> None:
        package = {
            "package_id": "EVAL-FUSION-1",
            "sources": {
                "event_log": {
                    "path": "events.log",
                    "content": "04-12 10:00:05.000 1234 1234 I am_anr  : [0,1234,com.demo,1,Input dispatching timed out]",
                },
                "trace": {
                    "path": "trace.txt",
                    "content": "\n".join([
                        "04-12 10:00:05.100 ----- pid 100 -----",
                        "Cmd line: com.demo",
                        '"main" prio=5 tid=1 Native',
                        '  | sysTid=100',
                        '  | state=S schedstat=( 50000000 800000000 200 ) utm=2 stm=3 core=0 HZ=100',
                        '  native: #00 pc 0  /system/lib/libbinder.so (android::IPCThreadState::waitForResponse+8)',
                        '  at android.os.BinderProxy.transact(BinderProxy.java:550)',
                    ]),
                },
                "logcat": {
                    "path": "logcat.txt",
                    "content": "\n".join([
                        "04-12 10:00:04.900 W BinderProxy slow binder transaction took 5000ms",
                        "04-12 10:00:05.000 E InputDispatcher Application is not responding",
                    ]),
                },
            },
        }
        result = build_ai_context(package, AiContextOptions(package_name="com.demo"))
        # Find the trace hint and verify it was promoted
        trace_hints = []
        for group in result.groups:
            trace_hints.extend(group["trace"].get("traceHints", []))
        binder_hints = [h for h in trace_hints if h["id"] == "MAIN_BINDER_WAIT_REPLY"]
        self.assertTrue(binder_hints, "MAIN_BINDER_WAIT_REPLY should fire")
        self.assertEqual(binder_hints[0]["confidence"], "critical")
        self.assertEqual(binder_hints[0]["confidencePromotedFrom"], "strong")
        self.assertTrue(binder_hints[0]["corroboratingEvidence"])
        self.assertEqual(binder_hints[0]["corroboratingEvidence"][0]["source"], "logcat")


class JsonSchemaPromptTests(unittest.TestCase):
    """Verify the AI prompt advertises the structured JSON tail."""

    def test_prompt_contains_json_schema_block(self) -> None:
        package = {
            "package_id": "EVAL-JSON-PROMPT",
            "sources": {
                "event_log": {
                    "path": "events.log",
                    "content": "04-12 10:00:05.000 1234 1234 I am_anr  : [0,1234,com.demo,1,Input dispatching timed out]",
                },
                "trace": {"path": "trace.txt", "content": "04-12 10:00:05.100 ----- pid 100 -----\n"},
                "logcat": {"path": "logcat.txt", "content": ""},
            },
        }
        result = build_ai_context(package, AiContextOptions(package_name="com.demo"))
        prompt = result.ai_prompt_markdown
        self.assertIn("必需输出 — 结构化 JSON 尾部", prompt)
        self.assertIn('"primaryRootCauseHintId"', prompt)
        self.assertIn('"candidateChains"', prompt)
        self.assertIn('"finalJudgment"', prompt)


if __name__ == "__main__":
    unittest.main()
