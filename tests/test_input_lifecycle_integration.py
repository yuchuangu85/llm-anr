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

    def test_dumpsys_snapshot_is_projected_without_promoting_blame_or_dump_lifecycle(self) -> None:
        package = self._package()
        sources = package["sources"]
        assert isinstance(sources, dict)
        sources["dumpsys_input"] = {
            "path": "dumpsys/input.txt",
            "content": """Input Dispatcher State:
  FocusedWindow: Window{a1b2c3 u0 com.demo/.MainActivity} inputChannelToken=Token{input-1} mDisplayId=0
  PendingEvents: seq=17 target=Window{a1b2c3 u0 com.demo/.MainActivity}
""",
        }
        sources["dumpsys_window"] = {
            "path": "dumpsys/window.txt",
            "content": """WINDOW MANAGER WINDOWS (dumpsys window windows)
  mCurrentFocus=Window{a1b2c3 u0 com.demo/.MainActivity}
  Window #0 Window{a1b2c3 u0 com.demo/.MainActivity}:
    mDisplayId=0
    mSession=Session{abcd 1234:u0a123}
    mInputChannelToken=Token{input-1}
    mHasSurface=true mViewVisibility=0x0
""",
        }

        result = build_ai_context(package, AiContextOptions(package_name="com.demo", anr_type="no_focus_window"))
        group = result.groups[0]
        dump = group["dumpQuality"]
        snapshot = dump["dumpsysSnapshot"]

        self.assertEqual(snapshot["inputStatus"], "observed")
        self.assertEqual(snapshot["windowStatus"], "observed")
        self.assertEqual(snapshot["queueStatus"], "pending")
        self.assertEqual(snapshot["transferStatus"], "matched")
        self.assertEqual(snapshot["blameTransfer"]["status"], "unqualified")
        self.assertEqual(snapshot["blameTransfer"]["reason"], "dumpsys_capture_time_unavailable")
        self.assertEqual(dump["lifecycleStatus"], "unknown")
        self.assertEqual(dump["dumpLifecycle"]["status"], "observed")
        self.assertEqual(dump["dumpLifecycle"]["captureTimeStatus"], "unavailable")
        self.assertFalse(dump["eligibleForHighCausalConfidence"])
        self.assertEqual(group["inputLifecycle"]["dump"]["dumpsysSnapshot"], snapshot)
        self.assertEqual(group["inputLifecycle"]["dump"]["dumpLifecycle"], dump["dumpLifecycle"])

        with tempfile.TemporaryDirectory() as tmpdir:
            summary = build_ai_context_artifacts(
                package,
                AiContextOptions(out_dir=tmpdir, package_name="com.demo", anr_type="no_focus_window"),
            )
            analysis = Path(summary["groups"][0]["artifactPaths"]["analysis"]).read_text(encoding="utf-8")
        self.assertIn("Shared DQ1 dump lifecycle", analysis)
        self.assertIn("Dumpsys snapshot", analysis)
        self.assertIn("blame transfer=`unqualified`", analysis)
        self.assertIn("cannot establish blame", analysis)

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
