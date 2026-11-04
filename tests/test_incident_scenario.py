from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from anr_evidence import AiContextOptions, build_ai_context_artifacts
from anr_evidence.ai_context import _merge_analyses
from anr_evidence.constants import SUPPORTED_TYPES
from anr_evidence.incident_scenario import build_incident_scenario, render_incident_sections
from anr_evidence.input_anr import build_input_anr
from anr_evidence.reconstruction import build_reconstruction, refresh_reconstruction, render_reconstruction_markdown
from anr_evidence.reconstruction_ai import attach_ai_analysis


REASONS = {
    "broadcast_timeout": "Broadcast of Intent { act=com.demo.TEST cmp=com.demo/.Receiver }",
    "service_timeout": "executing service com.demo/.DemoService",
    "content_provider_timeout": "timeout publishing content providers com.demo/.Provider",
    "job_scheduler_timeout": "JobServiceContext timeout com.demo/.Jobs onStartJob",
    "system_watchdog_swt": "system_server watchdog detected blocked in handler",
    "input_dispatching_timeout": "Input dispatching timed out (channel is not responding. Waited 5001ms for KeyEvent(action=ACTION_DOWN, keyCode=KEYCODE_BACK))",
    "no_focus_window": "Application does not have a focused window",
    "unknown": "Unclassified failure",
    "vendor_custom": "Vendor custom failure",
}


def _group(anr_type: str) -> dict:
    line = f"04-12 10:00:05.000 am_anr ANR in com.demo: {REASONS[anr_type]}"
    return {
        "id": "anr-all-types", "strategy": {"anrType": anr_type},
        "anchor": {"line": line, "timestamp": "04-12 10:00:05.000", "sourceKind": "event_log"},
        "eventLog": {"lines": [line]},
        "logcat": {"lines": [
            "04-12 10:00:03.000 ActivityManager: other process is still waiting",
        ]},
    }


def _package(anr_type: str) -> dict:
    group = _group(anr_type)
    return {
        "package_id": f"ALL-{anr_type}", "provided_type": anr_type,
        "sources": {
            "event_log": {"path": "events.log", "content": group["anchor"]["line"]},
            "logcat": {"path": "logcat.txt", "content": "\n".join(group["logcat"]["lines"])},
            "trace": {"path": "trace.txt", "content": '04-12 10:00:06.000 ----- pid 123 -----\nCmd line: com.demo\n"main" tid=1 Native\n  at nativePollOnce'},
        },
    }


class IncidentScenarioTests(unittest.TestCase):
    def test_all_types_include_model_and_steps_without_mutating_sources(self) -> None:
        for anr_type in (*SUPPORTED_TYPES, "unknown", "vendor_custom"):
            with self.subTest(anr_type=anr_type):
                group = _group(anr_type)
                before = deepcopy(group)
                report = build_reconstruction(group)
                scenario = report["incidentScenario"]
                before_report = deepcopy(report)
                markdown = render_reconstruction_markdown(report)
                self.assertEqual(group, before)
                self.assertEqual(report, before_report)
                self.assertEqual(scenario["schemaVersion"], "anr-incident-scenario-v1")
                self.assertTrue(scenario["anrModel"])
                self.assertTrue(scenario["simulationSteps"])
                self.assertEqual(scenario["certainty"], "inferred")
                self.assertEqual(scenario["validation"], "unverified")
                self.assertFalse(scenario["executed"])
                self.assertIn("## ANR 发生模型", markdown)
                self.assertIn("## 模拟操作步骤", markdown)
                self.assertEqual("## 超时输入事件" in markdown, anr_type in {"input_dispatching_timeout", "no_focus_window"})
                self.assertFalse(scenario["finalJudgment"])
                self.assertTrue(scenario["notRootCauseYet"])
                self.assertTrue(scenario["requiresHumanConfirmation"])
                for step in scenario["simulationSteps"]:
                    self.assertEqual(step["certainty"], "inferred")
                    self.assertEqual(step["validation"], "unverified")
                    self.assertFalse(step["executed"])

    def test_input_model_and_steps_reuse_existing_identity_contract(self) -> None:
        group = _group("input_dispatching_timeout")
        observations = [{"eventId": "observed-1", "rawLine": group["anchor"]["line"], "sourceKind": "event_log"}]
        input_anr = build_input_anr("input_dispatching_timeout", group["anchor"], observations)
        before = deepcopy(input_anr)
        scenario = build_incident_scenario("input_dispatching_timeout", group["anchor"], observations, input_anr=input_anr)
        self.assertEqual(scenario["anrModel"], input_anr["anrModel"])
        self.assertEqual(scenario["simulationSteps"], input_anr["simulationSteps"])
        self.assertIsNot(scenario["simulationSteps"], input_anr["simulationSteps"])
        self.assertEqual(render_incident_sections(scenario, input_anr)[1], "## 超时输入事件")
        self.assertEqual(input_anr, before)

    def test_non_input_models_are_type_specific_and_never_ack_models(self) -> None:
        expected = {
            "broadcast_timeout": ("goAsync", "PendingResult"),
            "service_timeout": ("onCreate/onStartCommand/onBind", "Service"),
            "content_provider_timeout": ("query/CRUD", "Provider"),
            "job_scheduler_timeout": ("onStartJob/onStopJob", "setNotification"),
            "system_watchdog_swt": ("monitor", "handler"),
        }
        for anr_type, words in expected.items():
            with self.subTest(anr_type=anr_type):
                report = build_reconstruction(_group(anr_type))
                self.assertIsNone(report["inputAnr"])
                text = json.dumps(report["incidentScenario"], ensure_ascii=False)
                for word in words:
                    self.assertIn(word, text)
                self.assertNotIn("waitQueue", text)
                self.assertNotIn("FINISHED", text)
                self.assertNotIn("native seq", text)

    def test_standard_am_anr_payload_retains_target_package(self) -> None:
        line = "04-12 10:00:05.000 I am_anr: [0,1234,com.demo:worker,1,Broadcast of Intent { act=com.demo.TEST }]"
        scenario = build_incident_scenario("broadcast_timeout", {"line": line}, [
            {"eventId": "anchor-event", "rawLine": line, "sourceKind": "event_log"},
        ])
        self.assertIn("com.demo:worker", scenario["simulationSteps"][0]["summary"])
        phase = next(phase for phase in scenario["anrModel"] if phase["phase"] == "notification")
        self.assertEqual(phase["supportingEventIds"], ["anchor-event"])

    def test_unknown_and_unsupported_types_keep_concrete_evidence_gaps(self) -> None:
        for anr_type in ("unknown", "vendor_custom", None):
            with self.subTest(anr_type=anr_type):
                scenario = build_incident_scenario(anr_type, {}, [])
                self.assertTrue(all(phase["certainty"] == "unknown" for phase in scenario["anrModel"]))
                markdown = "\n".join(render_incident_sections(scenario))
                self.assertIn("准确 ANR 类型", markdown)
                self.assertIn("不执行猜测性操作", markdown)
                self.assertNotIn("不适用", markdown)
                self.assertNotIn("waitQueue", markdown)
                self.assertNotIn("5 秒", markdown)

    def test_keyword_match_cannot_promote_entire_phase_to_observed(self) -> None:
        group = _group("broadcast_timeout")
        group["logcat"] = {"lines": [
            "04-12 10:00:03.000 Receiver: not calling PendingResult.finish onReceive",
            "04-12 10:00:04.000 Receiver: goAsync from another component",
            "04-12 10:00:06.000 Recovery: not recovered",
        ]}
        report = build_reconstruction(group)
        phases = report["incidentScenario"]["anrModel"]
        for phase in phases:
            if phase["phase"] != "notification":
                self.assertEqual(phase["certainty"], "unknown")
                self.assertEqual(phase["supportingEventIds"], [])
        self.assertTrue(next(phase for phase in phases if phase["phase"] == "callback")["candidateObservationIds"])

    def test_fallback_anchor_or_trace_does_not_prove_anr_report(self) -> None:
        for line, source in (
            ("04-12 10:00:05.000 BroadcastQueue: waiting on receiver", "logcat"),
            ("04-12 10:00:05.000 pre-ANR in com.demo", "logcat"),
            ("04-12 10:00:05.000 ANR in com.demo", "trace"),
        ):
            scenario = build_incident_scenario("broadcast_timeout", {"line": line}, [
                {"rawLine": line, "eventId": "candidate-1", "sourceKind": source},
            ])
            notification = next(phase for phase in scenario["anrModel"] if phase["phase"] == "notification")
            self.assertEqual(notification["certainty"], "unknown")

    def test_message_mentions_do_not_count_as_actual_report_tags(self) -> None:
        for line in (
            "04-12 10:00:05.000 BroadcastQueue: no am_anr record; still waiting for receiver com.demo/.Receiver",
            "04-12 10:00:05.000 BroadcastQueue: received text ANR in com.demo",
            "04-12 10:00:05.000 100 101 I BroadcastQueue: no am_anr record; waiting",
            "04-12 10:00:05.000 I/BroadcastQueue( 100): no am_anr record; waiting",
        ):
            with self.subTest(line=line):
                scenario = build_incident_scenario("broadcast_timeout", {"line": line}, [
                    {"eventId": "fallback", "rawLine": line, "sourceKind": "logcat"},
                ])
                notification = next(phase for phase in scenario["anrModel"] if phase["phase"] == "notification")
                self.assertEqual(notification["certainty"], "unknown")
                self.assertEqual(notification["supportingEventIds"], [])

    def test_real_report_headers_can_support_fallback_report_observation(self) -> None:
        for line in (
            "04-12 10:00:05.000 I am_anr: [0,1234,com.demo,1,Broadcast of Intent]",
            "04-12 10:00:05.000 100 101 E ActivityManager: ANR in com.demo",
            "04-12 10:00:05.000 I/AnrManager( 100): ANR in com.demo",
            "04-12 10:00:05.000 E Watchdog: *** WATCHDOG KILLING SYSTEM PROCESS: blocked in handler",
        ):
            with self.subTest(line=line):
                scenario = build_incident_scenario("broadcast_timeout", {"line": line}, [
                    {"eventId": "real-report", "rawLine": line, "sourceKind": "logcat"},
                ])
                notification = next(phase for phase in scenario["anrModel"] if phase["phase"] == "notification")
                self.assertEqual(notification["certainty"], "observed")
                self.assertEqual(notification["supportingEventIds"], ["real-report"])

    def test_local_simulation_constraints_survive_public_markdown(self) -> None:
        scenario = build_incident_scenario("broadcast_timeout", {}, [])
        step = scenario["simulationSteps"][1]
        step.update({
            "preconditions": ["ONLY_AUTHORIZED_TEST_ENV"],
            "missingParameters": ["RECEIVER_PERMISSION_MISSING"],
            "captureChecks": ["SAME_RECEIVER_COMPLETION"],
            "expectedObservations": ["MATCH_CALLBACK_AND_FINISH"],
            "uncertainties": ["TIMING_MAPPING_UNKNOWN"],
        })
        before = deepcopy(scenario)
        markdown = "\n".join(render_incident_sections(scenario))
        for text in (
            "ONLY_AUTHORIZED_TEST_ENV", "RECEIVER_PERMISSION_MISSING", "SAME_RECEIVER_COMPLETION",
            "MATCH_CALLBACK_AND_FINISH", "TIMING_MAPPING_UNKNOWN", "executed=false",
        ):
            self.assertIn(text, markdown)
        self.assertEqual(scenario, before)

    def test_other_anr_reports_cannot_become_this_incident_report_refs(self) -> None:
        group = _group("service_timeout")
        scenario = build_incident_scenario("service_timeout", group["anchor"], [
            {"eventId": "other-anr", "rawLine": "04-12 10:00:02.000 am_anr ANR in com.other: executing service", "sourceKind": "event_log"},
        ])
        phase = next(phase for phase in scenario["anrModel"] if phase["phase"] == "notification")
        self.assertEqual(phase["supportingEventIds"], [])
        self.assertEqual(phase["certainty"], "unknown")

    def test_ai_non_input_model_and_steps_stay_candidates_in_markdown(self) -> None:
        report = build_reconstruction(_group("broadcast_timeout"))
        ref = next(event["eventId"] for event in report["events"] if event["eventType"] == "anr_reported")
        before = deepcopy(report)
        result = attach_ai_analysis(report, ai_report={
            "anrModel": [{"summary": "Receiver candidate", "supportingEventIds": [ref], "certainty": "observed"}],
            "simulationSteps": [{"summary": "Candidate broadcast verification", "supportingEventIds": [ref], "executed": True}],
        })
        self.assertEqual(result["events"], before["events"])
        self.assertEqual(result["incidentScenario"], before["incidentScenario"])
        for claim in result["aiClaims"]:
            self.assertEqual(claim["certainty"], "inferred")
            self.assertEqual(claim["validation"], "unverified")
            self.assertFalse(claim["executed"])
        markdown = render_reconstruction_markdown(result)
        self.assertIn("Receiver candidate", markdown)
        self.assertIn("Candidate broadcast verification", markdown)

    def test_generate_refresh_old_v1_sidecar_and_slots_for_all_non_input_types(self) -> None:
        for anr_type in (*[kind for kind in SUPPORTED_TYPES if kind not in {"input_dispatching_timeout", "no_focus_window"}], "unknown"):
            with self.subTest(anr_type=anr_type), tempfile.TemporaryDirectory() as tmp:
                index = build_ai_context_artifacts(_package(anr_type), AiContextOptions(out_dir=tmp, anr_type=anr_type))
                paths = index["groups"][0]["artifactPaths"]
                json_path, analysis_path = Path(paths["reconstructionJson"]), Path(paths["analysis"])
                before = json.loads(json_path.read_text())
                text = _merge_analyses(analysis_path.read_text(), {
                    "trace": "本来源证据不足，保留缺口。", "eventlog": "保留 ANR 锚点。",
                    "logcat-anrmanager": "本来源缺少完成链。", "final-anr": "## 综合分析结论\n候选机制仍需补证。",
                })
                analysis_path.write_text(text)
                before.pop("incidentScenario")
                json_path.write_text(json.dumps(before))
                refresh_reconstruction(tmp)
                after = json.loads(json_path.read_text())
                self.assertEqual(analysis_path.read_text(), text)
                self.assertEqual(after["schemaVersion"], "anr-reconstruction-v1")
                self.assertTrue(after["incidentScenario"]["anrModel"])
                self.assertTrue(after["incidentScenario"]["simulationSteps"])
                self.assertEqual([event["eventId"] for event in after["events"]], [event["eventId"] for event in before["events"]])
                markdown = Path(paths["reconstruction"]).read_text()
                for title in ("## ANR 发生模型", "## 模拟操作步骤"):
                    self.assertIn(title, markdown)
                self.assertNotIn("## 超时输入事件", markdown)
                refresh_reconstruction(tmp)
                self.assertEqual(json.loads(json_path.read_text()), after)

    def test_cli_report_delivery_and_generation_cover_every_type(self) -> None:
        root = Path(__file__).resolve().parent.parent
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        for anr_type in (*SUPPORTED_TYPES, "unknown"):
            with self.subTest(anr_type=anr_type), tempfile.TemporaryDirectory() as tmp:
                fixture = Path(tmp) / "fixture.json"
                fixture.write_text(json.dumps(_package(anr_type)))
                for operation in ("--report", "--deliver"):
                    completed = subprocess.run(
                        [sys.executable, "-m", "anr_evidence", operation, str(fixture)],
                        cwd=root, capture_output=True, text=True, check=True, env=env,
                    )
                    self.assertIn("## ANR 发生模型", completed.stdout)
                    self.assertIn("## 模拟操作步骤", completed.stdout)
                    self.assertEqual("## 超时输入事件" in completed.stdout, anr_type in {"input_dispatching_timeout", "no_focus_window"})
                context = Path(tmp) / "context"
                subprocess.run(
                    [sys.executable, "scripts/anr_to_ai.py", str(fixture), "--out-dir", str(context), "--anr-type", anr_type],
                    cwd=root, capture_output=True, text=True, check=True, env=env,
                )
                index = json.loads((context / "index.json").read_text())
                paths = index["groups"][0]["artifactPaths"]
                self.assertEqual(set(index["groups"][0]["analysisSlots"].values()), {"pending"})
                self.assertEqual(len(index["groups"][0]["analysisSlots"]), 4)
                sidecar = json.loads(Path(paths["reconstructionJson"]).read_text())
                self.assertTrue(sidecar["incidentScenario"]["anrModel"])
                subprocess.run(
                    [sys.executable, "scripts/reconstruct_anr.py", str(context)],
                    cwd=root, capture_output=True, text=True, check=True, env=env,
                )
                markdown = Path(paths["reconstruction"]).read_text()
                self.assertIn("## ANR 发生模型", markdown)
                self.assertIn("## 模拟操作步骤", markdown)


if __name__ == "__main__":
    unittest.main()
