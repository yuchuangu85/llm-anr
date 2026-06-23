"""Tests for load_package_from_path — universal auto-detect loader."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from anr_evidence.extractor import (
    ArchiveLoadError,
    _find_archives_in_directory,
    load_package_from_path,
)
from anr_evidence import load_package_from_fixture


class LoadPathTests(unittest.TestCase):
    def setUp(self):
        self.ROOT = Path(__file__).resolve().parent.parent

    def test_load_json_fixture(self) -> None:
        package = load_package_from_path(self.ROOT / "tests/fixtures/nfw_01.json")
        self.assertIsNotNone(package.get("sources"))

    def test_load_archive_directly(self) -> None:
        archive = self.ROOT / "samples/replay/assets/bugreport_archive.zip"
        if archive.exists():
            package = load_package_from_path(archive)
            self.assertIsNotNone(package.get("sources"))

    def test_load_directory_with_nested_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            # Create a tiny bugreport zip
            trace_content = "\n".join([
                "04-12 10:00:04.000 ----- pid 100 -----",
                "Cmd line: com.demo",
                '  "main" tid=1 Native',
                "  | sysTid=100 nice=0",
            ])
            event_content = "04-12 10:00:05.000 I am_anr: [0,100,com.demo,1]"
            logcat_content = "04-12 10:00:06.000 I AnrManager: dumpAnrDebugInfo end: ProcessRecord{100:com.demo}"
            kernel_content = "04-12 10:00:05.000 binder"

            zip_path = tmpdir / "bugreport-NFW-01.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("data/anr/traces.txt", trace_content)
                zf.writestr("events/events.txt", event_content)
                zf.writestr("logs/logcat_main.txt", logcat_content)
                zf.writestr("kernel/last_kmsg", kernel_content)

            package = load_package_from_path(tmpdir)
            self.assertIsNotNone(package)
            self.assertIsNotNone(package.get("sources"))
            self.assertIn("trace", package["sources"])
            self.assertTrue(package["sources"]["trace"]["content"])

    def test_load_directory_with_multiple_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            # First archive: trace + event
            zip1 = tmpdir / "bugreport-part1.zip"
            with zipfile.ZipFile(zip1, "w") as zf:
                zf.writestr("data/anr/traces.txt", "Cmd line: com.demo\n----- pid 100 -----")
                zf.writestr("events/events.txt", "04-12 10:00:05 I am_anr: [0,100,com.demo,1]")

            # Second archive: logcat + kernel
            zip2 = tmpdir / "bugreport-part2.zip"
            with zipfile.ZipFile(zip2, "w") as zf:
                zf.writestr("logs/logcat_main.txt", "04-12 10:00:06 W timeout")
                zf.writestr("kernel/last_kmsg", "04-12 10:00:05 hung task")

            package = load_package_from_path(tmpdir)
            self.assertIsNotNone(package.get("sources"))
            self.assertIn("trace", package["sources"])
            self.assertIn("event_log", package["sources"])
            self.assertIn("logcat", package["sources"])


    def test_directory_with_complete_smart_loose_sources_skips_archive_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            (tmpdir / 'System_log/anr').mkdir(parents=True)
            (tmpdir / 'System_log/System_MT_logcat_event_04_12_10_00_00.txt').write_text(
                '04-12 10:05:00.000 am_anr ANR in com.demo: Input dispatching timed out\n',
                encoding='utf-8',
            )
            (tmpdir / 'System_log/System_MT_logcat_04_12_10_00_00.txt').write_text(
                '04-12 10:05:00.100 E InputDispatcher target timeout for com.demo\n',
                encoding='utf-8',
            )
            (tmpdir / 'System_log/anr/anr_2026-04-12-10-05-00-000').write_text(
                '----- pid 100 at 2026-04-12 10:05:00.000000000+0800 -----\nCmd line: com.demo\n',
                encoding='utf-8',
            )
            with zipfile.ZipFile(tmpdir / 'bugreport.zip', 'w') as zf:
                zf.writestr('logs/logcat_main.txt', '04-12 10:05:00.100 E InputDispatcher stale archive timeout\n')

            with unittest.mock.patch('anr_evidence.loaders.core.load_package_from_archive', side_effect=AssertionError('archive should not be loaded')):
                package = load_package_from_path(tmpdir, package_name='com.demo')

        self.assertIn('target timeout', package['sources']['logcat']['content'])
        self.assertNotIn('stale archive timeout', package['sources']['logcat']['content'])

    def test_load_path_not_exists(self) -> None:
        with self.assertRaises(ArchiveLoadError):
            load_package_from_path("/nonexistent/path")

    def test_find_archives_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archives = _find_archives_in_directory(Path(tmp))
            self.assertEqual(len(archives), 0)

    def test_find_archives_finds_zips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "bugreport.zip").touch()
            (tmpdir / "data").mkdir()
            archives = _find_archives_in_directory(tmpdir)
            self.assertEqual(len(archives), 1)
            self.assertEqual(archives[0].name, "bugreport.zip")
