from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import unittest

from anr_evidence.anr_strategy import ANR_TYPE_STRATEGIES
from anr_evidence.log_filter import LOGCAT_SIGNAL_PATTERNS, LOGCAT_SYSTEM_CONTEXT_TAGS


ROOT = Path(__file__).resolve().parent.parent
REFERENCE = ROOT / "docs/logcat-tags-reference.md"


class LogcatReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = REFERENCE.read_text(encoding="utf-8")
        blocks = re.findall(r"^```json\n(.*?)^```", self.text, re.MULTILINE | re.DOTALL)
        self.assertEqual(len(blocks), 1)
        self.config = json.loads(blocks[0])

    def test_documented_pattern_and_system_tag_sets_match_implementation(self) -> None:
        self.assertCountEqual(self.config["baselinePatterns"], LOGCAT_SIGNAL_PATTERNS)
        self.assertCountEqual(self.config["systemContextTags"], LOGCAT_SYSTEM_CONTEXT_TAGS)

    def test_documented_type_windows_match_the_strategy_registry(self) -> None:
        expected = {
            kind: {"before": strategy.logcat_before_seconds, "after": strategy.logcat_after_seconds}
            for kind, strategy in ANR_TYPE_STRATEGIES.items()
        }
        self.assertEqual(self.config["aiContextWindowsSeconds"], expected)

    def test_reference_links_and_navigation_are_valid(self) -> None:
        links = re.findall(r"\]\(([^)]+)\)", self.text)
        self.assertTrue(links)
        for link in links:
            with self.subTest(link=link):
                self.assertTrue((REFERENCE.parent / link.split("#", 1)[0]).is_file())
        for file in (
            "docs/event-log-tags-reference.md", "docs/anr-ai-analysis-guide.md", "README.md", "README-zh.md",
        ):
            with self.subTest(file=file):
                self.assertIn("logcat-tags-reference.md", (ROOT / file).read_text(encoding="utf-8"))

    def test_categories_and_filtering_boundaries_are_explicit(self) -> None:
        for category in ("2.1 ActivityManager", "2.2 WindowManager", "2.3 电源与电池", "2.4 输入分发"):
            self.assertIn(category, self.text)
        for boundary in (
            "不是新的可执行配置", "不是严格 tag 匹配", "不是系统 ANR 超时阈值",
            "system_or_package", "不是“无条件保留该 tag”", "完整 AnrManager",
            "原始 12 秒日志", "不是通用集合的简单超集", "missing-anchor", "empty-anchor-window",
            "至多 25 行", "不保证复现", "executed=false",
        ):
            self.assertIn(boundary, self.text)

    def test_documented_basic_filter_example_uses_the_requested_window(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/anr_logcat_filter.py", "tests/fixtures/idt_01.json", "--anchor", "04-12 11:00:03.000"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        result = json.loads(completed.stdout)
        self.assertEqual(result["sourceKind"], "logcat")
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["lines"], [
            "04-12 11:00:03.050 E InputDispatcher Input dispatching timed out (Waiting because the touched window has not finished processing)",
        ])

    def test_omitting_anchor_is_documented_as_a_fallback_not_a_window(self) -> None:
        completed = subprocess.run(
            [sys.executable, "scripts/anr_logcat_filter.py", "tests/fixtures/idt_01.json"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        result = json.loads(completed.stdout)
        self.assertIn("missing-anchor", [warning["code"] for warning in result["warnings"]])
        self.assertIn("04-12 11:00:02.000 I random noise", result["lines"])
        self.assertLessEqual(len(result["lines"]), 25)


if __name__ == "__main__":
    unittest.main()
