from __future__ import annotations

from datetime import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anr_evidence import extract_evidence_package, load_package_from_directory
from anr_evidence.loaders.core import find_event_anr_timestamp_by_command

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}


class DirectoryLoadingTests(unittest.TestCase):
    def test_command_anchor_prefers_rg_when_available(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("anr_evidence.loaders.core.shutil.which") as which,
            patch("anr_evidence.loaders.core.subprocess.run") as run,
        ):
            root = Path(tmpdir)
            which.side_effect = lambda name: "/usr/bin/rg" if name == "rg" else None
            run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    "events.txt:1:04-12 10:00:05.000 am_anr ANR in other.app\n"
                    "events.txt:2:05-03 10:00:57.460 am_anr ANR in com.demo\n"
                ),
            )

            timestamp = find_event_anr_timestamp_by_command(root, "com.demo")

            self.assertEqual(timestamp, datetime(2026, 5, 3, 10, 0, 57, 460000))
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0], ["/usr/bin/rg", "-n", "--fixed-strings", "am_anr", str(root)])

    def test_command_anchor_falls_back_to_grep(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("anr_evidence.loaders.core.shutil.which") as which,
            patch("anr_evidence.loaders.core.subprocess.run") as run,
        ):
            root = Path(tmpdir)
            which.side_effect = lambda name: "/usr/bin/grep" if name == "grep" else None
            run.return_value = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout="events.txt:9:04-06 08:41:06.000 am_anr ANR in com.demo\n",
            )

            timestamp = find_event_anr_timestamp_by_command(root, "com.demo")

            self.assertEqual(timestamp, datetime(2026, 4, 6, 8, 41, 6))
            self.assertEqual(run.call_args.args[0], ["/usr/bin/grep", "-R", "-n", "-F", "-I", "am_anr", str(root)])

    def test_command_anchor_returns_none_when_no_search_command_exists(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("anr_evidence.loaders.core.shutil.which", return_value=None),
            patch("anr_evidence.loaders.core.subprocess.run") as run,
        ):
            timestamp = find_event_anr_timestamp_by_command(Path(tmpdir), "com.demo")

            self.assertIsNone(timestamp)
            run.assert_not_called()

    def test_command_anchor_failure_does_not_block_python_fallback(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("anr_evidence.loaders.core.shutil.which") as which,
            patch("anr_evidence.loaders.core.subprocess.run", side_effect=subprocess.TimeoutExpired(["rg"], 8)),
        ):
            root = Path(tmpdir) / 'command-timeout'
            root.mkdir(parents=True)
            which.side_effect = lambda name: "/usr/bin/rg" if name == "rg" else None
            (root / 'anr_2026-04-06-08-41-06-491').write_text(
                '----- pid 100 at 2026-04-06 08:41:06.491000000+0800 -----\n'
                'Cmd line: com.demo\n',
                encoding='utf-8',
            )
            (root / 'events.txt').write_text(
                '04-06 08:41:06.000 am_anr ANR in com.demo\n',
                encoding='utf-8',
            )

            package = load_package_from_directory(root, package_name="com.demo")

            self.assertIn('event_log', package['sources'])
            self.assertIn('com.demo', package['sources']['event_log']['content'])

    def test_nested_bugreport_like_directory_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'bugreport-demo'
            (root / 'FS/data/anr').mkdir(parents=True)
            (root / 'FS/logs').mkdir(parents=True)
            (root / 'BUGREPORT/event-log').mkdir(parents=True)
            (root / 'SYS/kernel').mkdir(parents=True)

            (root / 'FS/data/anr/traces.txt').write_text(
                '04-12 10:00:05.100 ----- pid 100 -----\nmain tid=1 Native waiting because no focused window\n',
                encoding='utf-8',
            )
            (root / 'FS/logs/logcat_main.txt').write_text(
                '04-12 10:00:05.050 E InputDispatcher no focused window\n',
                encoding='utf-8',
            )
            (root / 'FS/logs/logcat_system.txt').write_text(
                '04-12 10:00:05.060 I ActivityManager after anr\n',
                encoding='utf-8',
            )
            (root / 'BUGREPORT/event-log/events.txt').write_text(
                '04-12 10:00:05.000 am_anr ANR in com.demo: No focused window\n',
                encoding='utf-8',
            )
            (root / 'SYS/kernel/last_kmsg').write_text(
                '04-12 10:00:05.070 binder: backlog\n',
                encoding='utf-8',
            )

            package = load_package_from_directory(root)
            self.assertEqual(package['package_id'], 'bugreport-demo')
            self.assertIn('trace', package['sources'])
            self.assertIn('logcat', package['sources'])
            self.assertIn('event_log', package['sources'])
            self.assertIn('kernel_log', package['sources'])
            self.assertIn('logcat_main.txt', package['sources']['logcat']['path'])
            self.assertIn('logcat_system.txt', package['sources']['logcat']['path'])
            self.assertIn('ActivityManager after anr', package['sources']['logcat']['content'])

            extracted = extract_evidence_package(package)
            self.assertEqual(extracted['metadata']['status'], 'complete')
            self.assertEqual(extracted['classification']['detectedType'], 'no_focus_window')

    def test_smart_discovery_falls_back_when_trace_is_outside_system_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'mixed-layout'
            (root / 'System_log').mkdir(parents=True)
            (root / 'data/anr').mkdir(parents=True)

            (root / 'System_log/System_MT_logcat_event_04_06_08_17_13.txt').write_text(
                '04-06 08:41:06.000 am_anr ANR in com.demo: Input dispatching timed out\n',
                encoding='utf-8',
            )
            (root / 'System_log/System_MT_logcat_04_06_08_39_58.txt').write_text(
                '04-06 08:41:06.100 E InputDispatcher Input dispatching timed out\n',
                encoding='utf-8',
            )
            (root / 'data/anr/anr_2026-04-06-08-41-06-491').write_text(
                '----- pid 100 at 2026-04-06 08:41:06.491000000+0800 -----\n'
                'Cmd line: com.demo\n'
                'main tid=1 input dispatching timeout\n',
                encoding='utf-8',
            )

            package = load_package_from_directory(root)

            self.assertIn('trace', package['sources'])
            self.assertIn('data/anr/anr_2026-04-06-08-41-06-491', package['sources']['trace']['path'])

    def test_smart_discovery_filters_system_log_trace_candidates_by_trace_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'system-log-traces'
            (root / 'System_log/anr').mkdir(parents=True)

            (root / 'System_log/System_MT_logcat_event_04_06_08_17_13.txt').write_text(
                '04-06 08:41:06.000 am_anr ANR in com.demo: Input dispatching timed out\n',
                encoding='utf-8',
            )
            (root / 'System_log/System_MT_logcat_04_06_08_39_58.txt').write_text(
                '04-06 08:41:06.100 E InputDispatcher Input dispatching timed out\n',
                encoding='utf-8',
            )
            (root / 'System_log/kernel_log.txt').write_text(
                '04-06 08:41:06.200 binder: backlog\n',
                encoding='utf-8',
            )
            (root / 'System_log/anr/anr_2026-04-06-07-00-00-000').write_text(
                '----- pid 100 at 2026-04-06 07:00:00.000000000+0800 -----\n'
                'Cmd line: com.old\n'
                'main tid=1 unrelated old ANR\n',
                encoding='utf-8',
            )
            (root / 'System_log/anr/anr_2026-04-06-08-41-06-491').write_text(
                '----- pid 100 at 2026-04-06 08:41:06.491000000+0800 -----\n'
                'Cmd line: com.demo\n'
                'main tid=1 input dispatching timeout\n',
                encoding='utf-8',
            )

            package = load_package_from_directory(root)

            self.assertEqual(package['sources']['trace']['path'], 'System_log/anr/anr_2026-04-06-08-41-06-491')
            self.assertIn('com.demo', package['sources']['trace']['content'])
            self.assertNotIn('com.old', package['sources']['trace']['content'])

    def test_cli_deliver_accepts_directory_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'bugreport-cli'
            (root / 'data/anr').mkdir(parents=True)
            (root / 'logs').mkdir(parents=True)
            (root / 'events').mkdir(parents=True)
            (root / 'kernel').mkdir(parents=True)
            (root / 'data/anr/traces.txt').write_text('04-12 11:00:03.100 main tid=1 input dispatching timeout\n', encoding='utf-8')
            (root / 'logs/logcat.txt').write_text('04-12 11:00:03.050 E InputDispatcher Input dispatching timed out\n', encoding='utf-8')
            (root / 'events/event.log').write_text('04-12 11:00:03.000 am_anr ANR in com.demo: Input dispatching timed out\n', encoding='utf-8')
            (root / 'kernel/console-ramoops').write_text('04-12 11:00:03.070 sched: pressure\n', encoding='utf-8')

            completed = subprocess.run(
                [sys.executable, '-m', 'anr_evidence', '--deliver', str(root)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env=ENV,
            )
            self.assertIn('# ANR 分析交付稿', completed.stdout)
            self.assertIn('输入分发', completed.stdout)

    def test_content_sniffing_detects_generic_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'generic-layout'
            root.mkdir(parents=True)
            (root / 'blob_a.txt').write_text(
                '04-12 12:00:05.000 am_anr ANR in com.demo: No focused window\\n',
                encoding='utf-8',
            )
            (root / 'blob_b.txt').write_text(
                '04-12 12:00:05.050 E InputDispatcher no focused window\\n',
                encoding='utf-8',
            )
            (root / 'blob_c.txt').write_text(
                '04-12 12:00:05.070 binder: backlog\\n',
                encoding='utf-8',
            )
            (root / 'blob_d.txt').write_text(
                '04-12 12:00:05.100 ----- pid 100 -----\\nCmd line: com.demo\\nmain tid=1 Native waiting because no focused window\\n',
                encoding='utf-8',
            )
            package = load_package_from_directory(root)
            self.assertIn('event_log', package['sources'])
            self.assertIn('logcat', package['sources'])
            self.assertIn('kernel_log', package['sources'])
            self.assertIn('trace', package['sources'])

    def test_logcat_shards_are_aggregated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'sharded-logcat'
            (root / 'logs').mkdir(parents=True)
            (root / 'eventlog').mkdir(parents=True)
            (root / 'traces').mkdir(parents=True)
            (root / 'kernel').mkdir(parents=True)
            (root / 'logs/main.txt').write_text('04-12 13:00:05.050 E InputDispatcher Input dispatching timed out\\n', encoding='utf-8')
            (root / 'logs/system.txt').write_text('04-12 13:00:05.060 I ActivityManager after anr\\n', encoding='utf-8')
            (root / 'logs/radio.txt').write_text('04-12 13:00:05.070 I RILJ radio noise\\n', encoding='utf-8')
            (root / 'eventlog/events.txt').write_text('04-12 13:00:05.000 am_anr ANR in com.demo: Input dispatching timed out\\n', encoding='utf-8')
            (root / 'traces/traces.txt').write_text('04-12 13:00:05.100 main tid=1 input dispatching timeout\\n', encoding='utf-8')
            (root / 'kernel/dmesg').write_text('04-12 13:00:05.080 sched: pressure\\n', encoding='utf-8')
            package = load_package_from_directory(root)
            self.assertIn('main.txt', package['sources']['logcat']['path'])
            self.assertIn('system.txt', package['sources']['logcat']['path'])
            self.assertIn('radio.txt', package['sources']['logcat']['path'])
            self.assertIn('ActivityManager after anr', package['sources']['logcat']['content'])
            logcat_paths = package['sources']['logcat']['path'].split(',')
            self.assertEqual(logcat_paths[0], 'logs/main.txt')
            self.assertEqual(logcat_paths[1], 'logs/system.txt')
            self.assertEqual(logcat_paths[2], 'logs/radio.txt')

    def test_timestamped_event_and_logcat_shards_use_trace_predecessor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'timestamped-shards'
            root.mkdir(parents=True)
            (root / 'anr_2026-04-06-08-41-06-491').write_text(
                '----- pid 100 at 2026-04-06 08:41:06.491000000+0800 -----\n'
                'Cmd line: com.demo\n'
                'main tid=1 input dispatching timeout\n',
                encoding='utf-8',
            )
            (root / 'System_MT_logcat_event_04_06_08_17_13.txt').write_text(
                '04-06 08:40:59.000 am_anr ANR in com.demo: before event shard\n',
                encoding='utf-8',
            )
            (root / 'System_MT_logcat_event_04_06_08_45_00.txt').write_text(
                '04-06 08:45:01.000 am_anr ANR in com.demo: after event shard\n',
                encoding='utf-8',
            )
            (root / 'System_MT_logcat_04_06_08_39_58.txt').write_text(
                '04-06 08:41:06.000 E InputDispatcher before logcat shard\n',
                encoding='utf-8',
            )
            (root / 'System_MT_logcat_04_06_08_47_49.txt').write_text(
                '04-06 08:47:50.000 E InputDispatcher after logcat shard\n',
                encoding='utf-8',
            )

            package = load_package_from_directory(root)

            self.assertEqual(
                package['sources']['event_log']['path'],
                'System_MT_logcat_event_04_06_08_17_13.txt',
            )
            self.assertIn('before event shard', package['sources']['event_log']['content'])
            self.assertNotIn('after event shard', package['sources']['event_log']['content'])
            self.assertEqual(
                package['sources']['logcat']['path'],
                'System_MT_logcat_04_06_08_39_58.txt',
            )
            self.assertIn('before logcat shard', package['sources']['logcat']['content'])
            self.assertNotIn('after logcat shard', package['sources']['logcat']['content'])

    def test_vendor_like_names_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'vendor-layout'
            (root / 'dropbox').mkdir(parents=True)
            (root / 'android-logs').mkdir(parents=True)
            (root / 'events').mkdir(parents=True)
            (root / 'ramoops').mkdir(parents=True)
            (root / 'dropbox/system_app_anr@123.txt').write_text(
                '04-12 14:00:05.100 Cmd line: com.demo\\nmain tid=1 Native: waiting because no focused window\\n',
                encoding='utf-8',
            )
            (root / 'android-logs/logcat_system').write_text(
                '04-12 14:00:05.050 E WindowManager no focused window\\n',
                encoding='utf-8',
            )
            (root / 'events/events-log').write_text(
                '04-12 14:00:05.000 am_anr ANR in com.demo: No focused window\\n',
                encoding='utf-8',
            )
            (root / 'ramoops/console_ramoops').write_text(
                '04-12 14:00:05.070 binder: backlog\\n',
                encoding='utf-8',
            )
            package = load_package_from_directory(root)
            self.assertIn('trace', package['sources'])
            self.assertIn('logcat', package['sources'])
            self.assertIn('event_log', package['sources'])
            self.assertIn('kernel_log', package['sources'])

    def test_duplicate_source_content_is_deduplicated_by_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / 'dup-layout'
            (root / 'logs').mkdir(parents=True)
            (root / 'events').mkdir(parents=True)
            (root / 'traces').mkdir(parents=True)
            (root / 'kernel').mkdir(parents=True)
            duplicate_log = '04-12 16:00:05.050 E InputDispatcher Input dispatching timed out\\n'
            (root / 'logs/main.txt').write_text(duplicate_log, encoding='utf-8')
            (root / 'logs/system.txt').write_text(duplicate_log, encoding='utf-8')
            (root / 'events/events.txt').write_text('04-12 16:00:05.000 am_anr ANR in com.demo: Input dispatching timed out\\n', encoding='utf-8')
            (root / 'traces/traces.txt').write_text('04-12 16:00:05.100 main tid=1 input dispatching timeout\\n', encoding='utf-8')
            (root / 'kernel/last_kmsg').write_text('04-12 16:00:05.070 sched: pressure\\n', encoding='utf-8')
            package = load_package_from_directory(root)
            logcat_paths = package['sources']['logcat']['path'].split(',')
            self.assertEqual(logcat_paths, ['logs/main.txt'])
            self.assertEqual(package['sources']['logcat']['content'].count('Input dispatching timed out'), 1)


if __name__ == '__main__':
    unittest.main()
