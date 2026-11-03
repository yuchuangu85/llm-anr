from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from anr_evidence import AiContextOptions, build_ai_context_artifacts
from anr_evidence.reconstruction import build_reconstruction, render_reconstruction_markdown


ANCHOR = "04-12 10:00:05.000 am_anr ANR in com.demo: No focused window"


def _group(lines: list[str]) -> dict:
    return {
        "id": "anr-summary",
        "anchor": {"timestamp": "04-12 10:00:05.000", "line": ANCHOR, "sourceKind": "event_log"},
        "strategy": {"anrType": "no_focus_window"},
        "eventLog": {"lines": [ANCHOR]},
        "logcat": {"lines": lines},
    }


class ReconstructionSummaryTests(unittest.TestCase):
    def test_noise_is_omitted_without_modifying_the_full_report(self) -> None:
        group = _group([
            *[f"04-12 10:00:01.000 Cache: ordinary-noise-{i}" for i in range(100)],
            "04-12 10:00:02.000 InputDispatcher: Waiting because no window has focus. Will wait for 7000 ms",
            "04-12 10:00:06.000 InputDispatcher: native newFocus=window-1",
        ])
        report = build_reconstruction(group)
        before = deepcopy(report)
        markdown = render_reconstruction_markdown(report)
        self.assertNotIn("ordinary-noise-", markdown)
        self.assertIn("7000 ms", markdown)
        self.assertIn("native newFocus=window-1", markdown)
        self.assertIn("## 关键时间线", markdown)
        self.assertNotIn("## 已观察事件与原始证据", markdown)
        self.assertNotIn("precedes（非因果）", markdown)
        self.assertEqual(report, before)
        self.assertEqual(len(report["events"]), 103)
        self.assertLess(len(markdown.splitlines()), 90)

    def test_repeated_identical_records_keep_count_and_endpoints(self) -> None:
        report = build_reconstruction(_group([
            f"04-12 10:00:0{i}.000 WindowManager: Focus request windowToken=w1" for i in (1, 2, 3)
        ]))
        markdown = render_reconstruction_markdown(report)
        self.assertEqual(markdown.count("Focus request windowToken=w1"), 1)
        self.assertIn("重复记录：3", markdown)
        self.assertIn("10:00:01", markdown)
        self.assertIn("10:00:03", markdown)
        self.assertIn("不表示连续", markdown)
        for event in (report["events"][0], report["events"][2]):
            self.assertIn(event["eventId"], markdown)

    def test_different_tokens_sequences_and_intervening_states_are_not_merged(self) -> None:
        records = [
            "04-12 10:00:01.000 WindowManager: Focus request windowToken=w1 seq=1",
            "04-12 10:00:02.000 WindowManager: Focus request windowToken=w2 seq=1",
            "04-12 10:00:03.000 WindowManager: Focus request windowToken=w2 seq=2",
            "04-12 10:00:04.000 InputDispatcher: native newFocus=null",
            "04-12 10:00:04.500 WindowManager: Focus request windowToken=w2 seq=2",
        ]
        markdown = render_reconstruction_markdown(build_reconstruction(_group(records)))
        self.assertNotIn("重复记录：", markdown)
        self.assertEqual(markdown.count("Focus request windowToken=w2 seq=2"), 2)

    def test_omitted_observation_still_breaks_a_repetition_run(self) -> None:
        markdown = render_reconstruction_markdown(build_reconstruction(_group([
            "04-12 10:00:01.000 WindowManager: Focus request windowToken=w1",
            "04-12 10:00:02.000 WindowManager: NOT_FOCUSABLE=false",
            "04-12 10:00:03.000 WindowManager: Focus request windowToken=w1",
        ])))
        self.assertNotIn("重复记录：", markdown)
        self.assertEqual(markdown.count("Focus request windowToken=w1"), 2)

    def test_standalone_configured_timeout_is_not_filtered_as_noise(self) -> None:
        markdown = render_reconstruction_markdown(build_reconstruction(_group([
            "04-12 10:00:01.000 InputDispatcher: Will wait for 7000 ms",
        ])))
        self.assertIn("7000 ms", markdown)
        self.assertIn("不是实测等待时长", markdown)

    def test_input_delivery_and_finish_signals_remain_key_observations(self) -> None:
        markdown = render_reconstruction_markdown(build_reconstruction(_group([
            "04-12 10:00:01.000 InputDispatcher: ACTION_DOWN eventId=key-input",
            "04-12 10:00:06.000 InputDispatcher: FINISHED seq=10 handled=false",
        ])))
        self.assertIn("ACTION_DOWN eventId=key-input", markdown)
        self.assertIn("FINISHED seq=10 handled=false", markdown)

    def test_repeated_timeout_signals_do_not_crowd_out_trace_snapshot(self) -> None:
        group = _group([
            f"04-12 10:00:04.{i:03d} InputDispatcher: no focused window windowToken=w{i}" for i in range(100)
        ])
        group["trace"] = {"lines": [
            "04-12 10:00:06.000 ----- pid 123 -----", '"main" prio=5 tid=1 Native',
            "  at android.os.BinderProxy.transactNative(Native method)",
        ]}
        markdown = render_reconstruction_markdown(build_reconstruction(group))
        self.assertIn("trace_snapshot", markdown)
        self.assertIn("BinderProxy.transactNative", markdown)
        self.assertIn(ANCHOR, markdown)

    def test_timeline_budget_keeps_anr_and_recovery_and_discloses_omission(self) -> None:
        group = _group([
            *[f"04-12 10:00:01.{i:03d} ActivityManager: Start proc worker-{i}" for i in range(100)],
            "04-12 10:00:04.000 InputDispatcher: Waiting because no window has focus",
            "04-12 10:00:06.000 InputDispatcher: native newFocus=recovered-window",
        ])
        markdown = render_reconstruction_markdown(build_reconstruction(group))
        self.assertIn(ANCHOR, markdown)
        self.assertIn("Waiting because no window has focus", markdown)
        self.assertIn("native newFocus=recovered-window", markdown)
        self.assertLessEqual(len([line for line in markdown.splitlines() if line.startswith("#### ")]), 24)
        self.assertIn("未展开", markdown)
        self.assertIn("anr_reconstruction.json", markdown)

    def test_snapshot_shows_main_thread_excerpt_not_the_full_dump(self) -> None:
        group = _group([])
        group["trace"] = {"lines": [
            "04-12 10:00:06.000 ----- pid 123 -----", "Cmd line: com.demo",
            '"background" prio=5 tid=2 Waiting', "  at background.NoisyFrame.run(NoisyFrame.java:1)",
            '"main" prio=5 tid=1 Native', "  native: #00 pc 0000 libbinder.so (IPCThreadState::talkWithDriver)",
            "  at android.os.BinderProxy.transactNative(Native method)",
            *[f"  at repeated.FullDump.frame{i}(FullDump.java:{i})" for i in range(40)],
        ]}
        markdown = render_reconstruction_markdown(build_reconstruction(group))
        self.assertIn('"main"', markdown)
        self.assertIn("BinderProxy.transactNative", markdown)
        self.assertNotIn("background.NoisyFrame", markdown)
        self.assertNotIn("repeated.FullDump", markdown)
        self.assertIn("不是连续阻塞区间", markdown)

    def test_only_short_final_summary_and_candidates_are_copied(self) -> None:
        report = build_reconstruction(_group([]), ai_analyses={
            "trace": "Detailed source analysis that must not be duplicated.",
            "final-anr": "## 综合分析结论\n候选阻塞点仍需核验。\n\n## 时间线\nDetailed final timeline must stay in analysis.\n```json\n{\"raw\": \"must not repeat\"}\n```",
        }, ai_report={"candidateChains": [
            {"summary": f"Candidate-{i}", "details": {"payload": "private-full-payload"}} for i in range(10)
        ]})
        markdown = render_reconstruction_markdown(report)
        self.assertIn("候选阻塞点仍需核验", markdown)
        self.assertIn("inferred", markdown)
        self.assertIn("unverified", markdown)
        self.assertIn("Candidate-0", markdown)
        self.assertNotIn("Candidate-9", markdown)
        self.assertNotIn("private-full-payload", markdown)
        self.assertNotIn("Detailed source analysis", markdown)
        self.assertNotIn("Detailed final timeline", markdown)
        self.assertNotIn('"raw"', markdown)
        self.assertEqual(len(report["aiClaims"]), 10)

    def test_unresolved_candidate_and_stale_evidence_warnings_remain_visible(self) -> None:
        report = build_reconstruction(_group([]), ai_report={"candidateChains": [
            {"summary": "untrusted", "evidenceRefs": ["missing-event"]},
        ]})
        report["warnings"].extend([
            {"code": "ai-evidence-revision-changed", "message": "Preserved AI uses old evidence."},
            {"code": "ai-evidence-revision-changed", "message": "Preserved AI uses old evidence."},
        ])
        markdown = render_reconstruction_markdown(report)
        self.assertIn("invalid_references", markdown)
        self.assertIn("ai-invalid-references", markdown)
        self.assertEqual(markdown.count("ai-evidence-revision-changed"), 1)
        self.assertIn("不能用 am_anr 减 5 秒", markdown)
        self.assertIn("finalJudgment=false", markdown)
        self.assertIn("requiresHumanConfirmation=true", markdown)

    def test_supported_candidate_text_fields_have_readable_summaries(self) -> None:
        report = build_reconstruction(_group([]), ai_report={
            "candidateChains": [{"title": "Main waiting for peer", "rationale": "Binder reply not observed"}],
            "candidateConclusions": [{"statement": "Peer identity unresolved"}],
        })
        markdown = render_reconstruction_markdown(report)
        self.assertIn("Main waiting for peer", markdown)
        self.assertIn("Binder reply not observed", markdown)
        self.assertIn("Peer identity unresolved", markdown)
        self.assertNotIn("仅含结构化字段", markdown)

    def test_candidate_limitations_and_evidence_quality_are_bounded_and_unverified(self) -> None:
        report = build_reconstruction(_group([]), ai_analyses={
            "final-anr": "## 综合分析结论\nPossible binder stall\n## 证据质量\nMissing peer stack prevents identifying transaction owner\n## 修复建议\nDo not duplicate full remediation",
        }, ai_report={"candidateChains": [{
            "summary": "Possible binder stall", "limitations": [
                "Peer stack unavailable", "Missing transaction sequence", "omitted-third-limitation",
            ],
        }]})
        markdown = render_reconstruction_markdown(report)
        self.assertIn("Missing peer stack prevents identifying transaction owner", markdown)
        self.assertIn("Peer stack unavailable", markdown)
        self.assertIn("Missing transaction sequence", markdown)
        self.assertNotIn("omitted-third-limitation", markdown)
        self.assertNotIn("Do not duplicate full remediation", markdown)
        self.assertIn("限制（inferred / unverified）", markdown)
        self.assertEqual(len(report["aiClaims"][0]["limitations"]), 3)

    def test_evidence_quality_heading_inside_fence_is_not_a_real_section(self) -> None:
        report = build_reconstruction(_group([]), ai_analyses={
            "final-anr": "## 综合分析结论\nSummary\n```text\n## 证据质量\nfenced-fake-gap\n```\n## 证据质量\nActual gap\n## 修复建议\nOther section",
        })
        markdown = render_reconstruction_markdown(report)
        self.assertIn("Actual gap", markdown)
        self.assertNotIn("fenced-fake-gap", markdown)
        self.assertNotIn("Other section", markdown)

    def test_different_clocks_and_unknown_times_remain_separate(self) -> None:
        report = build_reconstruction(_group([
            "2026-04-12 10:00:01.000+0800 InputDispatcher: no focused window",
            "InputDispatcher: no focused window",
            "InputDispatcher: no focused window",
        ]))
        markdown = render_reconstruction_markdown(report)
        self.assertIn("wall_clock:+0800", markdown)
        self.assertIn("时钟域：unknown", markdown)
        self.assertIn("未知/不可跨域比较", markdown)
        self.assertNotIn("重复记录：", markdown)

    def test_invalid_timestamp_records_never_form_a_timed_repetition(self) -> None:
        group = _group([
            "04-99 10:00:01.000 InputDispatcher: no focused window",
            "04-99 10:00:02.000 InputDispatcher: no focused window",
        ])
        report = build_reconstruction(group)
        self.assertTrue(any(event["timestamp"] is None for event in report["events"]))
        self.assertNotIn("重复记录：", render_reconstruction_markdown(report))

    def test_ai_cited_observation_keeps_its_evidence_locator(self) -> None:
        group = _group(["04-12 10:00:01.000 Cache: important cited observation"])
        original = build_reconstruction(group)
        event = next(e for e in original["events"] if e["eventType"] == "log_observation")
        report = build_reconstruction(group, source_paths={"logcat": "original.log"}, ai_report={
            "candidateChains": [{"summary": "candidate", "evidenceRefs": [event["rawLine"]]}],
        })
        markdown = render_reconstruction_markdown(report)
        self.assertIn("important cited observation", markdown)
        self.assertIn("original.log", markdown)
        self.assertIn("原始行号未知", markdown)

    def test_each_anr_has_exactly_one_independent_reconstruction_markdown(self) -> None:
        second = ANCHOR.replace("10:00:", "10:01:")
        package = {"package_id": "summary-test", "sources": {
            "event_log": {"path": "events.log", "content": ANCHOR + "\n" + second},
        }}
        with tempfile.TemporaryDirectory() as tmp:
            result = build_ai_context_artifacts(package, AiContextOptions(out_dir=tmp))
            documents = sorted(Path(tmp).rglob("anr_reconstruction.md"))
            self.assertEqual(result["groupCount"], 2)
            self.assertEqual(len(documents), 2)
            self.assertFalse((Path(tmp) / "anr_reconstruction.md").exists())
            for group in result["groups"]:
                path = Path(group["artifactPaths"]["reconstruction"])
                self.assertEqual(path.parent.name, group["id"])
                markdown = path.read_text(encoding="utf-8")
                report = json.loads(Path(group["artifactPaths"]["reconstructionJson"]).read_text(encoding="utf-8"))
                self.assertIn(group["id"], markdown)
                self.assertIn(group["anchor"]["line"], markdown)
                self.assertEqual(report["groupId"], group["id"])


if __name__ == "__main__":
    unittest.main()
