from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest

from anr_evidence.ai_context import AiContextOptions, build_ai_context, build_ai_context_artifacts_from_store, build_ai_context_from_store
from anr_evidence.evidence_slice import read_ess_jsonl
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

    def test_store_backed_artifact_export_writes_coordinate_sidecars_ess_and_hard_bounded_compact_view(self) -> None:
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
                options=AiContextOptions(
                    out_dir=output, package_name="com.demo", anr_type="input_dispatching_timeout",
                    build_ess=True, compact_llm_view_max_bytes=512,
                ),
            )
            self.assertEqual(index["groupCount"], 2)
            self.assertEqual(index["artifactPaths"]["evidenceStore"], str(path))
            refs_by_group = {}
            for group in index["groups"]:
                group_dir = output / group["id"]
                self.assertTrue((group_dir / "anr_analysis.md").is_file())
                self.assertTrue((group_dir / "logcat.txt").is_file())
                self.assertTrue((group_dir / "evidence_refs.json").is_file())
                self.assertTrue((group_dir / "evidence.jsonl").is_file())
                self.assertTrue((group_dir / "compact_evidence.jsonl").is_file())
                compact = (group_dir / "compact_evidence.jsonl").read_bytes()
                compact_metadata = json.loads((group_dir / "compact_evidence.meta.json").read_text())
                self.assertLessEqual(len(compact), 512)
                self.assertEqual(compact_metadata["retainedBytes"], len(compact))
                self.assertEqual((group_dir / "logcat.txt").read_text(), "\n".join(
                    build_ai_context_from_store(
                        path, source_group_id="capture", package_id="demo",
                        options=AiContextOptions(package_name="com.demo", anr_type="input_dispatching_timeout"),
                    ).groups[[item["id"] for item in index["groups"]].index(group["id"])]["logcat"]["lines"]
                ) + "\n")
                sidecar = json.loads((group_dir / "evidence_refs.json").read_text())
                refs_by_group[group["id"]] = sidecar["references"]
                resolved = [row for row in sidecar["references"] if row["status"] == "resolved"]
                self.assertTrue(resolved)
                self.assertTrue(any(row["section"] == "eventLog" for row in resolved))
                self.assertTrue(any(row["section"] == "logcat" for row in resolved))
                self.assertTrue(all(row["sourceStoreGroupId"] == "capture" for row in resolved))
                self.assertTrue(all(row["endByte"] > row["startByte"] for row in resolved))
                with EvidenceStore(path) as store:
                    rebound = read_ess_jsonl(
                        group_dir / "evidence.jsonl", store=store,
                        references={group["id"]: sidecar["references"]},
                    )
                self.assertTrue(any(item.evidence_ref for item in rebound))

            first_id, second_id = [group["id"] for group in index["groups"]]
            with EvidenceStore(path) as store:
                cross_group = read_ess_jsonl(
                    output / second_id / "evidence.jsonl", store=store,
                    references={second_id: refs_by_group[first_id]},
                )
            self.assertTrue(cross_group)
            self.assertTrue(all(item.evidence_ref is None for item in cross_group))
            self.assertTrue(any(item.evidence_ref_error == "reference_integrity_check_failed" for item in cross_group))

    def test_store_backed_context_requires_existing_source_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "evidence.sqlite3"
            with EvidenceStore(path) as store:
                store.add_group("other")
            result = build_ai_context_from_store(path, source_group_id="missing", package_id="demo", options=AiContextOptions(anr_type="unknown"))
            self.assertEqual(result.groups[0]["eventLog"]["warnings"][0]["code"], "missing-anchor")


if __name__ == "__main__":
    unittest.main()

# Keep the negative cases at module level so their data is visibly distinct from
# the normal multi-group capture above.
class StoreBackedCoordinateNegativeTests(unittest.TestCase):
    def _store(self, root: Path, events: bytes, logcat: bytes) -> Path:
        path = root / "evidence.sqlite3"
        with EvidenceStore(path) as store:
            store.add_streamed_group("capture", [
                {"reader": io.BytesIO(events), "source_kind": "event_log", "logical_path": "events.txt", "origin": {"type": "file", "path": "/input/events.txt"}},
                {"reader": io.BytesIO(logcat), "source_kind": "logcat", "logical_path": "logcat.txt", "origin": {"type": "file", "path": "/input/logcat.txt"}},
            ], shared_byte_budget=len(events) + len(logcat), chunk_size=13)
        return path

    def test_trimmed_logcat_output_is_unresolved_instead_of_text_rebound(self) -> None:
        events = b"06-09 13:12:45.321 am_anr ANR in com.demo\n"
        logcat = b"06-09 13:12:45.321 E InputDispatcher: Application does not have a focused window   \n"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = self._store(root, events, logcat)
            output = root / "context"
            index = build_ai_context_artifacts_from_store(
                path, source_group_id="capture", package_id="demo",
                options=AiContextOptions(
                    out_dir=output, package_name="com.demo", anr_type="input_dispatching_timeout",
                ),
            )
            group_id = index["groups"][0]["id"]
            refs = json.loads((output / group_id / "evidence_refs.json").read_text())["references"]
            transformed = [row for row in refs if row["section"] == "logcat"]
            self.assertEqual(len(transformed), 1)
            self.assertEqual(transformed[0]["status"], "unresolved")
            self.assertEqual(transformed[0]["reason"], "source_coordinate_unavailable")

    def test_crlf_store_coordinate_keeps_original_byte_span(self) -> None:
        events = b"06-09 13:12:45.321 am_anr ANR in com.demo\r\n"
        logcat = b"06-09 13:12:45.321 E InputDispatcher: Application does not have a focused window\r\n"
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            path = self._store(root, events, logcat)
            output = root / "context"
            index = build_ai_context_artifacts_from_store(
                path, source_group_id="capture", package_id="demo",
                options=AiContextOptions(
                    out_dir=output, package_name="com.demo", anr_type="input_dispatching_timeout",
                    build_ess=True,
                ),
            )
            group_id = index["groups"][0]["id"]
            refs = json.loads((output / group_id / "evidence_refs.json").read_text())["references"]
            event_ref = next(row for row in refs if row["section"] == "eventLog" and row["status"] == "resolved")
            self.assertEqual(event_ref["startByte"], 0)
            self.assertEqual(event_ref["endByte"], len(events))
            with EvidenceStore(path) as store:
                value = store.read_span(
                    event_ref["spanId"], group_id=group_id, source_kind="event_log",
                    max_bytes=EvidenceStore.DEFAULT_MAX_REFERENCE_READ_BYTES,
                    expected_artifact_commitment=event_ref["artifactCommitment"],
                )
            self.assertEqual(value["content"], events)
