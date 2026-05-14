from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from datetime import datetime

from anr_evidence import (
    FilterWorkflowOptions,
    MeminfoFilterOptions,
    SourceFilterContext,
    filter_meminfo_source,
    load_package_from_directory,
    parse_meminfo_snapshots,
    run_filter_workflow,
)

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def _meminfo_sample() -> str:
    return "\n".join([
        "================================",
        "2026-05-03 10:00:50",
        "================================",
        "Applications Memory Usage (in Kilobytes):",
        "Total RSS by OOM adjustment:",
        "    300,000K: Foreground",
        "        210,000K: com.demo (pid 100 / activities)",
        "         90,000K: com.other.hog (pid 200)",
        "Total PSS by OOM adjustment:",
        "    220,000K: Foreground",
        "        150,000K: com.demo (pid 100 / activities)",
        "         70,000K: com.other.hog (pid 200)",
        "Total RAM: 1,000,000K (status normal)",
        " Free RAM: 400,000K",
        " Used RAM: 600,000K",
        " Lost RAM: 0K",
        "================================",
        "2026-05-03 10:01:50",
        "================================",
        "Applications Memory Usage (in Kilobytes):",
        "Total RSS by OOM adjustment:",
        "    600,000K: Foreground",
        "        360,000K: com.demo (pid 100 / activities)",
        "        240,000K: com.other.hog (pid 200)",
        "Total PSS by OOM adjustment:",
        "    510,000K: Foreground",
        "        310,000K: com.demo (pid 100 / activities)",
        "        200,000K: com.other.hog (pid 200)",
        "Total RAM: 1,000,000K (status low)",
        " Free RAM: 100,000K",
        " Used RAM: 900,000K",
        " Lost RAM: 0K",
    ])


class MeminfoFilterTests(unittest.TestCase):
    def test_parse_meminfo_snapshots_and_filter_target_history(self) -> None:
        source = {"path": "System_log/meminfo.txt", "content": _meminfo_sample(), "readable": True}

        snapshots = parse_meminfo_snapshots(source["content"])
        result = filter_meminfo_source(
            source,
            SourceFilterContext(package_name="com.demo"),
            MeminfoFilterOptions(package_name="com.demo", high_processes=("com.other.hog",), top_n=1, include_all_snapshots=True),
        )

        self.assertEqual(len(snapshots), 2)
        self.assertEqual(result.source_kind, "meminfo")
        self.assertEqual(result.metadata["snapshotCount"], 2)
        self.assertEqual(result.metadata["targetHistory"][0]["pssKb"], 150000)
        self.assertEqual(result.metadata["targetHistory"][1]["rssKb"], 360000)
        self.assertEqual(result.metadata["targetHistory"][-1]["system"].get("status"), "low")
        self.assertIn("com.other.hog", "\n".join(result.lines))
        self.assertIn("com.demo", "\n".join(result.lines))

    def test_meminfo_filter_selects_snapshot_nearest_anchor(self) -> None:
        source = {"path": "System_log/meminfo.txt", "content": _meminfo_sample(), "readable": True}

        result = filter_meminfo_source(
            source,
            SourceFilterContext(
                package_name="com.demo",
                anchor_dt=datetime.strptime("2026-05-03 10:00:50", "%Y-%m-%d %H:%M:%S"),
            ),
            MeminfoFilterOptions(package_name="com.demo", high_processes=("com.other.hog",), top_n=1),
        )

        self.assertEqual(result.metadata["selectedTimestamp"], "2026-05-03 10:00:50")
        target_history = result.metadata["targetHistory"]
        nearest = [h for h in target_history if h.get("timestamp") == "2026-05-03 10:00:50"]
        self.assertTrue(len(nearest) > 0)
        self.assertIn("2026-05-03 10:00:50", "\n".join(result.lines))

    def test_meminfo_cli_loads_system_log_meminfo(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "System_log").mkdir()
            (root / "System_log" / "meminfo.txt").write_text(_meminfo_sample(), encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/anr_meminfo_filter.py",
                    str(root),
                    "--package",
                    "com.demo",
                    "--process",
                    "com.other.hog",
                    "--top",
                    "2",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env=ENV,
            )

        payload = json.loads(completed.stdout)
        self.assertEqual(payload["sourceKind"], "meminfo")
        self.assertIn("System_log/meminfo.txt", payload["evidence"][0]["provenance"]["sourcePath"])
        self.assertIn("com.demo", "\n".join(payload["lines"]))
        self.assertIn("com.other.hog", "\n".join(payload["lines"]))

    def test_directory_workflow_runs_meminfo_after_logcat(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data/anr").mkdir(parents=True)
            (root / "events").mkdir()
            (root / "logs").mkdir()
            (root / "kernel").mkdir()
            (root / "System_log").mkdir()
            (root / "data/anr/traces.txt").write_text("05-03 10:01:50.100 Cmd line: com.demo\nmain tid=1 Native\n", encoding="utf-8")
            (root / "events/events.txt").write_text("05-03 10:01:50.000 am_anr ANR in com.demo\n", encoding="utf-8")
            (root / "logs/logcat.txt").write_text("05-03 10:01:50.010 E InputDispatcher Input dispatching timed out for com.demo\n", encoding="utf-8")
            (root / "kernel/kmsg.txt").write_text("05-03 10:01:50.020 sched: pressure\n", encoding="utf-8")
            (root / "System_log/meminfo.txt").write_text(_meminfo_sample(), encoding="utf-8")

            package = load_package_from_directory(root, package_name="com.demo")
            result = run_filter_workflow(
                package,
                {"primary_anchor": {"timestamp": "05-03 10:01:50.000", "sourceKind": "event_log", "line": "am_anr ANR in com.demo"}},
                FilterWorkflowOptions(package_name="com.demo", high_load_processes=("com.other.hog",)),
            )

        self.assertIn("meminfo", package["sources"])
        self.assertEqual(
            [item["sourceKind"] for item in result.evidence],
            ["trace", "event_log", "event_log", "logcat", "meminfo", "kernel_log"],
        )
        self.assertIn("meminfo", result.source_results)
        self.assertIn("com.other.hog", "\n".join(result.source_results["meminfo"].lines))


if __name__ == "__main__":
    unittest.main()
