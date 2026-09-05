"""Tests for cross-source confidence fusion."""

from __future__ import annotations

import unittest
import copy
from dataclasses import replace

from tests.helpers import load_fixture

from anr_evidence import FusionScope, build_ai_context, fuse_cross_source_evidence
from anr_evidence.log_filter import parse_log_timestamp
from anr_evidence.ai_context import AiContextOptions


def _scope(**changes):
    scope = FusionScope(
        group_id="g1", target_pid=1234, target_process="com.demo",
        trace_pid=1234, trace_process="com.demo",
        anchor_timestamp=parse_log_timestamp("04-12 10:00:05.000"),
        trace_timestamp=parse_log_timestamp("04-12 10:00:05.100"),
        before_seconds=5, after_seconds=1,
    )
    return replace(scope, **changes)


class FusionUnitTests(unittest.TestCase):
    def test_missing_invalid_or_outside_scope_never_promotes(self):
        for scope, reason in (
            (None, "missing_scope"),
            (_scope(target_pid=None), "unknown_target"),
            (_scope(trace_timestamp=None), "unknown_trace_timestamp"),
            (_scope(before_seconds=float("nan")), "invalid_window"),
            (_scope(after_seconds=float("inf")), "invalid_window"),
            (_scope(before_seconds=-1), "invalid_window"),
        ):
            with self.subTest(reason=reason, scope=scope):
                hints = [{"id": "MAIN_BINDER_WAIT_REPLY", "confidence": "strong"}]
                result = fuse_cross_source_evidence(
                    hints, scope=scope,
                    logcat_text="04-12 10:00:04.900 1234 1234 W BinderProxy: slow binder transaction",
                )
                self.assertEqual(result[0]["confidence"], "strong")
                self.assertEqual(result[0]["contextEvidence"][0]["reason"], reason)
                self.assertNotIn("contextEvidence", hints[0])
        result = fuse_cross_source_evidence(
            [{"id": "SYSTEM_IO_PRESSURE", "confidence": "strong"}], scope=_scope(),
            kernel_log_text="04-12 09:00:00.000 pid=1234 hung_task",
        )
        self.assertEqual(result[0]["confidence"], "strong")
        self.assertEqual(result[0]["contextEvidence"][0]["reason"], "outside_window")

    def test_auditable_records_are_bounded_and_do_not_mutate_hints(self):
        hints = [{"id": "MAIN_BINDER_WAIT_REPLY", "confidence": "strong"}]
        unrelated = "04-12 10:00:04.900 9999 9999 W BinderProxy: slow binder transaction"
        valid = "04-12 10:00:04.950 1234 1234 W BinderProxy: slow binder transaction"
        result = fuse_cross_source_evidence(
            hints, scope=_scope(), logcat_text="\n".join(["noise", unrelated] * 500 + [valid] * 500),
        )
        self.assertEqual(len(result[0]["contextEvidence"]), 1)
        self.assertEqual(len(result[0]["corroboratingEvidence"]), 1)
        record = result[0]["corroboratingEvidence"][0]
        self.assertEqual((record["rawLine"], record["lineIndex"], record["groupId"]), (valid, 1000, "g1"))
        self.assertEqual(record["lineIndexScope"], "supplied_source_view")
        self.assertEqual(hints, [{"id": "MAIN_BINDER_WAIT_REPLY", "confidence": "strong"}])

    def test_yearless_corroboration_uses_explicit_anchor_year(self):
        scope = _scope(
            anchor_timestamp=parse_log_timestamp("2020-04-12 10:00:05.000"),
            trace_timestamp=parse_log_timestamp("2020-04-12 10:00:05.100"),
        )
        result = fuse_cross_source_evidence(
            [{"id": "MAIN_BINDER_WAIT_REPLY", "confidence": "strong"}], scope=scope,
            logcat_text="04-12 10:00:04.900 1234 1234 W BinderProxy: slow binder transaction",
        )
        self.assertEqual(result[0]["confidence"], "critical")
        self.assertEqual(result[0]["corroboratingEvidence"][0]["deltaTSeconds"], -0.1)

    def test_promotes_strong_to_critical_for_binder_with_slow_log(self) -> None:
        hints = [{"id": "MAIN_BINDER_WAIT_REPLY", "confidence": "strong"}]
        promoted = fuse_cross_source_evidence(
            hints,
            scope=_scope(),
            logcat_text="04-12 10:00:05.050 1234 1234 W BinderProxy: slow binder transaction took 5000ms",
        )
        self.assertEqual(promoted[0]["confidence"], "critical")
        self.assertEqual(promoted[0]["confidencePromotedFrom"], "strong")
        self.assertEqual(len(promoted[0]["corroboratingEvidence"]), 1)
        self.assertEqual(promoted[0]["corroboratingEvidence"][0]["source"], "logcat")

    def test_promotes_weak_to_strong_for_native_poll_busy_with_choreographer(self) -> None:
        hints = [{"id": "NATIVE_POLL_BUT_BUSY", "confidence": "weak"}]
        promoted = fuse_cross_source_evidence(
            hints,
            scope=_scope(),
            logcat_text="04-12 10:00:05.050 1234 1234 I Choreographer: Skipped 35 frames!",
        )
        self.assertEqual(promoted[0]["confidence"], "strong")

    def test_no_promotion_without_evidence(self) -> None:
        hints = [{"id": "MAIN_BINDER_WAIT_REPLY", "confidence": "strong"}]
        promoted = fuse_cross_source_evidence(hints, logcat_text="random unrelated log")
        self.assertEqual(promoted[0]["confidence"], "strong")
        self.assertNotIn("confidencePromotedFrom", promoted[0])
        self.assertNotIn("corroboratingEvidence", promoted[0])

    def test_kernel_pressure_without_clock_mapping_is_only_context(self) -> None:
        hints = [{"id": "SYSTEM_IO_PRESSURE", "confidence": "strong"}]
        promoted = fuse_cross_source_evidence(
            hints,
            scope=_scope(),
            kernel_log_text="[ 1234.567] INFO: task jbd2/dm-0:567 blocked for more than 120 seconds. hung_task_timeout_secs",
        )
        self.assertEqual(promoted[0]["confidence"], "strong")
        self.assertNotIn("corroboratingEvidence", promoted[0])
        self.assertEqual(promoted[0]["contextEvidence"][0]["reason"], "unknown_timestamp")

    def test_critical_does_not_overflow(self) -> None:
        hints = [{"id": "DEADLOCK_CYCLE", "confidence": "critical"}]
        promoted = fuse_cross_source_evidence(
            hints,
            scope=_scope(target_process="system_server", trace_process="system_server"),
            logcat_text="04-12 10:00:05.050 1234 1234 W Watchdog: system_server",
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
            scope=_scope(),
            logcat_text="04-12 10:00:05.050 1234 1234 I art: Background concurrent copying GC freed 12345 objects",
        )
        # Only the GC hint should be promoted
        self.assertEqual(promoted[0]["confidence"], "strong")
        self.assertEqual(promoted[1]["confidence"], "critical")


class FusionIntegrationTests(unittest.TestCase):
    """End-to-end via build_ai_context: fusion fires inside the pipeline."""

    def test_legacy_anchor_uses_matching_anrmanager_target_identity(self):
        package = load_fixture("eval/eval_fusion_binder_critical.json")["package"]
        package["sources"]["event_log"]["content"] = (
            "04-12 10:00:05.000 am_anr ANR in com.demo: Input dispatching timed out"
        )
        package["sources"]["logcat"]["content"] += (
            "\n04-12 10:00:05.050 999 999 I AnrManager: dumpAnrDebugInfo end: "
            "AnrDumpRecord{ Input dispatching timed out ProcessRecord{1234:com.demo/u0a123} }"
        )
        result = build_ai_context(package, AiContextOptions(package_name="com.demo"))
        hint = next(h for h in result.groups[0]["trace"]["traceHints"]
                    if h["id"] == "MAIN_BINDER_WAIT_REPLY")
        self.assertEqual(hint["confidence"], "critical")

    def test_anrmanager_identity_conflict_blocks_promotion(self):
        package = load_fixture("eval/eval_fusion_binder_critical.json")["package"]
        package["sources"]["logcat"]["content"] += (
            "\n04-12 10:00:05.050 999 999 I AnrManager: dumpAnrDebugInfo end: "
            "AnrDumpRecord{ Input dispatching timed out ProcessRecord{5678:com.demo/u0a123} }"
        )
        result = build_ai_context(package, AiContextOptions(package_name="com.demo"))
        hint = next(h for h in result.groups[0]["trace"]["traceHints"]
                    if h["id"] == "MAIN_BINDER_WAIT_REPLY")
        self.assertEqual(hint["confidence"], "strong")
        self.assertEqual(hint["contextEvidence"][0]["reason"], "conflicting_target_identity")
        self.assertIn("ProcessRecord{5678:com.demo", "\n".join(result.groups[0]["logcat"]["lines"]))

    def test_legacy_anchor_does_not_borrow_unrelated_anrmanager_identity(self):
        for timestamp, process in (("10:00:05.050", "com.demo.other"),
                                   ("10:01:05.050", "com.demo")):
            with self.subTest(timestamp=timestamp, process=process):
                package = load_fixture("eval/eval_fusion_binder_critical.json")["package"]
                package["sources"]["event_log"]["content"] = (
                    "04-12 10:00:05.000 am_anr ANR in com.demo: Input dispatching timed out"
                )
                package["sources"]["logcat"]["content"] += (
                    f"\n04-12 {timestamp} 999 999 I AnrManager: dumpAnrDebugInfo end: "
                    f"AnrDumpRecord{{ Input dispatching timed out ProcessRecord{{1234:{process}/u0a123}} }}"
                )
                result = build_ai_context(package, AiContextOptions(package_name="com.demo"))
                hint = next(h for h in result.groups[0]["trace"]["traceHints"]
                            if h["id"] == "MAIN_BINDER_WAIT_REPLY")
                self.assertEqual(hint["confidence"], "strong")


    def test_misleading_same_window_signals_do_not_promote(self):
        base = load_fixture("eval/eval_fusion_binder_critical.json")["package"]
        base["sources"]["trace"]["content"] = base["sources"]["trace"]["content"].replace("pid 100", "pid 1234").replace("sysTid=100", "sysTid=1234")
        cases = {
            "other_emitter": "04-12 10:00:04.900 9999 9999 W BinderProxy: slow binder transaction took 5000ms",
            "tid_only": "04-12 10:00:04.900 9999 1234 W BinderProxy: slow binder transaction took 5000ms",
            "bare_number": "04-12 10:00:04.900 W BinderProxy: slow binder transaction took 1234ms for com.demo",
            "unknown_identity": "04-12 10:00:04.900 W BinderProxy: slow binder transaction took 5000ms",
            "conflicting_target": "04-12 10:00:04.900 1234 1234 W BinderProxy: slow binder transaction pid=9999",
        }
        for name, line in cases.items():
            with self.subTest(name=name):
                package = copy.deepcopy(base)
                package["sources"]["logcat"]["content"] = line
                result = build_ai_context(package, AiContextOptions(package_name="com.demo"))
                hint = next(h for h in result.groups[0]["trace"]["traceHints"] if h["id"] == "MAIN_BINDER_WAIT_REPLY")
                self.assertEqual(hint["confidence"], "strong")
                self.assertNotIn("confidencePromotedFrom", hint)

    def test_wrong_or_stale_dump_does_not_promote(self):
        for wrong_pid, stale in ((True, False), (False, True)):
            with self.subTest(wrong_pid=wrong_pid, stale=stale):
                package = load_fixture("eval/eval_fusion_binder_critical.json")["package"]
                package["sources"]["trace"]["content"] = package["sources"]["trace"]["content"].replace("pid 1234", "pid 100")
                if not wrong_pid:
                    package["sources"]["trace"]["content"] = package["sources"]["trace"]["content"].replace("pid 100", "pid 1234")
                if stale:
                    package["sources"]["trace"]["content"] = package["sources"]["trace"]["content"].replace("10:00:05.100", "09:00:05.100")
                package["sources"]["logcat"]["content"] = "04-12 10:00:04.900 1234 1234 W BinderProxy: slow binder transaction took 5000ms"
                result = build_ai_context(package, AiContextOptions(package_name="com.demo"))
                hint = next(h for h in result.groups[0]["trace"]["traceHints"] if h["id"] == "MAIN_BINDER_WAIT_REPLY")
                self.assertEqual(hint["confidence"], "strong")
                self.assertNotIn("confidencePromotedFrom", hint)

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
                        "04-12 10:00:05.100 ----- pid 1234 -----",
                        "Cmd line: com.demo",
                        '"main" prio=5 tid=1 Native',
                        '  | sysTid=1234',
                        '  | state=S schedstat=( 50000000 800000000 200 ) utm=2 stm=3 core=0 HZ=100',
                        '  native: #00 pc 0  /system/lib/libbinder.so (android::IPCThreadState::waitForResponse+8)',
                        '  at android.os.BinderProxy.transact(BinderProxy.java:550)',
                    ]),
                },
                "logcat": {
                    "path": "logcat.txt",
                    "content": "\n".join([
                        "04-12 10:00:04.900 1234 1234 W BinderProxy: slow binder transaction took 5000ms",
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
        self.assertNotIn("除非证据极其充分", prompt)
        self.assertNotIn("可直接作为根因", prompt)
        self.assertNotIn("大概率为内存泄漏", prompt)
        self.assertIn("CPU/RSS 偏高或一次 GC 等待不能单独证明内存泄漏", prompt)


if __name__ == "__main__":
    unittest.main()
