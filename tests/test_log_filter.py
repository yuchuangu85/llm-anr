import tempfile
from pathlib import Path
import unittest

from anr_evidence.log_filter import (
    DEFAULT_EVENT_LOG_TAGS,
    KERNEL_SIGNAL_PATTERNS,
    LOGCAT_SIGNAL_PATTERNS,
    LogFilterSpec,
    filter_file_preceding_anchor_window,
    filter_preceding_anchor_window,
    filter_timestamp_window,
    parse_log_timestamp,
    parse_tags_from_markdown,
)


class LogFilterTests(unittest.TestCase):
    def test_event_log_pre_window_keeps_only_tagged_lines(self) -> None:
        content = "\n".join([
            "04-12 10:00:00.000 unrelated_tag com.demo old",
            "04-12 10:00:01.000 wm_focus com.demo focus",
            "04-12 10:00:02.000 random_noise com.demo noisy",
            "04-12 10:00:13.000 am_anr ANR in com.demo",
        ])
        result = filter_preceding_anchor_window(
            content,
            "am_anr",
            LogFilterSpec("event_log", before_seconds=12, include_patterns=DEFAULT_EVENT_LOG_TAGS),
        )
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.lines, [
            "04-12 10:00:01.000 wm_focus com.demo focus",
            "04-12 10:00:13.000 am_anr ANR in com.demo",
        ])

    def test_event_log_package_filter_applies_to_anchor_only_when_requested(self) -> None:
        content = "\n".join([
            "04-12 10:00:00.000 wm_task_created next-app",
            "04-12 10:00:01.000 wm_pause_activity com.demo",
            "04-12 10:00:02.000 am_proc_start com.next",
            "04-12 10:00:13.000 am_anr ANR in com.demo",
        ])
        result = filter_preceding_anchor_window(
            content,
            "am_anr",
            LogFilterSpec(
                "event_log",
                before_seconds=12,
                include_patterns=DEFAULT_EVENT_LOG_TAGS,
                package_name="com.demo",
                package_filter_scope="anchor",
            ),
        )
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.lines, [
            "04-12 10:00:01.000 wm_pause_activity com.demo",
            "04-12 10:00:02.000 am_proc_start com.next",
            "04-12 10:00:13.000 am_anr ANR in com.demo",
        ])

    def test_default_event_log_tags_cover_docs_master(self) -> None:
        doc_tags = parse_tags_from_markdown([Path("docs/event_log_tags_master.md")])
        self.assertTrue(doc_tags)
        self.assertTrue(doc_tags.issubset(DEFAULT_EVENT_LOG_TAGS))

    def test_logcat_and_kernel_share_anchor_window_filtering(self) -> None:
        anchor = parse_log_timestamp("04-12 10:00:10.000 am_anr ANR in com.demo")
        logcat = "\n".join([
            "04-12 10:00:08.000 I Choreographer skipped frames",
            "04-12 10:00:09.000 E InputDispatcher Input dispatching timed out",
        ])
        kernel = "\n".join([
            "04-12 10:00:09.000 random kernel line",
            "04-12 10:00:11.000 binder: backlog",
        ])
        logcat_result = filter_timestamp_window(logcat, anchor, LogFilterSpec("logcat", 5, 5, LOGCAT_SIGNAL_PATTERNS), fallback_label="logcat")
        kernel_result = filter_timestamp_window(kernel, anchor, LogFilterSpec("kernel_log", 5, 5, KERNEL_SIGNAL_PATTERNS), fallback_label="kernel")
        self.assertEqual(logcat_result.lines, ["04-12 10:00:09.000 E InputDispatcher Input dispatching timed out"])
        self.assertEqual(kernel_result.lines, ["04-12 10:00:11.000 binder: backlog"])

    def test_large_file_scanner_does_not_require_full_content_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "events.log"
            path.write_text("\n".join([
                "04-12 10:00:00.000 wm_focus old",
                "04-12 10:00:08.000 input_focus target",
                "04-12 10:00:09.000 noise target",
                "04-12 10:00:10.000 am_anr ANR in target",
            ]), encoding="utf-8")
            result = filter_file_preceding_anchor_window(
                path,
                "am_anr",
                LogFilterSpec("event_log", before_seconds=3, include_patterns=DEFAULT_EVENT_LOG_TAGS),
                chunk_size=16,
            )
        self.assertEqual(result.lines, [
            "04-12 10:00:08.000 input_focus target",
            "04-12 10:00:10.000 am_anr ANR in target",
        ])


if __name__ == "__main__":
    unittest.main()
