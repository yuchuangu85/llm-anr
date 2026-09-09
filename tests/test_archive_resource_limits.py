from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile
from pathlib import Path

from anr_evidence import (
    ArchiveLoadError,
    InputResourceBudget,
    load_package_from_archive,
    load_package_from_path,
)
from anr_evidence.evidence_store import EvidenceStore, EvidenceStoreBudgetExceeded
from anr_evidence.loaders import core


def _write_tar_member(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    archive.addfile(info, io.BytesIO(data))


class ArchiveResourceLimitTests(unittest.TestCase):
    def _archive(self, root: Path, kind: str, members: list[tuple[str, bytes]]) -> Path:
        if kind == "zip":
            path = root / "evidence.zip"
            with zipfile.ZipFile(path, "w") as archive:
                for name, data in members:
                    archive.writestr(name, data)
            return path

        path = root / "evidence.tar"
        with tarfile.open(path, "w") as archive:
            for name, data in members:
                _write_tar_member(archive, name, data)
        return path

    def test_zip_and_tar_reject_too_many_members(self) -> None:
        for kind in ("zip", "tar"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmpdir:
                path = self._archive(Path(tmpdir), kind, [("one.txt", b"1"), ("two.txt", b"2")])
                with mock.patch.object(core, "MAX_ARCHIVE_MEMBERS", 1):
                    with self.assertRaisesRegex(ArchiveLoadError, r"member count.*limit 1"):
                        load_package_from_archive(path)

    def test_zip_and_tar_reject_oversized_member_from_metadata(self) -> None:
        for kind in ("zip", "tar"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmpdir:
                path = self._archive(Path(tmpdir), kind, [("events/events.txt", b"12345")])
                with mock.patch.object(core, "MAX_ARCHIVE_MEMBER_BYTES", 4):
                    with self.assertRaisesRegex(ArchiveLoadError, r"events/events\.txt.*5 bytes.*limit 4"):
                        load_package_from_archive(path)

    def test_compressed_zip_bomb_is_checked_by_uncompressed_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "compressed.zip"
            payload = b"x" * 8192
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("events/events.txt", payload)
                info = archive.getinfo("events/events.txt")
                self.assertLess(info.compress_size, info.file_size)
            with mock.patch.object(core, "MAX_ARCHIVE_MEMBER_BYTES", 1024):
                with self.assertRaisesRegex(ArchiveLoadError, r"8192 bytes.*limit 1024"):
                    load_package_from_archive(path)

    def test_zip_and_tar_reject_cumulative_uncompressed_size_from_metadata(self) -> None:
        members = [("events/events.txt", b"123"), ("logs/logcat.txt", b"456")]
        for kind in ("zip", "tar"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmpdir:
                path = self._archive(Path(tmpdir), kind, members)
                with mock.patch.object(core, "MAX_ARCHIVE_TOTAL_BYTES", 5):
                    with self.assertRaisesRegex(ArchiveLoadError, r"total uncompressed size.*6 bytes.*limit 5"):
                        load_package_from_archive(path)

    def test_zip_and_tar_reject_parent_or_absolute_member_names(self) -> None:
        for kind, name in (("zip", r"..\outside.txt"), ("tar", "/absolute/events.txt")):
            with self.subTest(kind=kind, name=name), tempfile.TemporaryDirectory() as tmpdir:
                path = self._archive(Path(tmpdir), kind, [(name, b"unsafe")])
                with self.assertRaisesRegex(ArchiveLoadError, "unsafe member name"):
                    load_package_from_archive(path)

    def test_tar_rejects_symbolic_and_hard_links(self) -> None:
        for link_type in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            with self.subTest(link_type=link_type), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "links.tar"
                with tarfile.open(path, "w") as archive:
                    info = tarfile.TarInfo(name="events/events.txt")
                    info.type = link_type
                    info.linkname = "target.txt"
                    archive.addfile(info)
                with self.assertRaisesRegex(ArchiveLoadError, "link member"):
                    load_package_from_archive(path)

    def test_zip_rejects_unix_symbolic_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "link.zip"
            with zipfile.ZipFile(path, "w") as archive:
                info = zipfile.ZipInfo("events/events.txt")
                info.create_system = 3
                info.external_attr = 0o120777 << 16
                archive.writestr(info, "target.txt")
            with self.assertRaisesRegex(ArchiveLoadError, "link member"):
                load_package_from_archive(path)

    def test_member_reader_stops_after_limit_plus_one_byte(self) -> None:
        class RecordingHandle:
            def __init__(self) -> None:
                self.requested: list[int] = []

            def read(self, size: int = -1) -> bytes:
                self.requested.append(size)
                return b"x" * size

        handle = RecordingHandle()
        with self.assertRaisesRegex(ArchiveLoadError, "actual data exceeds.*limit 4"):
            core._read_archive_member(handle, "events/events.txt", max_bytes=4)
        self.assertEqual(handle.requested, [5])

    def test_zip_and_tar_enforce_cumulative_budget_during_actual_reads(self) -> None:
        members = [("events/events.txt", b"123"), ("logs/logcat.txt", b"456")]
        for kind in ("zip", "tar"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmpdir:
                path = self._archive(Path(tmpdir), kind, members)
                with (
                    mock.patch.object(core, "MAX_ARCHIVE_TOTAL_BYTES", 5),
                    mock.patch.object(core, "_validate_archive_metadata_sizes", return_value=None),
                    mock.patch.object(core, "_validate_archive_total_size", return_value=None),
                ):
                    with self.assertRaisesRegex(ArchiveLoadError, r"actual data exceeds.*limit 2"):
                        load_package_from_archive(path)

    def test_zip_and_tar_streaming_enforces_actual_hard_cumulative_budget_transactionally(self):
        members = [("events/events.txt", b"123"), ("logs/logcat.txt", b"456")]
        for kind in ("zip", "tar"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmpdir:
                path = self._archive(Path(tmpdir), kind, members)
                with (
                    EvidenceStore(":memory:") as store,
                    mock.patch.object(core, "MAX_ARCHIVE_TOTAL_BYTES", 5),
                    mock.patch.object(core, "_validate_archive_metadata_sizes", return_value=None),
                    mock.patch.object(core, "_validate_archive_total_size", return_value=None),
                ):
                    with self.assertRaisesRegex(EvidenceStoreBudgetExceeded, "Shared evidence byte budget"):
                        core.stream_archive_to_store(
                            path, store, group_id="g", shared_byte_budget=8, chunk_size=2,
                        )
                    self.assertEqual(
                        store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0], 0,
                    )
                    self.assertEqual(
                        store.connection.execute("SELECT COUNT(*) FROM anr_groups").fetchone()[0], 0,
                    )

    def test_directory_does_not_swallow_policy_rejection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._archive(Path(tmpdir), "zip", [("events.txt", b"12345")])
            with mock.patch.object(core, "MAX_ARCHIVE_MEMBER_BYTES", 4):
                with self.assertRaisesRegex(ArchiveLoadError, "per-member limit"):
                    load_package_from_path(path.parent)

    def test_accepted_invalid_utf8_preserves_evidence_and_marks_unreadable(self):
        content, readable, size = core._read_archive_member(
            io.BytesIO(b"abc\xff"), "events.txt", max_bytes=4)
        self.assertEqual(content, "abc\ufffd")
        self.assertFalse(readable)
        self.assertEqual(size, 4)

    def test_tar_rejects_large_member_before_scanning_next_header(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._archive(Path(tmpdir), "tar", [("events.txt", b"x" * 8192)])
            original_next = tarfile.TarFile.next
            calls = []
            def tracked(archive):
                calls.append(True)
                return original_next(archive)
            with mock.patch.object(core, "MAX_ARCHIVE_MEMBER_BYTES", 1024), \
                    mock.patch.object(tarfile.TarFile, "next", tracked):
                with self.assertRaises(ArchiveLoadError):
                    load_package_from_archive(path)
            # Constructor fetches first header; iteration returns the cached one.
            self.assertLessEqual(len(calls), 2)

    def test_compressed_tar_extension_metadata_has_stream_budget(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "metadata.tar.gz"
            with tarfile.open(path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
                info = tarfile.TarInfo("events.txt")
                info.pax_headers = {"comment": "x" * 50000}
                archive.addfile(info, io.BytesIO())
            with mock.patch.object(core, "MAX_ARCHIVE_TOTAL_BYTES", 64), \
                    mock.patch.object(core, "MAX_ARCHIVE_MEMBERS", 1):
                with self.assertRaisesRegex(ArchiveLoadError, "stream exceeds byte budget"):
                    load_package_from_archive(path)

    def test_mixed_zip_and_tar_share_one_accepted_input_byte_budget(self) -> None:
        event = b"04-12 10:00:05 I am_anr: [0,1,com.demo,1]\n"
        logcat = b"04-12 10:00:06 W timeout data\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with zipfile.ZipFile(root / "a.zip", "w") as archive:
                archive.writestr("events/events.txt", event)
            with tarfile.open(root / "b.tar", "w") as archive:
                _write_tar_member(archive, "logs/logcat.txt", logcat)

            with self.assertRaisesRegex(ArchiveLoadError, "Input resource byte budget exceeded"):
                load_package_from_path(
                    root,
                    input_budget=InputResourceBudget(
                        max_bytes=len(event) + len(logcat) - 1,
                        max_entries=8,
                    ),
                )

    def test_mixed_archive_and_loose_file_share_one_accepted_input_byte_budget(self) -> None:
        event = b"04-12 10:00:05 I am_anr: [0,1,com.demo,1]\n"
        loose_logcat = b"04-12 10:00:06 W loose timeout\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with zipfile.ZipFile(root / "a.zip", "w") as archive:
                archive.writestr("events/events.txt", event)
            (root / "logcat.txt").write_bytes(loose_logcat)

            with self.assertRaisesRegex(ArchiveLoadError, "Loose file.*accepted-input byte limit"):
                load_package_from_path(
                    root,
                    input_budget=InputResourceBudget(
                        max_bytes=len(event) + len(loose_logcat) - 1,
                        max_entries=8,
                    ),
                )

    def test_mixed_input_budget_allows_combined_sources_at_exact_limit(self) -> None:
        event = b"04-12 10:00:05 I am_anr: [0,1,com.demo,1]\n"
        logcat = b"04-12 10:00:06 W archive timeout\n"
        kernel = b"04-12 10:00:07 kernel loose signal\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with zipfile.ZipFile(root / "a.zip", "w") as archive:
                archive.writestr("events/events.txt", event)
            with tarfile.open(root / "b.tar", "w") as archive:
                _write_tar_member(archive, "logs/logcat.txt", logcat)
            (root / "last_kmsg").write_bytes(kernel)

            package = load_package_from_path(
                root,
                input_budget=InputResourceBudget(
                    max_bytes=len(event) + len(logcat) + len(kernel),
                    max_entries=8,
                ),
            )

        self.assertIn("event_log", package["sources"])
        self.assertIn("logcat", package["sources"])
        self.assertIn("kernel_log", package["sources"])

    def test_mixed_input_budget_counts_archive_and_loose_entries_together(self) -> None:
        event = b"04-12 10:00:05 I am_anr: [0,1,com.demo,1]\n"
        logcat = b"04-12 10:00:06 W loose timeout\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with zipfile.ZipFile(root / "a.zip", "w") as archive:
                archive.writestr("events/events.txt", event)
            (root / "logcat.txt").write_bytes(logcat)

            with self.assertRaisesRegex(ArchiveLoadError, "Input resource entry budget exceeded"):
                load_package_from_path(
                    root,
                    input_budget=InputResourceBudget(max_bytes=1024, max_entries=1),
                )

    def test_accepted_compressed_tar_variants(self):
        payload = b"2026-04-12 10:00:05.000 I am_anr: [0,123,com.test,1,Input dispatching timed out]\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            for suffix, mode in (("gz", "w:gz"), ("bz2", "w:bz2"), ("xz", "w:xz")):
                with self.subTest(suffix=suffix):
                    path = Path(tmpdir) / ("events.tar." + suffix)
                    with tarfile.open(path, mode) as archive:
                        _write_tar_member(archive, "events/events.txt", payload)
                    result = load_package_from_archive(path)
                    self.assertIn(payload.decode().strip(), result["sources"]["event_log"]["content"])


if __name__ == "__main__":
    unittest.main()
