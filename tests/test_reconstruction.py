from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from anr_evidence import AiContextOptions, build_ai_context, build_ai_context_artifacts, load_package_from_fixture
from anr_evidence.ai_context import _merge_analyses
from anr_evidence.evidence_slice import build_evidence_slices
from anr_evidence.time_norm import compute_delta_t_for_group
from anr_evidence.reconstruction import (
    build_reconstruction, index_source_locations, refresh_reconstruction,
    render_reconstruction_markdown,
)

ROOT = Path(__file__).resolve().parent.parent


def _group(lines: list[str], *, group_id: str = "anr-test") -> dict:
    return {
        "id": group_id,
        "anchor": {"timestamp": "04-12 10:00:05.000", "line": "04-12 10:00:05.000 am_anr ANR in com.demo", "sourceKind": "event_log"},
        "strategy": {"anrType": "no_focus_window"},
        "logcat": {"lines": lines},
    }


class ReconstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = load_package_from_fixture(ROOT / "tests/fixtures/nfw_01.json")

    def test_fixture_events_keep_real_times_and_relative_offsets(self) -> None:
        context = build_ai_context(self.package, AiContextOptions())
        report = context.groups[0]["reconstruction"]
        by_type = {event["eventType"]: event for event in report["events"]}
        self.assertEqual(by_type["process_start"]["deltaTSeconds"], -4.0)
        self.assertEqual(by_type["focus_change"]["deltaTSeconds"], -2.0)
        self.assertEqual(by_type["anr_reported"]["deltaTSeconds"], 0.0)
        self.assertTrue(any(event["sourceKind"] == "kernel_log" for event in report["events"]))
        self.assertTrue(all(event["certainty"] == "observed" for event in report["events"]))
        self.assertTrue(all(not relation["causal"] for relation in report["relations"]))
        self.assertFalse(report["finalJudgment"])
        self.assertTrue(report["notRootCauseYet"])
        self.assertTrue(report["requiresHumanConfirmation"])

    def test_snapshot_uses_dump_time_and_is_not_a_duration(self) -> None:
        group = _group([])
        group["trace"] = {"lines": ["04-12 10:00:06.800 ----- pid 123 -----", "Cmd line: com.demo", '"main" prio=5 tid=1 Native', "nativePollOnce"]}
        snapshot = build_reconstruction(group)["events"][0]
        self.assertEqual(snapshot["eventType"], "trace_snapshot")
        self.assertAlmostEqual(snapshot["deltaTSeconds"], 1.8)
        self.assertEqual(snapshot["timeBasis"], "trace_snapshot")
        self.assertIsNone(snapshot["stateBefore"])
        self.assertIsNone(snapshot["stateAfter"])
        self.assertNotIn("durationMs", snapshot)

    def test_unknown_times_never_fall_back_to_anchor(self) -> None:
        report = build_reconstruction(_group(["InputDispatcher: no focused window", "uptime=103000 eventId=1"]))
        self.assertTrue(all(event["timestamp"] is None and event["deltaTSeconds"] is None for event in report["events"]))
        self.assertEqual(report["relations"], [])
        self.assertTrue(any(event["clockDomain"] == "monotonic:unmapped" for event in report["events"]))

    def test_different_clock_domains_are_not_ordered_or_subtracted(self) -> None:
        report = build_reconstruction(_group([
            "04-12 10:00:04.000 WindowManager: Focus request windowToken=w",
            "2026-04-12 10:00:06.000+0800 InputDispatcher: no focused window",
        ]))
        zoned = next(event for event in report["events"] if "+0800" in event["rawLine"])
        self.assertIsNone(zoned["deltaTSeconds"])
        self.assertEqual(report["relations"], [])

    def test_anchor_preserves_explicit_year_for_reconstruction_ess_and_delta(self) -> None:
        package = deepcopy(self.package)
        for source in package["sources"].values():
            source["content"] = source["content"].replace("04-12 ", "2025-04-12 ")
        context = build_ai_context(package, AiContextOptions(build_ess=True, delta_t_normalized=True))
        group = context.groups[0]
        anr = next(event for event in group["reconstruction"]["events"] if event["eventType"] == "anr_reported")
        self.assertEqual(anr["deltaTSeconds"], 0)
        self.assertTrue(anr["timestamp"].startswith("2025-"))
        ess_anr = next(item for item in group["evidenceSlices"] if "am_anr" in item["content"])
        self.assertEqual(ess_anr["delta_t_seconds"], 0)
        self.assertEqual(group["timeNormalized"]["eventLog"][0]["delta_t_seconds"], -4)

    def test_payload_dates_do_not_retimestamp_monotonic_or_untimed_records(self) -> None:
        group = _group([
            "[ 100.000] binder: last_error_at=2026-04-12 09:59:59.000",
            "[ 110.000] binder: last_error_at=2026-04-12 09:59:58.000",
            "InputDispatcher historical date=2026-04-12 09:59:57.000",
        ])
        report = build_reconstruction(group)
        self.assertTrue(all(event["timestamp"] is None for event in report["events"]))
        self.assertEqual(report["relations"], [])
        self.assertTrue(all(item.timestamp_iso is None for item in build_evidence_slices([group])))
        self.assertTrue(all(item.timestamp_iso is None for item in compute_delta_t_for_group(group)["logcat"]))

    def test_false_drawn_flags_do_not_become_completed_drawing(self) -> None:
        report = build_reconstruction(_group([
            "04-12 10:00:01.000 WindowState: allDrawn=false",
            "04-12 10:00:02.000 WindowState: firstWindowDrawn=false",
            "04-12 10:00:03.000 WindowState: allDrawn=true",
        ]))
        self.assertEqual(sum(event["eventType"] == "window_drawn" for event in report["events"]), 1)
        self.assertTrue(all(event["stateAfter"] is None for event in report["events"] if "false" in event["rawLine"]))

    def test_equal_timestamps_have_no_happens_before_edge(self) -> None:
        report = build_reconstruction(_group([
            "04-12 10:00:05.000 WindowManager: Focus request windowToken=a",
            "04-12 10:00:05.000 WindowManager: reportDrawFinished windowToken=b",
        ]))
        self.assertEqual(len(report["events"]), 2)
        self.assertEqual(report["relations"], [])

    def test_cleared_focus_and_false_flags_do_not_prove_focus_recovery_or_ineligibility(self) -> None:
        report = build_reconstruction(_group([
            "04-12 10:00:05.000 InputDispatcher: native newFocus=null",
            "04-12 10:00:05.100 WindowInfo: NOT_VISIBLE=false",
        ]))
        self.assertFalse(any(event["eventType"] in {"focus_granted", "window_ineligible"} for event in report["events"]))
        recovery = next(step for step in report["mechanismSteps"] if "焦点恢复" in step["step"])
        self.assertEqual(recovery["certainty"], "unknown")

    def test_explicit_identity_and_timeout_are_not_inferred_from_anchor(self) -> None:
        report = build_reconstruction(_group([
            "04-12 10:00:01.000 InputDispatcher: Waiting because no window has focus displayId=2 appToken=a eventId=44 Will wait for 6000ms",
        ]))
        event = report["events"][0]
        self.assertEqual(event["entities"], {"displayId": "2", "applicationToken": "a", "inputEventId": "44"})
        self.assertEqual(event["configuredTimeoutMs"], 6000)
        self.assertIsNone(event["stateBefore"])
        self.assertNotIn("deadline", event)

    def test_threadtime_emitter_is_distinct_from_anr_process_and_art_tid(self) -> None:
        report = build_reconstruction(_group([
            "04-12 10:00:05.000 1000 123 456 I am_anr: [0,999,com.target,1,no focused window]",
            "04-12 10:00:05.100 trace: tid=1 sysTid=999",
        ]))
        anr = next(event for event in report["events"] if event["eventType"] == "anr_reported")
        self.assertEqual(anr["entities"]["emitterPid"], "123")
        self.assertEqual(anr["entities"]["emitterTid"], "456")
        self.assertEqual(anr["entities"]["anrProcess"], "com.target")
        trace = next(event for event in report["events"] if event["eventType"] != "anr_reported")
        self.assertEqual(trace["entities"]["artTid"], "1")
        self.assertEqual(trace["entities"]["sysTid"], "999")

    def test_source_coordinates_are_not_filtered_line_indices(self) -> None:
        line = "04-12 10:00:03.000 InputDispatcher: no focused window"
        group = _group([line])
        package = {"sources": {"logcat": {"path": "raw.log", "content": "irrelevant\n\n" + line}}}
        report = build_reconstruction(group, source_paths={"logcat": "raw.log"}, source_locations=index_source_locations(package, [group]))
        ref = report["events"][0]["evidenceRefs"][0]
        self.assertEqual(ref["sourcePath"], "raw.log")
        self.assertEqual(ref["lineNumber"], 3)
        self.assertEqual(ref["extractedLineNumber"], 1)
        self.assertEqual(ref["coordinateSystem"], "loaded-source-content")

    def test_ambiguous_source_coordinates_are_explicit(self) -> None:
        line = "InputDispatcher: no focused window"
        group = _group([line])
        package = {"sources": {"logcat": {"content": line + "\n" + line}}}
        ref = build_reconstruction(group, source_locations=index_source_locations(package, [group]))["events"][0]["evidenceRefs"][0]
        self.assertIsNone(ref["lineNumber"])
        self.assertEqual(ref["sourceLineCandidates"], [1, 2])

    def test_duplicate_views_merge_but_real_repeated_lines_remain(self) -> None:
        line = "04-12 10:00:05.000 AnrManager: ANR in com.demo"
        group = _group([line, line])
        group["anrManager"] = {"lines": [line]}
        events = build_reconstruction(group)["events"]
        self.assertEqual(len(events), 2)
        self.assertEqual(len({event["eventId"] for event in events}), 2)

    def test_ids_are_stable_and_input_is_not_mutated(self) -> None:
        group = _group(["04-12 10:00:03.000 InputDispatcher: no focused window"])
        before = deepcopy(group)
        self.assertEqual(build_reconstruction(group), build_reconstruction(group))
        self.assertEqual(group, before)

    def test_no_focus_reason_does_not_fabricate_wait_start(self) -> None:
        report = build_reconstruction(_group(["04-12 10:00:05.000 am_anr Application does not have a focused window"]))
        wait = next(step for step in report["mechanismSteps"] if "首次" in step["step"])
        self.assertEqual(wait["certainty"], "unknown")
        self.assertEqual(wait["supportingEventIds"], [])
        self.assertTrue(any("不能用 am_anr 减 5 秒" in gap for gap in report["evidenceGaps"]))

    def test_pre_anr_warning_is_not_a_full_anr_report(self) -> None:
        report = build_reconstruction(_group(["04-12 10:00:02.000 WindowManager: pre-ANR in com.demo"]))
        self.assertEqual(report["events"][0]["eventType"], "pre_anr_warning")
        anr_step = next(step for step in report["mechanismSteps"] if "ANR 上报" in step["step"])
        self.assertEqual(anr_step["certainty"], "unknown")

    def test_empty_evidence_and_ai_only_never_create_observed_events(self) -> None:
        report = build_reconstruction(_group([]), ai_report={"events": [{"timestamp": "04-12 10:00:00.000", "summary": "invented"}]})
        self.assertEqual(report["events"], [])
        self.assertEqual(report["processCompleteness"], "unknown")
        self.assertEqual(report["aiClaims"][0]["certainty"], "inferred")

    def test_markdown_fences_cannot_be_closed_by_evidence(self) -> None:
        report = build_reconstruction(_group(["04-12 10:00:05.000 InputDispatcher: ``` no focused window"]))
        markdown = render_reconstruction_markdown(report)
        self.assertIn("````text", markdown)
        self.assertIn("causal=false", markdown)

    def test_default_artifacts_include_independent_document_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index = build_ai_context_artifacts(self.package, AiContextOptions(out_dir=tmp))
            paths = index["groups"][0]["artifactPaths"]
            markdown = Path(paths["reconstruction"]).read_text(encoding="utf-8")
            report = json.loads(Path(paths["reconstructionJson"]).read_text(encoding="utf-8"))
            self.assertIn("# ANR 发生过程重建", markdown)
            self.assertIn("process_start", markdown)
            self.assertEqual(report["aiStatus"], "absent")
            self.assertEqual(index["groups"][0]["analysisSlots"]["final-anr"], "pending")
            refresh_reconstruction(tmp)
            self.assertEqual(json.loads(Path(paths["reconstructionJson"]).read_text())["aiStatus"], "absent")
            self.assertTrue(all(event["evidenceRefs"] for event in report["events"]))
            analysis = Path(paths["analysis"]).read_text(encoding="utf-8")
            self.assertIn("anr_reconstruction.md", analysis)
            self.assertEqual(len(re.findall(r"<!-- AI_ANALYSIS_SLOT:", analysis)), 4)

    def test_optional_ess_and_delta_flags_are_propagated_and_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            options = AiContextOptions(out_dir=tmp, build_ess=True, delta_t_normalized=True)
            result = build_ai_context(self.package, options)
            self.assertTrue(result.options.build_ess)
            self.assertTrue(result.options.delta_t_normalized)
            self.assertTrue(result.groups[0]["evidenceSlices"])
            self.assertIn("kernelLog", result.groups[0]["timeNormalized"])
            paths = build_ai_context_artifacts(self.package, options)["groups"][0]["artifactPaths"]
            ess = [json.loads(line) for line in Path(paths["evidenceSlices"]).read_text().splitlines()]
            times = json.loads(Path(paths["timeNormalized"]).read_text())
            self.assertTrue(any(item["source"] == "kernel_log" for item in ess))
            self.assertEqual(times["eventLog"][0]["delta_t_seconds"], -4.0)

    def test_refresh_updates_ai_but_preserves_observed_events_and_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            index = build_ai_context_artifacts(self.package, AiContextOptions(out_dir=tmp))
            paths = index["groups"][0]["artifactPaths"]
            analysis = Path(paths["analysis"])
            before = json.loads(Path(paths["reconstructionJson"]).read_text())
            analyses = {name: "完成本来源分析" for name in ("trace", "eventlog", "logcat-anrmanager")}
            analyses["final-anr"] = "## 综合分析结论\n候选解释\n```json\n" + json.dumps({"candidateChains": [{"summary": "candidate", "evidenceRefs": [before["events"][0]["eventId"]]}]}) + "\n```"
            text = _merge_analyses(analysis.read_text(), analyses)
            analysis.write_text(text)
            refresh_reconstruction(analysis)
            after = json.loads(Path(paths["reconstructionJson"]).read_text())
            # Artifact positions may move when AI text is inserted; observations do not.
            self.assertEqual([e["eventId"] for e in before["events"]], [e["eventId"] for e in after["events"]])
            self.assertEqual([e["timestamp"] for e in before["events"]], [e["timestamp"] for e in after["events"]])
            self.assertEqual(analysis.read_text(), text)
            self.assertEqual(after["aiClaims"][0]["validation"], "references_resolved")
            self.assertTrue(json.loads((Path(tmp) / "index.json").read_text())["groups"][0]["analysisComplete"])
            again = refresh_reconstruction(tmp)
            self.assertEqual(again["groupCount"], 1)
            self.assertEqual(json.loads(Path(paths["reconstructionJson"]).read_text()), after)

    def test_regeneration_preserves_slots_and_refreshes_reconstruction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            options = AiContextOptions(out_dir=tmp)
            paths = build_ai_context_artifacts(self.package, options)["groups"][0]["artifactPaths"]
            analysis = Path(paths["analysis"])
            analysis.write_text(_merge_analyses(analysis.read_text(), {"final-anr": "## 综合分析结论\n保留这个候选解释"}))
            build_ai_context_artifacts(self.package, options)
            self.assertIn("保留这个候选解释", analysis.read_text())
            self.assertIn("保留这个候选解释", Path(paths["reconstruction"]).read_text())

    def test_changed_extracted_log_requires_rebuild_not_silent_stale_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_ai_context_artifacts(self.package, AiContextOptions(out_dir=tmp))["groups"][0]["artifactPaths"]
            before = Path(paths["reconstructionJson"]).read_bytes()
            logcat = Path(paths["logcat"])
            logcat.write_text(logcat.read_text() + "04-12 10:00:05.000 NEW EVIDENCE\n")
            with self.assertRaisesRegex(ValueError, "evidence changed"):
                refresh_reconstruction(tmp)
            self.assertEqual(Path(paths["reconstructionJson"]).read_bytes(), before)

    def test_meminfo_headings_inside_fence_are_covered_by_evidence_digest(self) -> None:
        package = deepcopy(self.package)
        package["sources"]["meminfo"] = {"path": "meminfo.txt", "content": "\n".join([
            "2026-04-12 10:00:04", "Applications Memory Usage (in Kilobytes):",
            "Total RSS by OOM adjustment:", "    160,000K: com.demo (pid 100)",
            "Total PSS by OOM adjustment:", "    150,000K: com.demo (pid 100)",
            "Total RAM: 1,000,000K (status low)", " Free RAM: 100,000K",
        ])}
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_ai_context_artifacts(package, AiContextOptions(out_dir=tmp))["groups"][0]["artifactPaths"]
            path = Path(paths["analysis"])
            text = path.read_text()
            self.assertIn("### Target package", text)
            self.assertIn("160,000", text)
            path.write_text(text.replace("160,000", "999,000"))
            with self.assertRaisesRegex(ValueError, "evidence changed"):
                refresh_reconstruction(tmp)

    def test_ai_quotation_cannot_become_a_source_artifact_coordinate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_ai_context_artifacts(self.package, AiContextOptions(out_dir=tmp))["groups"][0]["artifactPaths"]
            path = Path(paths["analysis"])
            kernel_line = self.package["sources"]["kernel_log"]["content"].splitlines()[0]
            path.write_text(_merge_analyses(path.read_text(), {"final-anr": "## 综合分析结论\n```text\n" + kernel_line + "\n```"}))
            refresh_reconstruction(tmp)
            report = json.loads(Path(paths["reconstructionJson"]).read_text())
            kernel = next(event for event in report["events"] if event["rawLine"] == kernel_line)
            self.assertIsNone(kernel["evidenceRefs"][0]["artifactPath"])
            self.assertIsNone(kernel["evidenceRefs"][0]["artifactLineNumber"])

    def test_legacy_workspace_migrates_without_overwriting_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = build_ai_context_artifacts(self.package, AiContextOptions(out_dir=tmp))["groups"][0]["artifactPaths"]
            analysis = Path(paths["analysis"])
            before = analysis.read_bytes()
            Path(paths["reconstruction"]).unlink()
            Path(paths["reconstructionJson"]).unlink()
            refresh_reconstruction(analysis.parent)
            migrated = json.loads(Path(paths["reconstructionJson"]).read_text())
            self.assertTrue(any(event["eventType"] == "process_start" for event in migrated["events"]))
            self.assertTrue(any("旧版" in gap for gap in migrated["evidenceGaps"]))
            self.assertEqual(analysis.read_bytes(), before)

    def test_multiple_groups_remain_isolated_and_reject_unscoped_ai(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            package = deepcopy(self.package)
            for source in package["sources"].values():
                source["content"] += "\n" + source["content"].replace("10:00:", "10:01:")
            index = build_ai_context_artifacts(package, AiContextOptions(out_dir=tmp))
            self.assertEqual(index["groupCount"], 2)
            with self.assertRaisesRegex(ValueError, "explicit groupId"):
                refresh_reconstruction(tmp, ai_report={"candidateChains": []})
            first_id = index["groups"][0]["id"]
            refresh_reconstruction(tmp, ai_report={"groups": [{"groupId": first_id, "candidateChains": [{"summary": "first only"}]}]})
            reports = [json.loads(Path(group["artifactPaths"]["reconstructionJson"]).read_text()) for group in index["groups"]]
            self.assertEqual(len(reports[0]["aiClaims"]), 1)
            self.assertEqual(reports[1]["aiClaims"], [])
            self.assertTrue(all("10:01:" not in event["rawLine"] for event in reports[0]["events"]))

    def test_script_outputs_path_and_cli_refresh_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            generated = subprocess.run([sys.executable, "scripts/anr_to_ai.py", "tests/fixtures/nfw_01.json", "--out-dir", tmp, "--build-ess", "--delta-t-normalized"], cwd=ROOT, capture_output=True, text=True, check=True)
            self.assertIn("anr_reconstruction.md", generated.stdout)
            refreshed = subprocess.run([sys.executable, "scripts/reconstruct_anr.py", tmp], cwd=ROOT, capture_output=True, text=True, check=True)
            self.assertIn("anr_reconstruction.md", refreshed.stdout)
            cli = subprocess.run([sys.executable, "-m", "anr_evidence", "--reconstruct", tmp], cwd=ROOT, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(cli.stdout)["groupCount"], 1)

    def test_bad_refresh_input_has_friendly_error(self) -> None:
        result = subprocess.run([sys.executable, "scripts/reconstruct_anr.py", "tests/fixtures/nfw_01.json"], cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Reconstruction failed", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
