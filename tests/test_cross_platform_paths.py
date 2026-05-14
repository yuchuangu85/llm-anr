from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from anr_evidence.loaders import build_package_from_entries
from scripts.extract_bugreport import extract_bugreport
from scripts.web_server import _safe_upload_filename


class CrossPlatformPathTests(unittest.TestCase):
    def test_windows_style_entry_paths_are_detected_and_ranked_on_any_host(self) -> None:
        entries = [
            {
                "path": r"System_log\anr\anr_2026-04-12-10-00-05-100",
                "content": 'Cmd line: com.demo\n"main" prio=5 tid=1 Native\n',
                "readable": True,
            },
            {
                "path": r"System_log\System_MT_logcat_04_12_09_59_00.txt",
                "content": "04-12 09:59:59.000 E InputDispatcher before anchor\n",
                "readable": True,
            },
            {
                "path": r"System_log\System_MT_logcat_04_12_10_01_00.txt",
                "content": "04-12 10:01:00.000 E InputDispatcher after anchor\n",
                "readable": True,
            },
            {
                "path": r"System_log\System_MT_logcat_event_04_12_09_59_00.txt",
                "content": "04-12 09:59:59.000 I am_proc_start: [0,100,com.demo]\n",
                "readable": True,
            },
            {
                "path": r"System_log\System_MT_logcat_event_04_12_10_01_00.txt",
                "content": "04-12 10:01:00.000 I am_proc_died: [0,100,com.demo]\n",
                "readable": True,
            },
        ]

        package = build_package_from_entries("windows-style", entries)

        self.assertIn("trace", package["sources"])
        self.assertIn("logcat", package["sources"])
        self.assertIn("event_log", package["sources"])
        self.assertIn("09_59_00", package["sources"]["logcat"]["path"])
        self.assertNotIn("10_01_00", package["sources"]["logcat"]["path"])
        self.assertIn("09_59_00", package["sources"]["event_log"]["path"])
        self.assertNotIn("10_01_00", package["sources"]["event_log"]["path"])

    def test_extract_zip_normalizes_windows_member_separators(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archive_path = root / "bugreport.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(r"System_log\events.txt", "04-12 10:00:05.000 am_anr\n")

            out_dir = extract_bugreport(archive_path, root / "out")

            self.assertTrue((out_dir / "System_log" / "events.txt").is_file())

    def test_extract_tar_normalizes_windows_member_separators(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archive_path = root / "bugreport.tar"
            data = b"04-12 10:00:05.000 am_anr\n"
            with tarfile.open(archive_path, "w") as archive:
                info = tarfile.TarInfo(name=r"System_log\events.txt")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

            out_dir = extract_bugreport(archive_path, root / "out")

            self.assertTrue((out_dir / "System_log" / "events.txt").is_file())

    def test_extract_zip_rejects_traversal_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            archive_path = root / "bugreport.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(r"..\outside.txt", "escape\n")

            with self.assertRaises(ValueError):
                extract_bugreport(archive_path, root / "out")
            self.assertFalse((root / "outside.txt").exists())

    def test_upload_filename_strips_windows_fakepath_on_posix_hosts(self) -> None:
        self.assertEqual(_safe_upload_filename(r"C:\fakepath\bugreport.zip"), "bugreport.zip")


if __name__ == "__main__":
    unittest.main()
