from __future__ import annotations

import io
from pathlib import Path
import tempfile
import unittest

from anr_evidence.ai_context import AiContextOptions, build_ai_context, build_ai_context_artifacts_from_store, build_ai_context_from_store
from anr_evidence.evidence_store import EvidenceStore


EVENTS = (
    b"06-09 13:12:40.000 wm_focus before\n"
    b"06-09 13:12:45.321 am_anr ANR in com.demo\n"
    b"06-09 13:13:00.000 am_anr ANR in com.demo\n"
)
LOGCAT = (
    b"06-09 13:12:44.900 I InputDispatcher: pre first\n"
    b"06-09 13:12:45.321 E InputDispatcher: Application does not have a focused window\n"
    b"06-09 13:12:59.900 I InputDispatcher: pre second\n"
    b"06-09 13:13:00.000 E InputDispatcher: Application does not have a focused window\n"
)


class StoreBackedAiContextTests(unittest.TestCase):
    def test_store_backed_multi_group_context_matches_package_group_anchors_and_logcat(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "evidence.sqlite3"
            with EvidenceStore(path) as store:
                store.add_streamed_group(
                    "capture",
                    [
                        {"reader": io.BytesIO(EVENTS), "source_kind": "event_log", "logical_path": "events.txt", "origin": {"type": "file", "path": "/input/events.txt"}},
                        {"reader": io.BytesIO(LOGCAT), "source_kind": "logcat", "logical_path": "logcat.txt", "origin": {"type": "file", "path": "/input/logcat.txt"}},
                    ],
                    shared_byte_budget=len(EVENTS) + len(LOGCAT), chunk_size=17,
                )
            options = AiContextOptions(package_name="com.demo", anr_type="input_dispatching_timeout")
            expected = build_ai_context({"package_id": "demo", "sources": {
                "event_log": {"path": "events.txt", "content": EVENTS.decode()},
                "logcat": {"path": "logcat.txt", "content": LOGCAT.decode()},
            }}, options)
            actual = build_ai_context_from_store(path, source_group_id="capture", package_id="demo", options=options, max_stream_source_bytes=len(LOGCAT), max_stream_source_lines=100)

            self.assertEqual([group["id"] for group in actual.groups], [group["id"] for group in expected.groups])
            self.assertEqual([group["anchor"]["line"] for group in actual.groups], [group["anchor"]["line"] for group in expected.groups])
            self.assertEqual([group["logcat"]["lines"] for group in actual.groups], [group["logcat"]["lines"] for group in expected.groups])

    def test_store_backed_artifact_export_writes_each_group_without_legacy_package_or_ess(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = root / "evidence.sqlite3"
            with EvidenceStore(path) as store:
                store.add_streamed_group("capture", [
                    {"reader": io.BytesIO(EVENTS), "source_kind": "event_log", "logical_path": "events.txt", "origin": {"type": "file", "path": "/input/events.txt"}},
                    {"reader": io.BytesIO(LOGCAT), "source_kind": "logcat", "logical_path": "logcat.txt", "origin": {"type": "file", "path": "/input/logcat.txt"}},
                ], shared_byte_budget=len(EVENTS) + len(LOGCAT), chunk_size=17)
            output = root / "context"
            index = build_ai_context_artifacts_from_store(
                path, source_group_id="capture", package_id="demo",
                options=AiContextOptions(out_dir=output, package_name="com.demo", anr_type="input_dispatching_timeout"),
            )
            self.assertEqual(index["groupCount"], 2)
            self.assertEqual(index["artifactPaths"]["evidenceStore"], str(path))
            for group in index["groups"]:
                self.assertTrue((output / group["id"] / "anr_analysis.md").is_file())
                self.assertTrue((output / group["id"] / "logcat.txt").is_file())
            with self.assertRaisesRegex(ValueError, "ESS"):
                build_ai_context_artifacts_from_store(
                    path, source_group_id="capture", package_id="demo",
                    options=AiContextOptions(out_dir=output, anr_type="input_dispatching_timeout", build_ess=True),
                )

    def test_store_backed_context_requires_existing_source_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "evidence.sqlite3"
            with EvidenceStore(path) as store:
                store.add_group("other")
            result = build_ai_context_from_store(path, source_group_id="missing", package_id="demo", options=AiContextOptions(anr_type="unknown"))
            self.assertEqual(result.groups[0]["eventLog"]["warnings"][0]["code"], "missing-anchor")


if __name__ == "__main__":
    unittest.main()
