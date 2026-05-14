from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from anr_evidence import (
    SourceFilterContext,
    filter_event_log_source,
    filter_logcat_source,
    filter_trace_source,
    load_package_from_fixture,
    run_filter_workflow,
    trace_anr_timestamp_from_entries,
)
from anr_evidence.loaders.package import build_package_from_entries
from anr_evidence.extractor import collect_anchor_candidates, resolve_anchor
from anr_evidence.sources.shared import parse_raw_timestamp, select_preceding_entries_for_anchor

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


class SourceWorkflowTests(unittest.TestCase):
    def test_trace_entrypoint_parses_trace_anr_time_and_builds_evidence(self) -> None:
        source = {
            "path": "anr_2026-04-06-08-41-06-491",
            "content": "----- pid 1 at 2026-04-06 08:41:06.491000000+0800 -----\nCmd line: com.demo\nmain tid=1 input dispatching timeout\n",
            "readable": True,
        }

        result = filter_trace_source(source, SourceFilterContext())

        self.assertEqual(result.source_kind, "trace")
        self.assertEqual(result.evidence[0]["id"], "trace_core")
        self.assertEqual(str(result.metadata["traceAnrTimestamp"]), "2026-04-06 08:41:06.491000")
        self.assertEqual(trace_anr_timestamp_from_entries([source]), result.metadata["traceAnrTimestamp"])

    def test_shared_predecessor_selection_uses_trace_anchor(self) -> None:
        entries = [
            {"path": "System_MT_logcat_04_06_08_39_58.txt", "content": "before", "readable": True},
            {"path": "System_MT_logcat_04_06_08_47_49.txt", "content": "after", "readable": True},
        ]

        selected = select_preceding_entries_for_anchor(entries, parse_raw_timestamp("04-06 08:41:06.491"))

        self.assertEqual([entry["path"] for entry in selected], ["System_MT_logcat_04_06_08_39_58.txt"])

    def test_package_name_prefers_matching_trace_for_sharded_event_log_selection(self) -> None:
        entries = [
            {
                "path": "System_log/anr/anr_2026-04-30-20-47-27-801",
                "content": "----- pid 1 at 2026-04-30 20:47:27.801000000+0800 -----\nCmd line: other.app\n",
                "readable": True,
            },
            {
                "path": "System_log/anr/anr_2026-05-03-10-00-57-490",
                "content": "----- pid 2 at 2026-05-03 10:00:57.490000000+0800 -----\nCmd line: com.demo\n",
                "readable": True,
            },
            {
                "path": "System_log/System_MT_logcat_event_04_30_20_50_00.txt",
                "content": "04-30 20:47:27.801 am_anr ANR in other.app\n",
                "readable": True,
            },
            {
                "path": "System_log/System_MT_logcat_event_05_03_09_16_13.txt",
                "content": "05-03 10:00:57.460 am_anr ANR in com.demo\n",
                "readable": True,
            },
            {
                "path": "System_log/System_MT_logcat_event_05_03_10_20_00.txt",
                "content": "05-03 10:10:00.000 input_focus later\n",
                "readable": True,
            },
            {
                "path": "System_log/System_MT_logcat_05_03_09_57_54.txt",
                "content": "05-03 10:00:57.410 E InputDispatcher no focused window for com.demo\n",
                "readable": True,
            },
            {
                "path": "System_log/System_MT_logcat_05_03_10_20_00.txt",
                "content": "05-03 10:10:00.000 I later\n",
                "readable": True,
            },
        ]

        package = build_package_from_entries("demo", entries, package_name="com.demo")

        self.assertIn("anr_2026-05-03-10-00-57-490", package["sources"]["trace"]["path"])
        self.assertIn("System_MT_logcat_event_05_03_09_16_13.txt", package["sources"]["event_log"]["path"])
        self.assertIn("com.demo", package["sources"]["event_log"]["content"])
        self.assertNotIn("other.app", package["sources"]["event_log"]["content"])

    def test_event_log_and_logcat_entrypoints_filter_independently(self) -> None:
        anchor_dt = parse_raw_timestamp("04-12 10:00:05.000")
        event_result = filter_event_log_source(
            {"path": "events.txt", "content": "04-12 10:00:04.000 input_focus demo\n04-12 10:00:05.000 am_anr ANR in com.demo\n"},
            SourceFilterContext(anchor_dt=anchor_dt),
        )
        logcat_result = filter_logcat_source(
            {"path": "logcat.txt", "content": "04-12 10:00:04.900 E InputDispatcher Input dispatching timed out\n"},
            SourceFilterContext(anchor_dt=anchor_dt),
        )

        self.assertEqual([item["id"] for item in event_result.evidence], ["event_am_anr", "event_pre_window"])
        self.assertEqual(logcat_result.evidence[0]["id"], "logcat_anchor_window")

    def test_workflow_preserves_phase1_source_evidence_order(self) -> None:
        package = load_package_from_fixture(ROOT / "tests/fixtures/nfw_01.json")
        anchors = resolve_anchor(collect_anchor_candidates(package))

        result = run_filter_workflow(package, anchors)

        self.assertEqual([item["sourceKind"] for item in result.evidence], ["trace", "event_log", "event_log", "logcat", "kernel_log"])
        self.assertEqual(result.evidence[0]["id"], "trace_core")
        self.assertFalse(result.warnings)

    def test_new_cli_entrypoints_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            trace = root / "anr_2026-04-06-08-41-06-491"
            trace.write_text("----- pid 1 at 2026-04-06 08:41:06.491000000+0800 -----\nCmd line: com.demo\nmain tid=1 input dispatching timeout\n", encoding="utf-8")
            events = root / "events.txt"
            events.write_text("04-06 08:41:06.000 am_anr ANR in com.demo\n", encoding="utf-8")
            logcat = root / "logcat.txt"
            logcat.write_text("04-06 08:41:06.100 E InputDispatcher Input dispatching timed out\n", encoding="utf-8")

            for script, input_path in [
                ("scripts/anr_trace_filter.py", trace),
                ("scripts/anr_event_log_filter.py", events),
                ("scripts/anr_logcat_filter.py", logcat),
            ]:
                completed = subprocess.run(
                    [sys.executable, script, str(input_path), "--anchor", "04-06 08:41:06.491"],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                    env=ENV,
                )
                payload = json.loads(completed.stdout)
                self.assertIn("sourceKind", payload)
                self.assertIn("evidence", payload)

            workflow = subprocess.run(
                [sys.executable, "scripts/anr_filter_workflow.py", "tests/fixtures/nfw_01.json"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env=ENV,
            )
            workflow_payload = json.loads(workflow.stdout)
            self.assertIn("anchors", workflow_payload)
            self.assertIn("evidence", workflow_payload)


if __name__ == "__main__":
    unittest.main()
