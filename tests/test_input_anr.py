from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from anr_evidence import AiContextOptions, build_ai_context_artifacts, load_package_from_fixture
from anr_evidence.ai_context import _merge_analyses
from anr_evidence.input_anr import build_input_anr, render_input_anr_sections
from anr_evidence.reconstruction import build_reconstruction, refresh_reconstruction, render_reconstruction_markdown
from anr_evidence.reconstruction_ai import INPUT_CLAIM_FIELDS, attach_ai_analysis


REASON = (
    "04-12 10:00:05.000 am_anr ANR in com.demo: Input dispatching timed out "
    "(windowToken=w1 connectionToken=c1 is not responding. Waited 6001ms for "
    "MotionEvent(eventId=0x123, action=ACTION_DOWN, deviceId=4, source=0x1002, displayId=0))"
)


def _group(reason: str = REASON, lines: list[str] | None = None, anr_type: str = "input_dispatching_timeout") -> dict:
    return {
        "id": "anr-input", "strategy": {"anrType": anr_type},
        "anchor": {"line": reason, "timestamp": "04-12 10:00:05.000", "sourceKind": "event_log"},
        "eventLog": {"lines": [reason]}, "logcat": {"lines": lines or []},
    }


class InputAnrTests(unittest.TestCase):
    def test_reason_identifies_reported_motion_without_claiming_first_expiry(self) -> None:
        report = build_reconstruction(_group())
        details = report["inputAnr"]
        self.assertEqual(details["branch"], "ack_timeout")
        self.assertEqual(details["eventIdentificationStatus"], "reason_event_identified")
        event = details["reportedEvents"][0]
        identity = event["identity"]
        for name, value in {
            "eventType": "MotionEvent", "inputEventId": "0x123", "action": "ACTION_DOWN",
            "deviceId": "4", "source": "0x1002", "displayId": "0",
            "windowToken": "w1", "connectionToken": "c1", "reportedWaitMs": 6001,
        }.items():
            self.assertEqual(identity[name], value, name)
        for name in ("nativeSeq", "reportedSeq", "effectiveTimeoutMs", "deliveryTime", "deadline"):
            self.assertIsNone(identity[name], name)
        self.assertIsNone(details["triggeringEvent"])
        self.assertEqual(event["evidenceRefs"], [report["events"][0]["eventId"]])
        self.assertFalse(details["finalJudgment"])
        self.assertTrue(details["notRootCauseYet"])

    def test_key_reason_gives_only_the_observed_action(self) -> None:
        reason = REASON.replace("MotionEvent(eventId=0x123, action=ACTION_DOWN", "KeyEvent(eventId=0x123, action=ACTION_UP, keyCode=KEYCODE_BACK")
        details = build_reconstruction(_group(reason))["inputAnr"]
        action = details["simulationSteps"][1]["summary"]
        self.assertIn("ACTION_UP", action)
        self.assertIn("KEYCODE_BACK", action)
        self.assertIn("时序待补", action)

    def test_incomplete_first_event_does_not_borrow_fields_from_second(self) -> None:
        reason = REASON.replace(
            "MotionEvent(eventId=0x123, action=ACTION_DOWN, deviceId=4, source=0x1002, displayId=0)",
            "MotionEvent(action=ACTION_DOWN) followed by KeyEvent(eventId=other, keyCode=KEYCODE_HOME)",
        )
        identity = build_reconstruction(_group(reason))["inputAnr"]["reportedEvents"][0]["identity"]
        self.assertEqual(identity["eventType"], "MotionEvent")
        self.assertEqual(identity["action"], "ACTION_DOWN")
        self.assertIsNone(identity["inputEventId"])
        self.assertIsNone(identity["keyCode"])

    def test_sequence_and_timing_do_not_leak_from_second_event(self) -> None:
        reason = REASON.replace(
            "MotionEvent(eventId=0x123, action=ACTION_DOWN, deviceId=4, source=0x1002, displayId=0)",
            "MotionEvent(action=ACTION_DOWN) followed by KeyEvent(nativeSeq=99, seq=41, deliveryTime=100ns, deadline=600ns)",
        )
        identity = build_reconstruction(_group(reason))["inputAnr"]["reportedEvents"][0]["identity"]
        for name in ("nativeSeq", "reportedSeq", "deliveryTime", "deadline"):
            self.assertIsNone(identity[name], name)
        self.assertEqual(identity["seqDomain"], "unknown")

    def test_nested_pointer_index_keeps_complete_event_identity(self) -> None:
        reason = REASON.replace("action=ACTION_DOWN", "action=POINTER_DOWN(1)")
        identity = build_reconstruction(_group(reason))["inputAnr"]["reportedEvents"][0]["identity"]
        self.assertEqual(identity["action"], "POINTER_DOWN(1)")
        self.assertEqual(identity["inputEventId"], "0x123")
        self.assertEqual(identity["deviceId"], "4")
        self.assertEqual(identity["displayId"], "0")

    def test_focus_wait_fallback_does_not_become_an_anr_report(self) -> None:
        wait = "04-12 10:00:05.000 InputDispatcher: Waiting because no window has focus. Will wait for 6000ms"
        report = build_reconstruction(_group(wait, anr_type="no_focus_window"))
        self.assertEqual(report["events"][0]["eventType"], "focus_wait_started")
        anr = next(step for step in report["inputAnr"]["anrModel"] if "reason / 上报" in step["summary"])
        self.assertEqual(anr["certainty"], "unknown")
        self.assertEqual(anr["supportingEventIds"], [])

    def test_explicit_timing_fields_are_preserved_but_not_calculated(self) -> None:
        details = build_reconstruction(_group(REASON + " deliveryTime=100000ns timeoutTime=6100000ns dispatchingTimeout=6000ms"))["inputAnr"]
        identity = details["reportedEvents"][0]["identity"]
        self.assertEqual(identity["deliveryTime"], "100000ns")
        self.assertEqual(identity["deadline"], "6100000ns")
        self.assertEqual(identity["effectiveTimeoutMs"], 6000)
        self.assertIsNone(details["triggeringEvent"])

    def test_reason_wait_and_generic_seq_do_not_create_dispatch_timing_or_native_seq(self) -> None:
        details = build_reconstruction(_group(REASON + " seq=42"))["inputAnr"]
        identity = details["reportedEvents"][0]["identity"]
        self.assertEqual(identity["reportedSeq"], "42")
        self.assertEqual(identity["seqDomain"], "unknown")
        self.assertIsNone(identity["nativeSeq"])
        self.assertIsNone(identity["deadline"])
        self.assertIsNone(identity["effectiveTimeoutMs"])

    def test_explicit_native_seq_is_not_java_sequence(self) -> None:
        details = build_reconstruction(_group(REASON + " nativeSeq=8 seq=42"))["inputAnr"]
        identity = details["reportedEvents"][0]["identity"]
        self.assertEqual(identity["nativeSeq"], "8")
        self.assertEqual(identity["reportedSeq"], "42")
        self.assertEqual(identity["seqDomain"], "native")

    def test_no_focus_model_does_not_assume_published_input(self) -> None:
        reason = "04-12 10:00:05.000 am_anr ANR in com.demo: Input dispatching timed out (Application does not have a focused window)"
        details = build_reconstruction(_group(reason))["inputAnr"]
        self.assertEqual(details["branch"], "no_focused_window")
        self.assertEqual(details["eventIdentificationStatus"], "event_unspecified")
        self.assertTrue(all("FINISHED" not in step["summary"] for step in details["anrModel"]))
        self.assertIn("不是已发布事件未收到 ACK", "\n".join(render_input_anr_sections(details)))

    def test_unrelated_input_and_other_anr_cannot_become_target_event(self) -> None:
        reason = "04-12 10:00:05.000 am_anr ANR in com.demo: Input dispatching timed out"
        other_reason = REASON.replace("com.demo:", "com.other:")
        same_process_other_anr = REASON.replace("10:00:05.000", "10:00:04.000")
        details = build_reconstruction(_group(reason, [
            "04-12 10:00:04.000 InputDispatcher: MotionEvent(action=ACTION_UP) seq=3",
            other_reason, same_process_other_anr,
        ]))["inputAnr"]
        self.assertEqual(details["branch"], "unknown")
        self.assertEqual(details["eventIdentificationStatus"], "event_unspecified")
        self.assertFalse(any("com.other" in item["rawReason"] for item in details["reportedEvents"]))
        candidate = next(item for item in details["reportedEvents"] if "MotionEvent" in item["rawReason"])
        self.assertEqual(candidate["association"], "unverified")

    def test_missing_reason_does_not_invent_event_or_guarantee_reproduction(self) -> None:
        details = build_input_anr("input_dispatching_timeout", {}, [])
        self.assertEqual(details["branch"], "unknown")
        self.assertEqual(details["reportedEvents"], [])
        self.assertTrue(all(step["certainty"] == "unknown" for step in details["anrModel"]))
        markdown = "\n".join(render_input_anr_sections(details))
        for text in ("无法确定具体超时输入事件", "不保证复现", "executed=false", "待补参数"):
            self.assertIn(text, markdown)
        self.assertNotIn("adb shell", markdown)
        self.assertNotIn("sleep(5)", markdown)

    def test_non_input_does_not_get_a_simulation_plan(self) -> None:
        self.assertIsNone(build_input_anr("broadcast_timeout", {}, []))
        self.assertIsNone(build_input_anr("unknown", {}, []))
        self.assertEqual(render_input_anr_sections(None), [])

    def test_input_reason_classifies_even_if_strategy_unknown(self) -> None:
        details = build_reconstruction(_group(anr_type="unknown"))["inputAnr"]
        self.assertEqual(details["branch"], "ack_timeout")

    def test_model_is_not_filled_from_unlinked_finish_or_trace(self) -> None:
        group = _group(lines=["04-12 10:00:06.000 InputDispatcher: FINISHED seq=42 handled=false"])
        group["trace"] = {"lines": ['"main" tid=1 Native', "nativePollOnce"]}
        report = build_reconstruction(group)
        for step in report["inputAnr"]["anrModel"]:
            if "reason / 上报" not in step["summary"]:
                self.assertEqual(step["certainty"], "unknown")

    def test_inputs_not_mutated_and_render_has_three_sections(self) -> None:
        group = _group()
        before = deepcopy(group)
        report = build_reconstruction(group)
        before_report = deepcopy(report)
        markdown = render_reconstruction_markdown(report)
        self.assertEqual(group, before)
        self.assertEqual(report, before_report)
        for title in ("超时输入事件", "ANR 发生模型", "模拟操作步骤"):
            self.assertIn("## " + title, markdown)
        self.assertIn("ACTION_DOWN", markdown)
        self.assertIn("不能仅凭该动作推定点击或滑动", markdown)

    def test_anonymous_reasons_cannot_crowd_target_out_of_markdown(self) -> None:
        group = _group(lines=[
            f"04-12 10:00:0{i}.000 InputDispatcher: other channel is not responding. Waited 5001ms for KeyEvent(action=ACTION_UP)"
            for i in range(5)
        ])
        details = build_reconstruction(group)["inputAnr"]
        self.assertEqual(details["reportedEvents"][0]["association"], "anchor")
        self.assertIn("inputEventId=0x123", "\n".join(render_input_anr_sections(details)))

    def test_duplicate_source_view_does_not_erase_known_simulation_action(self) -> None:
        details = build_reconstruction(_group(lines=[REASON]))["inputAnr"]
        self.assertEqual(details["eventIdentificationStatus"], "reason_event_identified")
        self.assertIn("MotionEvent：action=ACTION_DOWN", details["simulationSteps"][1]["summary"])

    def test_ai_event_model_and_steps_use_local_refs_without_promotion(self) -> None:
        report = build_reconstruction(_group())
        evidence_id = report["events"][0]["eventId"]
        payload = {field: [{
            "summary": field, "supportingEventIds": [evidence_id],
            "certainty": "observed", "validation": "verified", "executed": True,
            "finalJudgment": True, "requiresHumanConfirmation": False,
        }] for field in INPUT_CLAIM_FIELDS}
        result = attach_ai_analysis(report, ai_report=payload)
        self.assertEqual(result["inputAnr"], report["inputAnr"])
        for claim in result["aiClaims"]:
            self.assertEqual(claim["certainty"], "inferred")
            self.assertEqual(claim["validation"], "unverified")
            self.assertEqual(claim["referenceValidation"], "references_resolved")
            self.assertFalse(claim["executed"])
            self.assertFalse(claim["finalJudgment"])
            self.assertTrue(claim["requiresHumanConfirmation"])

    def test_ai_cannot_use_a_fabricated_input_event_for_model_or_steps(self) -> None:
        report = build_reconstruction(_group())
        payload = {
            "inputEventAssessments": [{"eventId": "forged-input", "summary": "Invented"}],
            "anrModel": [{"summary": "model", "supportingEventIds": ["forged-input"]}],
            "simulationSteps": [{"summary": "step", "evidenceRefs": ["other-group-id"]}],
        }
        result = attach_ai_analysis(report, ai_report=payload)
        self.assertEqual(result["events"], report["events"])
        for claim in result["aiClaims"][1:]:
            self.assertEqual(claim["referenceValidation"], "invalid_references")
            self.assertEqual(claim["resolvedEventIds"], [])
        self.assertIn("invalid_references", render_reconstruction_markdown(result))

    def test_model_phases_and_simulation_actions_are_in_markdown_not_only_json(self) -> None:
        report = build_reconstruction(_group())
        result = attach_ai_analysis(report, ai_report={
            "anrModel": [{"summary": "Candidate pipeline", "phases": [
                {"summary": "Receiver completion gap", "certainty": "observed"},
            ]}],
            "simulationSteps": [{
                "summary": "Candidate operation", "preconditions": ["Original window ready"],
                "actions": [{"step": 1, "action": "Repeat the verified key sequence"}],
                "expectedObservations": ["Same target event in reason"],
                "captureChecks": ["Capture matching native seq"],
                "missingParameters": ["Build version"],
            }],
        })
        markdown = render_reconstruction_markdown(result)
        for text in (
            "Receiver completion gap", "Original window ready", "Repeat the verified key sequence",
            "Same target event in reason", "Capture matching native seq", "Build version", "候选 / unverified",
        ):
            self.assertIn(text, markdown)

    def test_nested_unknown_action_number_and_gates_survive_markdown(self) -> None:
        report = build_reconstruction(_group())
        result = attach_ai_analysis(report, ai_report={
            "anrModel": [{"summary": "Recovery candidate", "phases": [{
                "phase": "recovery", "summary": "连接已恢复", "certainty": "unknown",
                "uncertainties": ["缺少恢复证据"],
            }]}],
            "simulationSteps": [{"summary": "Operation candidate", "actions": [{
                "step": 8, "action": "重复原输入", "preconditions": ["原窗口核验后才执行"],
                "missingParameters": ["坐标待补"],
            }]}],
        })
        markdown = render_reconstruction_markdown(result)
        for text in ('certainty=unknown', '缺少恢复证据', 'actions 8', '原窗口核验后才执行', '坐标待补'):
            self.assertIn(text, markdown)

    def test_full_generation_slots_refresh_and_old_sidecar_upgrade(self) -> None:
        fixture = Path(__file__).parent / "fixtures/idt_01.json"
        package = load_package_from_fixture(fixture)
        with tempfile.TemporaryDirectory() as tmp:
            index = build_ai_context_artifacts(package, AiContextOptions(out_dir=tmp))
            paths = index["groups"][0]["artifactPaths"]
            analysis_path, json_path = Path(paths["analysis"]), Path(paths["reconstructionJson"])
            before = json.loads(json_path.read_text())
            ref = before["events"][0]["eventId"]
            final = "## 综合分析结论\n未确认上游阻塞\n```json\n" + json.dumps({
                "simulationSteps": [{"summary": "回到原页面重做输入，参数待补", "evidenceRefs": [ref]}],
            }, ensure_ascii=False) + "\n```"
            slots = {key: "本来源缺口仍需补证" for key in ("trace", "eventlog", "logcat-anrmanager")}
            slots["final-anr"] = final
            text = _merge_analyses(analysis_path.read_text(), slots)
            analysis_path.write_text(text)
            # Existing v1 sidecars can be refreshed without re-extracting evidence.
            before.pop("inputAnr")
            json_path.write_text(json.dumps(before))
            refresh_reconstruction(tmp)
            after = json.loads(json_path.read_text())
            self.assertEqual(analysis_path.read_text(), text)
            # Inserting slot text changes artifact line coordinates, not evidence.
            self.assertEqual(
                [(event["eventId"], event["rawLine"], event["timestamp"]) for event in after["events"]],
                [(event["eventId"], event["rawLine"], event["timestamp"]) for event in before["events"]],
            )
            self.assertIn("inputAnr", after)
            self.assertFalse(after["aiClaims"][0]["executed"])
            self.assertIn("回到原页面重做输入", Path(paths["reconstruction"]).read_text())
            self.assertTrue(json.loads((Path(tmp) / "index.json").read_text())["groups"][0]["analysisComplete"])


if __name__ == "__main__":
    unittest.main()
