"""End-to-end C2 default routing coverage for raw directory/ZIP/TAR inputs."""

from __future__ import annotations

import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

from anr_evidence import (
    AiContextOptions,
    build_ai_context,
    build_ai_context_artifacts_from_input_path,
    load_package_from_path,
)
from anr_evidence.evidence_store import EvidenceStore


EVENTS = b"""04-12 11:00:00.000 wm_focus prior focus\n04-12 11:00:03.000 am_anr ANR in com.demo: Input dispatching timed out\n"""
LOGCAT = b"""04-12 10:59:20.000 I noise: outside the retained input-timeout window\n04-12 11:00:02.900 I InputDispatcher: retained pre-anchor\n04-12 11:00:03.050 E InputDispatcher: Input dispatching timed out for com.demo\n04-12 11:00:25.000 I noise: outside the retained input-timeout window\n"""


def _write_tar_member(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    archive.addfile(member, io.BytesIO(content))


def _write_raw_input(root: Path, kind: str) -> Path:
    if kind == "directory":
        (root / "events").mkdir(parents=True)
        (root / "logs").mkdir()
        (root / "events" / "events.txt").write_bytes(EVENTS)
        (root / "logs" / "logcat.txt").write_bytes(LOGCAT)
        # Unclassified loose files are included in store accounting but do not
        # masquerade as a filtered context source.
        (root / "metadata.bin").write_bytes(b"opaque input metadata\n")
        return root
    if kind == "zip":
        root.mkdir(parents=True, exist_ok=True)
        path = root / "bugreport.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("events/events.txt", EVENTS)
            archive.writestr("logs/logcat.txt", LOGCAT)
            archive.writestr("metadata.bin", b"opaque input metadata\n")
        return path
    if kind == "tar":
        root.mkdir(parents=True, exist_ok=True)
        path = root / "bugreport.tar"
        with tarfile.open(path, "w") as archive:
            _write_tar_member(archive, "events/events.txt", EVENTS)
            _write_tar_member(archive, "logs/logcat.txt", LOGCAT)
            _write_tar_member(archive, "metadata.bin", b"opaque input metadata\n")
        return path
    raise AssertionError(kind)


class C2DefaultRoutingTests(unittest.TestCase):
    def _assert_default_route_and_complete_baseline(self, input_path: Path, output: Path, expected_kind: str) -> dict:
        expected = build_ai_context(
            load_package_from_path(input_path, package_name="com.demo"),
            AiContextOptions(package_name="com.demo"),
        )
        actual = build_ai_context_artifacts_from_input_path(
            input_path,
            AiContextOptions(out_dir=output, package_name="com.demo"),
        )
        persisted = json.loads((output / "index.json").read_text(encoding="utf-8"))

        self.assertEqual(actual["inputRouting"], persisted["inputRouting"])
        routing = persisted["inputRouting"]
        self.assertEqual(routing["mode"], "store_backed_default")
        self.assertEqual(routing["entrypoint"], "build_ai_context_artifacts_from_input_path")
        self.assertEqual(routing["inputKind"], expected_kind)
        self.assertFalse(routing["legacyPackageMaterialized"])
        self.assertEqual(routing["inferredAnrType"], "input_dispatching_timeout")
        self.assertGreaterEqual(routing["capturedBytes"], len(EVENTS) + len(LOGCAT))
        self.assertGreaterEqual(routing["capturedEntries"], 2)

        self.assertTrue(Path(actual["artifactPaths"]["evidenceStore"]).is_file())
        self.assertEqual(actual["groupCount"], expected.summary()["groupCount"])
        for actual_group, expected_group in zip(actual["groups"], expected.groups, strict=True):
            baseline = (output / actual_group["id"] / "logcat.txt").read_text(encoding="utf-8").splitlines()
            # The normal logcat artifact remains the complete filtered baseline;
            # it is not the separate hard-capped compact LLM view.
            self.assertEqual(baseline, expected_group["logcat"]["lines"])
            self.assertTrue((output / actual_group["id"] / "compact_evidence.jsonl").is_file())
        return actual

    def test_raw_directory_zip_and_tar_use_the_same_default_store_backed_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            directory = _write_raw_input(root / "directory", "directory")
            archive_zip = _write_raw_input(root / "zip", "zip")
            archive_tar = _write_raw_input(root / "tar", "tar")

            self._assert_default_route_and_complete_baseline(directory, directory / "anr_ai_context", "directory")
            self._assert_default_route_and_complete_baseline(archive_zip, root / "zip-context", "archive")
            self._assert_default_route_and_complete_baseline(archive_tar, root / "tar-context", "archive")

    def test_directory_regeneration_replaces_capture_and_excludes_prior_output_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "bugreport"
            input_path = _write_raw_input(root, "directory")
            output = root / "anr_ai_context"
            first = build_ai_context_artifacts_from_input_path(
                input_path, AiContextOptions(out_dir=output, package_name="com.demo"),
            )
            store_path = Path(first["artifactPaths"]["evidenceStore"])
            first_size = store_path.stat().st_size

            (root / "logs" / "logcat.txt").write_bytes(
                b"04-12 11:00:02.900 I InputDispatcher: retained replacement\n"
                b"04-12 11:00:03.050 E InputDispatcher: Input dispatching timed out for com.demo\n"
            )
            second = build_ai_context_artifacts_from_input_path(
                input_path, AiContextOptions(out_dir=output, package_name="com.demo"),
            )
            self.assertEqual(Path(second["artifactPaths"]["evidenceStore"]), store_path)
            rendered = (output / second["groups"][0]["id"] / "logcat.txt").read_text(encoding="utf-8")
            self.assertIn("retained replacement", rendered)
            self.assertNotIn("retained pre-anchor", rendered)
            self.assertLess(store_path.stat().st_size, first_size * 2)
            with EvidenceStore.open_read_only(store_path) as store:
                artifacts = store.list_group_artifacts("input-capture", max_artifacts=10_000)
            self.assertEqual(len(artifacts), 3)


if __name__ == "__main__":
    unittest.main()
