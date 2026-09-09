from __future__ import annotations

from datetime import datetime
import unittest

from anr_evidence.cross_source_fusion import FusionScope
from anr_evidence.dump_quality import audit_dump_quality
from anr_evidence.input_lifecycle import (
    build_input_lifecycle,
    filter_input_lifecycle_lines,
    filter_input_lifecycle_windows,
)


ANCHOR = "04-12 10:00:05.000 1000 1000 I am_anr  : [0,1234,com.demo,1,no focused window]"
TARGET_NO_FOCUS = (
    "04-12 10:00:04.900 E InputDispatcher: Application does not have a focused "
    "window for com.demo pid=1234 token=window-1"
)


def quality(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "group_id": "g",
        "target_pid": 1234,
        "target_process": "com.demo",
        "trace_pid": 1234,
        "trace_process": "com.demo",
        "anchor_timestamp": datetime(2026, 4, 12, 10, 0, 5),
        "trace_timestamp": datetime(2026, 4, 12, 10, 0, 5, 100_000),
        "before_seconds": 15,
        "after_seconds": 15,
    }
    values.update(changes)
    return audit_dump_quality(FusionScope(**values))  # type: ignore[arg-type]


def build(
    *,
    event_log: list[str] | None = None,
    logcat: list[str] | None = None,
    anrmanager: list[str] | None = None,
    dump_quality: dict[str, object] | None = None,
    anchor: str | None = ANCHOR,
) -> dict[str, object]:
    return build_input_lifecycle(
        group_id="g",
        anr_type="no_focus_window",
        anchor={
            "line": anchor,
            "timestamp": datetime(2026, 4, 12, 10, 0, 5) if anchor else None,
            "packageName": "com.demo" if anchor else None,
        }
        if anchor
        else None,
        event_log_lines=event_log or ([ANCHOR] if anchor else []),
        logcat_lines=logcat or [],
        anrmanager_lines=anrmanager or [],
        dump_quality=dump_quality or quality(),
    )


class InputLifecycleTests(unittest.TestCase):
    def test_no_focus_wms_target_and_pending_recents_are_structured_with_evidence_edges(self) -> None:
        result = build(
            logcat=[
                "04-12 10:00:03.000 I TaskAnimationManager: startRecentsAnimation handler=0x1 token=window-1",
                "04-12 10:00:03.100 I TaskAnimationManager: mRecentsAnimationStartPending=true handler=0x1 token=window-1",
                "04-12 10:00:04.000 I input_focus: [Focus entering recents_animation_input_consumer,reason=setFocusedWindow] token=window-1",
                TARGET_NO_FOCUS,
            ]
        )

        self.assertEqual(result["schemaVersion"], "input-lifecycle-v1")
        self.assertEqual(result["trigger"], {
            "status": "observed",
            "kind": "no_focus",
            "sourceEventIds": ["logcat:3"],
        })
        self.assertEqual(
            result["originalTarget"],
            {"kind": "process", "name": "com.demo", "pid": "1234", "sourceEventIds": ["eventLog:0"]},
        )
        self.assertEqual(result["blameTarget"]["kind"], "input_consumer")
        self.assertEqual(result["blameTarget"]["name"], "recents_animation_input_consumer")
        self.assertEqual(result["focusLifecycle"]["status"], "focus_absent")
        self.assertEqual(result["recentsLifecycle"]["instances"][0]["status"], "pending_at_anchor")
        self.assertTrue(all(edge["evidenceEventIds"] for edge in result["edges"]))
        self.assertFalse(result["eligibleForHighCausalConfidence"])

    def test_original_target_comes_from_actual_anchor_not_pre_anr_focus_context(self) -> None:
        result = build(
            event_log=[
                "04-12 10:00:04.000 1000 1000 I wm_focus: [Focus entering com.other/.Other]",
                ANCHOR,
            ]
        )

        self.assertEqual(result["anchor"]["sourceEventIds"], ["eventLog:1"])
        self.assertEqual(result["originalTarget"]["name"], "com.demo")
        self.assertEqual(result["originalTarget"]["pid"], "1234")

    def test_unacked_without_wms_mapping_leaves_blame_unknown(self) -> None:
        result = build(
            anchor="04-12 10:00:05.000 1000 1000 I am_anr  : [0,1234,com.demo,1,Input dispatching timed out]",
            logcat=[
                "04-12 10:00:04.900 E InputDispatcher: Input dispatching timed out "
                "(Waiting because the touched window has not finished processing) for com.demo pid=1234",
            ],
        )

        self.assertEqual(result["trigger"]["kind"], "unacked_input")
        self.assertIsNone(result["blameTarget"])
        self.assertIn("missing_wms_blame_mapping", result["gaps"])
        self.assertEqual(result["dump"]["associationStatus"], "associated")

    def test_wrong_pid_and_late_dump_are_exposed_from_dq1_but_never_promoted(self) -> None:
        wrong_pid = build(dump_quality=quality(trace_pid=9999))
        late_dump = build(dump_quality=quality(trace_timestamp=datetime(2026, 4, 12, 10, 1, 5)))

        self.assertEqual(wrong_pid["dump"]["identityStatus"], "wrong_pid")
        self.assertEqual(wrong_pid["dump"]["associationStatus"], "rejected")
        self.assertEqual(late_dump["dump"]["timeStatus"], "outside_window")
        self.assertEqual(late_dump["dump"]["associationStatus"], "rejected")
        self.assertFalse(wrong_pid["eligibleForHighCausalConfidence"])
        self.assertFalse(late_dump["eligibleForHighCausalConfidence"])

    def test_pending_focus_with_embedded_pid_is_observed_but_not_promoted_to_blame(self) -> None:
        pending_anchor = "04-12 10:00:05.000 1000 1000 I am_anr  : [0,1234,com.demo,1,Input dispatching timed out]"
        result = build(
            anchor=pending_anchor,
            event_log=[pending_anchor],
            logcat=[
                "04-12 10:00:04.900 E InputDispatcher: Input dispatching timed out for com.demo pid=1234",
                "04-12 10:00:04.950 I WindowManager: mPendingFocus=Window{a1b2c3 u0 com.demo/.MainActivity} "
                "owner=ProcessRecord{abcd 1234:com.demo/u0a123} token=window-1",
            ],
        )

        pending = next(event for event in result["events"] if event["id"] == "logcat:1")
        self.assertEqual(pending["targetPids"], ["1234"])
        self.assertEqual(pending["targetProcesses"], ["com.demo"])
        self.assertEqual(pending["associationStatus"], "matched")
        self.assertEqual(result["focusLifecycle"]["status"], "pending_focus")
        self.assertEqual(result["focusLifecycle"]["eventIds"], ["logcat:1"])
        self.assertIsNone(result["blameTarget"])
        self.assertIn("pending_focus_not_blame_proof", result["gaps"])

    def test_recovery_cancel_and_suppression_require_explicit_matched_evidence(self) -> None:
        cancelled = build(
            logcat=[
                "04-12 10:00:04.900 E InputDispatcher: Input dispatching timed out for com.demo pid=1234",
                "04-12 10:00:04.950 I InputDispatcher: cancel pending input event for com.demo pid=1234",
            ]
        )
        recovered = build(
            logcat=[
                "04-12 10:00:04.900 E InputDispatcher: Input dispatching timed out for com.demo pid=1234",
                "04-12 10:00:04.950 I InputDispatcher: input dispatch recovered for com.demo pid=1234",
            ]
        )
        suppressed = build(
            logcat=[
                "04-12 10:00:04.900 E InputDispatcher: Input dispatching timed out for com.demo pid=1234",
                "04-12 10:00:04.950 I InputDispatcher: input queue suppressed for com.demo pid=1234",
            ]
        )

        self.assertEqual(cancelled["inputLifecycle"]["status"], "cancelled")
        self.assertEqual(cancelled["queueLifecycle"]["status"], "cancelled")
        self.assertEqual(recovered["inputLifecycle"]["status"], "recovered")
        self.assertEqual(recovered["queueLifecycle"]["status"], "unknown")
        self.assertEqual(suppressed["inputLifecycle"]["status"], "timed_out_at_anchor")
        self.assertEqual(suppressed["queueLifecycle"]["status"], "suppressed")

    def test_repeated_suppression_and_recovery_require_explicit_matched_events(self) -> None:
        result = build(
            logcat=[
                TARGET_NO_FOCUS,
                "04-12 10:00:04.920 I InputDispatcher: input queue suppressed for com.demo pid=1234",
                "04-12 10:00:04.930 I InputDispatcher: input queue suppressed for com.demo pid=1234",
                "04-12 10:00:04.950 I InputDispatcher: input dispatch recovered for com.demo pid=1234",
            ]
        )

        self.assertEqual(result["inputLifecycle"]["status"], "recovered")
        self.assertEqual(result["queueLifecycle"]["status"], "suppressed")
        self.assertEqual(result["queueLifecycle"]["eventIds"], ["logcat:1"])
        suppressed = [event for event in result["events"] if "queue_suppressed" in event["kinds"]]
        self.assertEqual([event["associationStatus"] for event in suppressed], ["matched", "matched"])
        self.assertTrue(any(edge["kind"] == "input_recovered" for edge in result["edges"]))

    def test_missing_lifecycle_events_remain_unknown_not_negative_claims(self) -> None:
        timeout_anchor = "04-12 10:00:05.000 1000 1000 I am_anr  : [0,1234,com.demo,1,Input dispatching timed out]"
        result = build(anchor=timeout_anchor, event_log=[timeout_anchor], logcat=[])

        self.assertEqual(result["trigger"]["status"], "observed")
        self.assertEqual(result["focusLifecycle"]["status"], "unknown")
        self.assertEqual(result["queueLifecycle"]["status"], "unknown")
        self.assertEqual(result["recentsLifecycle"]["status"], "unknown")
        self.assertNotIn("recents_finished", [edge["kind"] for edge in result["edges"]])
        self.assertNotIn("recents_cancelled", [edge["kind"] for edge in result["edges"]])
        self.assertIn("queue_lifecycle_unavailable", result["gaps"])
        self.assertIn("missing_wms_blame_mapping", result["gaps"])

    def test_lifecycle_window_keeps_system_recents_lines_without_package_text(self) -> None:
        lines = filter_input_lifecycle_lines(
            [
                "04-12 10:00:03.000 I TaskAnimationManager: startRecentsAnimation handler=0x1",
                "04-12 10:00:25.000 I TaskAnimationManager: finishRecentsAnimation handler=0x1",
                "04-12 10:00:04.000 I unrelated: ordinary log line",
            ],
            anchor_timestamp=datetime(2026, 4, 12, 10, 0, 5),
            before_seconds=5,
            after_seconds=5,
        )

        self.assertEqual(lines, ["04-12 10:00:03.000 I TaskAnimationManager: startRecentsAnimation handler=0x1"])

    def test_lifecycle_window_filter_keeps_groups_separate_in_one_scan(self) -> None:
        selected = filter_input_lifecycle_windows(
            [
                "04-12 10:00:03.000 I TaskAnimationManager: startRecentsAnimation handler=0x1",
                "04-12 10:30:03.000 I TaskAnimationManager: startRecentsAnimation handler=0x2",
            ],
            windows=[
                (datetime(2026, 4, 12, 10, 0, 5), 5, 5),
                (datetime(2026, 4, 12, 10, 30, 5), 5, 5),
            ],
        )

        self.assertEqual(len(selected[0]), 1)
        self.assertIn("handler=0x1", selected[0][0])
        self.assertEqual(len(selected[1]), 1)
        self.assertIn("handler=0x2", selected[1][0])

    def test_unknown_identity_and_unkeyed_recents_are_not_linked_or_promoted(self) -> None:
        result = build(
            anchor=None,
            event_log=[],
            logcat=[
                "04-12 10:00:04.900 E InputDispatcher: Input dispatching timed out",
                "04-12 10:00:04.950 I TaskAnimationManager: startRecentsAnimation",
                "04-12 10:00:05.100 I TaskAnimationManager: finishRecentsAnimation",
            ],
            dump_quality=quality(target_pid=None, target_process=None),
        )

        self.assertIsNone(result["originalTarget"])
        self.assertEqual(result["recentsLifecycle"]["status"], "unknown")
        self.assertIn("unkeyed_recents_events_unlinked", result["gaps"])
        self.assertFalse(result["eligibleForHighCausalConfidence"])

    def test_keyed_recents_cancel_before_anchor_is_explicitly_terminal(self) -> None:
        result = build(
            logcat=[
                "04-12 10:00:03.000 I TaskAnimationManager: startRecentsAnimation handler=0x1 token=window-1",
                "04-12 10:00:04.000 I TaskAnimationManager: cancelRecentsAnimation handler=0x1 token=window-1",
                TARGET_NO_FOCUS,
            ]
        )

        self.assertEqual(result["recentsLifecycle"]["status"], "cancelled_before_anchor")
        self.assertEqual(result["recentsLifecycle"]["instances"][0]["eventIds"], ["logcat:0", "logcat:1"])
        self.assertTrue(any(edge["kind"] == "recents_cancelled" for edge in result["edges"]))

    def test_recents_finish_after_anchor_is_not_recovery_at_anr(self) -> None:
        result = build(
            logcat=[
                "04-12 10:00:03.000 I TaskAnimationManager: startRecentsAnimation handler=0x1 token=window-1",
                TARGET_NO_FOCUS,
                "04-12 10:00:06.000 I TaskAnimationManager: finishRecentsAnimation handler=0x1 token=window-1",
            ]
        )

        self.assertEqual(result["recentsLifecycle"]["instances"][0]["status"], "finished_after_anchor")
        self.assertNotEqual(result["inputLifecycle"]["status"], "recovered")

    def test_foreign_trigger_cannot_override_matched_group_trigger(self) -> None:
        result = build(
            logcat=[
                TARGET_NO_FOCUS,
                "04-12 10:00:04.950 E InputDispatcher: Input dispatching timed out "
                "(Waiting because the touched window has not finished processing) for com.other pid=9876",
            ]
        )

        self.assertEqual(result["trigger"]["sourceEventIds"], ["logcat:0"])
        self.assertEqual(result["trigger"]["kind"], "no_focus")
        foreign = next(event for event in result["events"] if event["id"] == "logcat:1")
        self.assertEqual(foreign["associationStatus"], "wrong_identity")

    def test_inconsistent_explicit_pid_and_process_are_wrong_identity(self) -> None:
        result = build(
            logcat=[
                "04-12 10:00:04.900 E InputDispatcher: Input dispatching timed out "
                "for com.other pid=1234 token=window-1",
            ]
        )

        inconsistent = next(event for event in result["events"] if event["id"] == "logcat:0")
        self.assertEqual(inconsistent["associationStatus"], "wrong_identity")
        self.assertEqual(result["trigger"]["sourceEventIds"], ["eventLog:0"])

    def test_matched_post_anchor_focus_is_context_not_at_anr_blame(self) -> None:
        result = build(
            logcat=[
                TARGET_NO_FOCUS,
                "04-12 10:00:06.000 I input_focus: [Focus entering recents_animation_input_consumer,reason=setFocusedWindow] token=window-1",
            ]
        )

        self.assertIsNone(result["blameTarget"])
        self.assertNotEqual(result["focusLifecycle"]["status"], "focus_present")
        self.assertEqual(result["focusLifecycle"]["postAnchorEventIds"], ["logcat:1"])

    def test_reused_recents_key_is_segmented_into_distinct_animation_instances(self) -> None:
        result = build(
            logcat=[
                "04-12 10:00:02.000 I TaskAnimationManager: startRecentsAnimation handler=0x1 token=window-1",
                "04-12 10:00:03.000 I TaskAnimationManager: finishRecentsAnimation handler=0x1 token=window-1",
                "04-12 10:00:03.500 I TaskAnimationManager: startRecentsAnimation handler=0x1 token=window-1",
                "04-12 10:00:04.000 I TaskAnimationManager: mRecentsAnimationStartPending=true handler=0x1 token=window-1",
                TARGET_NO_FOCUS,
            ]
        )

        instances = result["recentsLifecycle"]["instances"]
        self.assertEqual([instance["status"] for instance in instances], ["finished_before_anchor", "pending_at_anchor"])
        self.assertEqual(result["recentsLifecycle"]["status"], "pending_at_anchor")

    def test_yearless_threadtime_uses_anchor_year_across_december_january_rollover(self) -> None:
        retained = filter_input_lifecycle_lines(
            ["12-31 23:59:59.000 I TaskAnimationManager: startRecentsAnimation handler=0x1"],
            anchor_timestamp=datetime(2025, 1, 1, 0, 0, 2),
            before_seconds=5,
            after_seconds=1,
        )

        self.assertEqual(len(retained), 1)


if __name__ == "__main__":
    unittest.main()
