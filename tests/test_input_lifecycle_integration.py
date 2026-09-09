from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from anr_evidence.ai_context import AiContextOptions, build_ai_context, build_ai_context_artifacts


class InputLifecycleIntegrationTests(unittest.TestCase):
    def _package(self) -> dict[str, object]:
        return {
            "package_id": "input-lifecycle-integration",
            "sources": {
                "event_log": {
                    "path": "events.txt",
                    "content": "04-12 10:00:05.000 1000 1000 I am_anr  : [0,1234,com.demo,1,no focused window]",
                },
                "trace": {
                    "path": "traces.txt",
                    "content": "04-12 10:00:05.100 ----- pid 1234 at 2026-04-12 10:00:05.100000000+0000 -----\nCmd line: com.demo\nmain tid=1 Native: waiting because no focused window",
                },
                "logcat": {
                    "path": "logcat.txt",
                    "content": "\n".join(
                        [
                            "04-12 10:00:03.000 I TaskAnimationManager: startRecentsAnimation handler=0x1 token=window-1",
                            "04-12 10:00:03.100 I TaskAnimationManager: mRecentsAnimationStartPending=true handler=0x1 token=window-1",
                            "04-12 10:00:04.000 I input_focus: [Focus entering recents_animation_input_consumer,reason=setFocusedWindow] token=window-1",
                            "04-12 10:00:04.900 E InputDispatcher: Application does not have a focused window for com.demo pid=1234 token=window-1",
                        ]
                    ),
                },
            },
        }

    def test_context_exposes_structured_lifecycle_and_source_specific_slot_guidance(self) -> None:
        result = build_ai_context(self._package(), AiContextOptions(package_name="com.demo", anr_type="no_focus_window"))
        group = result.groups[0]
        lifecycle = group["inputLifecycle"]

        self.assertEqual(lifecycle["trigger"]["kind"], "no_focus")
        self.assertEqual(lifecycle["originalTarget"]["name"], "com.demo")
        self.assertEqual(lifecycle["blameTarget"]["name"], "recents_animation_input_consumer")
        self.assertEqual(lifecycle["recentsLifecycle"]["status"], "pending_at_anchor")
        self.assertTrue(any(event["step"] == "input_lifecycle_built" for event in result.events))

        with tempfile.TemporaryDirectory() as tmpdir:
            summary = build_ai_context_artifacts(
                self._package(),
                AiContextOptions(out_dir=tmpdir, package_name="com.demo", anr_type="no_focus_window"),
            )
            analysis = Path(summary["groups"][0]["artifactPaths"]["analysis"]).read_text(encoding="utf-8")

        self.assertIn("### Input / WMS / AMS / Recents structured lifecycle (D1/D2)", analysis)
        self.assertIn("original target", analysis)
        self.assertIn("D1/D2 inputLifecycle", analysis)
        self.assertIn("只分析 Trace / 死锁检测 / Trace 线索，以及 DQ1 dump PID/time association", analysis)
        self.assertIn("只分析 EventLog 与 Anchor", analysis)
        self.assertIn("先读取同目录 `logcat.txt` 中的过滤后 Logcat", analysis)
        self.assertIn("仅在 Trace、EventLog、Logcat/AnrManager 三段专项分析完成后填写", analysis)
        self.assertEqual(sum(line.startswith("#### AI Analysis — ") for line in analysis.splitlines()), 4)


if __name__ == "__main__":
    unittest.main()
