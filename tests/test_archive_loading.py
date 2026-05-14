from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from anr_evidence import ArchiveLoadError, extract_evidence_package, load_package_from_archive

ROOT = Path(__file__).resolve().parent.parent
ENV = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}


def _write_tar_member(archive: tarfile.TarFile, name: str, content: str) -> None:
    data = content.encode('utf-8')
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


class ArchiveLoadingTests(unittest.TestCase):
    def test_zip_archive_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / 'bugreport.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('FS/data/anr/traces.txt', '04-12 10:00:05.100 main tid=1 waiting because no focused window\n')
                archive.writestr('logs/logcat_main.txt', '04-12 10:00:05.050 E InputDispatcher no focused window\n')
                archive.writestr('events/events.txt', '04-12 10:00:05.000 am_anr ANR in com.demo: No focused window\n')
                archive.writestr('kernel/last_kmsg', '04-12 10:00:05.070 binder: backlog\n')
            package = load_package_from_archive(archive_path)
            self.assertIn('trace', package['sources'])
            self.assertIn('logcat', package['sources'])
            self.assertIn('event_log', package['sources'])
            self.assertIn('kernel_log', package['sources'])
            extracted = extract_evidence_package(package)
            self.assertEqual(extracted['classification']['detectedType'], 'no_focus_window')

    def test_tar_gz_archive_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / 'bugreport.tar.gz'
            with tarfile.open(archive_path, 'w:gz') as archive:
                _write_tar_member(archive, 'data/anr/traces.txt', '04-12 11:00:03.100 main tid=1 input dispatching timeout\n')
                _write_tar_member(archive, 'logs/logcat.txt', '04-12 11:00:03.050 E InputDispatcher Input dispatching timed out\n')
                _write_tar_member(archive, 'eventlog/events.txt', '04-12 11:00:03.000 am_anr ANR in com.demo: Input dispatching timed out\n')
                _write_tar_member(archive, 'kernel/console-ramoops', '04-12 11:00:03.070 sched: pressure\n')
            package = load_package_from_archive(archive_path)
            extracted = extract_evidence_package(package)
            self.assertEqual(extracted['classification']['detectedType'], 'input_dispatching_timeout')

    def test_cli_deliver_accepts_archive_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / 'bugreport.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('data/anr/traces.txt', '04-12 11:00:03.100 main tid=1 input dispatching timeout\n')
                archive.writestr('logs/logcat.txt', '04-12 11:00:03.050 E InputDispatcher Input dispatching timed out\n')
                archive.writestr('events/events.txt', '04-12 11:00:03.000 am_anr ANR in com.demo: Input dispatching timed out\n')
                archive.writestr('kernel/console-ramoops', '04-12 11:00:03.070 sched: pressure\n')
            completed = subprocess.run(
                [sys.executable, '-m', 'anr_evidence', '--deliver', str(archive_path)],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                env=ENV,
            )
            self.assertIn('# ANR 分析交付稿', completed.stdout)
            self.assertIn('输入分发', completed.stdout)

    def test_vendor_like_names_in_archive_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / 'vendor-bugreport.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('dropbox/system_server_anr@456.txt', '04-12 15:00:05.100 Cmd line: system_server\\nmain tid=1 input dispatching timeout\\n')
                archive.writestr('android-logs/log-main', '04-12 15:00:05.050 E InputDispatcher Input dispatching timed out\\n')
                archive.writestr('events/events_log.txt', '04-12 15:00:05.000 am_anr ANR in com.demo: Input dispatching timed out\\n')
                archive.writestr('panic/lastkmsg', '04-12 15:00:05.070 sched: pressure\\n')
            package = load_package_from_archive(archive_path)
            self.assertIn('trace', package['sources'])
            self.assertIn('logcat', package['sources'])
            self.assertIn('event_log', package['sources'])
            self.assertIn('kernel_log', package['sources'])

    def test_archive_shard_priority_and_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / 'priority.zip'
            duplicate_log = '04-12 17:00:05.050 E InputDispatcher Input dispatching timed out\\n'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('logs/system.txt', duplicate_log)
                archive.writestr('logs/main.txt', duplicate_log)
                archive.writestr('eventlog/events.txt', '04-12 17:00:05.000 am_anr ANR in com.demo: Input dispatching timed out\\n')
                archive.writestr('data/anr/traces.txt', '04-12 17:00:05.100 main tid=1 input dispatching timeout\\n')
                archive.writestr('kernel/last_kmsg', '04-12 17:00:05.070 sched: pressure\\n')
            package = load_package_from_archive(archive_path)
            self.assertEqual(package['sources']['logcat']['path'].split(','), ['logs/main.txt'])
            self.assertEqual(package['sources']['logcat']['content'].count('Input dispatching timed out'), 1)

    def test_corrupted_zip_raises_friendly_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / 'broken.zip'
            archive_path.write_bytes(b'not-a-real-zip')
            with self.assertRaises(ArchiveLoadError) as ctx:
                load_package_from_archive(archive_path)
            self.assertIn('not a readable zip file', str(ctx.exception))

    def test_archive_with_no_recognizable_sources_raises_friendly_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / 'empty-sources.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                archive.writestr('notes/readme.txt', 'hello world\\n')
            with self.assertRaises(ArchiveLoadError) as ctx:
                load_package_from_archive(archive_path)
            self.assertIn('no recognizable ANR log sources were found', str(ctx.exception))

    def test_cli_reports_archive_error_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            archive_path = Path(tmpdir) / 'broken.zip'
            archive_path.write_bytes(b'broken-data')
            completed = subprocess.run(
                [sys.executable, '-m', 'anr_evidence', '--deliver', str(archive_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                env=ENV,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn('not a readable zip file', completed.stderr or completed.stdout)


if __name__ == '__main__':
    unittest.main()
