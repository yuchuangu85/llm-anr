from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from anr_evidence import AiContextOptions, build_ai_context, build_ai_context_artifacts


def _package_with_digest_signals() -> dict:
    return {
        "package_id": "DIGEST-INTEGRATION",
        "sources": {
            "event_log": {
                "path": "events.log",
                "content": "04-12 10:00:05.000 am_anr ANR in com.demo: Input dispatching timed out",
            },
            "trace": {
                "path": "traces.txt",
                "content": "04-12 10:00:05.100 main tid=1 Native: input dispatching timeout",
            },
            "logcat": {
                "path": "logcat.txt",
                "content": "\n".join([
                    "04-12 10:00:04.700 E InputDispatcher: Input dispatching timed out for com.demo",
                    "04-12 10:00:04.900 E InputDispatcher: Input dispatching timed out for com.demo",
                    "04-12 10:00:04.950 W WindowManager: mCurrentFocus=null for com.demo",
                    (
                        "04-12 10:00:05.050 E SurfaceFlinger: fence blocked; "
                        "waiting because the touched window has not finished processing for com.demo"
                    ),
                    "04-12 10:00:05.100 I ActivityManager: unrelated retained context for com.demo",
                ]),
            },
        },
    }


class InputWindowDigestIntegrationTests(unittest.TestCase):
    def test_input_artifacts_render_digest_without_changing_baseline_artifacts(self) -> None:
        package = _package_with_digest_signals()
        with tempfile.TemporaryDirectory() as tmpdir:
            summary = build_ai_context_artifacts(
                package,
                AiContextOptions(
                    out_dir=tmpdir,
                    package_name="com.demo",
                    anr_type="input_dispatching_timeout",
                ),
            )
            index = json.loads(Path(tmpdir, "index.json").read_text(encoding="utf-8"))
            group_dir = Path(tmpdir, summary["groups"][0]["id"])
            analysis_path = group_dir / "anr_analysis.md"
            logcat_path = group_dir / "logcat.txt"
            analysis = analysis_path.read_text(encoding="utf-8")
            logcat = logcat_path.read_text(encoding="utf-8")

            self.assertIn("### Input / Window / Surface evidence digest", analysis)
            self.assertIn("retrieval relevance, not root-cause confidence", analysis)
            self.assertIn("not Perfetto or SF scheduling proof", analysis)
            self.assertIn("family=`input_trigger`", analysis)
            self.assertIn("family=`window_state`", analysis)
            self.assertIn("family=`surface_state`", analysis)
            self.assertIn("过滤后的 Logcat 已单独保存为：`logcat.txt`", analysis)
            self.assertLess(
                analysis.index("### Input / Window / Surface evidence digest"),
                analysis.index("### Logcat 系统日志"),
            )

            # Digest deduplication is presentation-only: both timestamped raw
            # trigger occurrences remain in the full filtered logcat artifact.
            self.assertIn("10:00:04.700 E InputDispatcher", logcat)
            self.assertIn("10:00:04.900 E InputDispatcher", logcat)
            self.assertEqual(logcat.count("Input dispatching timed out for com.demo"), 2)

            self.assertEqual(index["artifactPaths"], {"index": str(Path(tmpdir, "index.json"))})
            self.assertEqual(
                set(index["groups"][0]["artifactPaths"]),
                {"analysis", "logcat"},
            )
            self.assertEqual(
                set(index["groups"][0]["analysisSlots"]),
                {"trace", "eventlog", "logcat-anrmanager", "final-anr"},
            )
            self.assertEqual(analysis.count("<!-- AI_ANALYSIS_SLOT:"), 4)
            digest_events = [
                event for event in index["events"]
                if event["step"] == "input_window_digest_built"
            ]
            self.assertEqual(len(digest_events), 1)
            self.assertEqual(digest_events[0]["details"]["candidateLineCount"], 4)
            self.assertEqual(digest_events[0]["details"]["retainedItemCount"], 3)
            self.assertEqual(digest_events[0]["details"]["duplicateCount"], 1)

    def test_no_focus_strategy_builds_digest_and_exposes_it_on_core_result(self) -> None:
        result = build_ai_context(
            _package_with_digest_signals(),
            AiContextOptions(package_name="com.demo", anr_type="no_focus_window"),
        )

        digest = result.groups[0]["inputWindowDigest"]
        self.assertEqual(set(digest["countsByFamily"]), {"input_trigger", "window_state", "surface_state"})
        self.assertLessEqual(len(digest["items"]), 12)
        self.assertTrue(any(event["step"] == "input_window_digest_built" for event in result.events))

    def test_non_input_strategy_omits_digest_section_and_event(self) -> None:
        package = _package_with_digest_signals()
        package["sources"]["event_log"]["content"] = (
            "04-12 10:00:05.000 am_anr ANR in com.demo: executing service timeout"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            summary = build_ai_context_artifacts(
                package,
                AiContextOptions(
                    out_dir=tmpdir,
                    package_name="com.demo",
                    anr_type="service_timeout",
                ),
            )
            group_dir = Path(tmpdir, summary["groups"][0]["id"])
            analysis = (group_dir / "anr_analysis.md").read_text(encoding="utf-8")

        self.assertNotIn("Input / Window / Surface evidence digest", analysis)
        self.assertFalse(any(event["step"] == "input_window_digest_built" for event in summary["events"]))

    def test_regeneration_preserves_filled_slot_alongside_digest(self) -> None:
        package = _package_with_digest_signals()
        options = AiContextOptions(
            package_name="com.demo",
            anr_type="input_dispatching_timeout",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            options = AiContextOptions(**{**options.__dict__, "out_dir": tmpdir})
            summary = build_ai_context_artifacts(package, options)
            analysis_path = Path(summary["groups"][0]["artifactPaths"]["analysis"])
            content = analysis_path.read_text(encoding="utf-8")
            marker = "<!-- AI_ANALYSIS_SLOT:trace -->"
            start = content.index(marker) + len(marker)
            end = content.index("\n### EventLog 事件日志", start)
            analysis_path.write_text(
                content[:start] + "\nTrace conclusion retained.\n" + content[end:],
                encoding="utf-8",
            )

            regenerated = build_ai_context_artifacts(package, options)
            regenerated_text = analysis_path.read_text(encoding="utf-8")

        self.assertIn("Trace conclusion retained.", regenerated_text)
        self.assertIn("### Input / Window / Surface evidence digest", regenerated_text)
        self.assertEqual(regenerated["groups"][0]["analysisSlots"]["trace"], "filled")


if __name__ == "__main__":
    unittest.main()
