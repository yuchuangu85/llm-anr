import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from anr_evidence.evidence_store import (
    EvidenceStore,
    EvidenceStoreBudgetExceeded,
    EvidenceStoreError,
)
from anr_evidence.loaders.core import (
    DirectoryEventLogConsumer,
    load_package_from_path,
    stream_directory_event_logs_to_store,
)


class _GuardedBinaryReader:
    def __init__(self, raw, requests: list[int], maximum: int):
        self._raw = raw
        self._requests = requests
        self._maximum = maximum

    def __enter__(self):
        self._raw.__enter__()
        return self

    def __exit__(self, *args):
        return self._raw.__exit__(*args)

    def read(self, size=-1):
        if size <= 0 or size > self._maximum:
            raise AssertionError(f"unbounded read requested: {size}")
        self._requests.append(size)
        return self._raw.read(size)


class _BlobReadRecorder:
    def __init__(self):
        self.byte_counts: list[int] = []

    def row_factory(self, cursor, row):
        self.byte_counts.extend(len(value) for value in row if isinstance(value, bytes))
        return tuple(row)

    @property
    def total_bytes(self) -> int:
        return sum(self.byte_counts)


def _sqlite_steps(connection: sqlite3.Connection, operation) -> int:
    steps = 0

    def progress() -> int:
        nonlocal steps
        steps += 1
        return 0

    connection.set_progress_handler(progress, 1)
    try:
        operation()
    finally:
        connection.set_progress_handler(None, 0)
    return steps


class _FailingReader:
    def __init__(self, first: bytes):
        self._first = first
        self._reads = 0

    def read(self, size=-1):
        self._reads += 1
        if self._reads == 1:
            return self._first[:size]
        raise OSError("injected later ingestion failure")


def _legacy_artifact_id(source_kind: str, logical_path: str, origin: dict[str, str], content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    identity_input = json.dumps(
        [source_kind, logical_path, origin, digest],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "artifact-" + hashlib.sha256(identity_input).hexdigest()


def _span_id(group_id: str, artifact_id: str, start_byte: int, end_byte: int) -> str:
    identity_input = json.dumps(
        [group_id, artifact_id, start_byte, end_byte],
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    return "span-" + hashlib.sha256(identity_input).hexdigest()


_REAL_SQLITE_CONNECT = sqlite3.connect


class _FetchOneOnlyCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        raise AssertionError("migration artifact IDs must not be materialized")

    def __iter__(self):
        raise AssertionError("migration artifact IDs must use incremental fetchone traversal")


class _IncrementalMigrationConnection(sqlite3.Connection):
    def execute(self, sql, parameters=(), /):
        cursor = super().execute(sql, parameters)
        if (
            sql.startswith("SELECT id FROM artifacts WHERE id GLOB 'artifact-*'")
            and "ORDER BY id" in sql
        ):
            return _FetchOneOnlyCursor(cursor)
        return cursor


def _seed_legacy_database(path: Path, contents: list[bytes]) -> list[str]:
    connection = sqlite3.connect(path)
    for statement in EvidenceStore._BASE_SCHEMA:
        connection.execute(statement)
    artifact_ids = []
    for index, content in enumerate(contents):
        logical_path = f"events-{index}.txt"
        origin = {"type": "file", "path": f"/legacy/{logical_path}"}
        artifact_id = _legacy_artifact_id("event_log", logical_path, origin, content)
        connection.execute(
            "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
            (artifact_id, "event_log", logical_path, json.dumps(origin, sort_keys=True),
             hashlib.sha256(content).hexdigest(), len(content), content),
        )
        artifact_ids.append(artifact_id)
    connection.commit()
    connection.close()
    return artifact_ids


class StreamingBudgetTests(unittest.TestCase):
    def test_package_import_without_fcntl_fails_only_streaming_call_closed(self):
        code = "\n".join((
            "import importlib.abc",
            "import pathlib",
            "import sys",
            "import tempfile",
            "class BlockFcntl(importlib.abc.MetaPathFinder):",
            "    def find_spec(self, fullname, path=None, target=None):",
            "        if fullname == 'fcntl':",
            "            raise ModuleNotFoundError('blocked fcntl')",
            "        return None",
            "sys.modules.pop('fcntl', None)",
            "sys.meta_path.insert(0, BlockFcntl())",
            "import anr_evidence",
            "from anr_evidence.evidence_store import EvidenceStoreError",
            "from anr_evidence.loaders.core import stream_directory_event_logs_to_store",
            "with tempfile.TemporaryDirectory() as tmpdir:",
            "    base = pathlib.Path(tmpdir)",
            "    root = base / 'input'",
            "    root.mkdir()",
            "    (root / 'events.txt').write_bytes(b'event\\n')",
            "    try:",
            "        stream_directory_event_logs_to_store(root, base / 'evidence.sqlite3', group_id='g', shared_byte_budget=6)",
            "    except EvidenceStoreError as exc:",
            "        assert 'primitives are unavailable' in str(exc), exc",
            "    else:",
            "        raise AssertionError('streaming call did not fail closed')",
        ))
        completed = subprocess.run(
            [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

    def test_directory_event_logs_stream_to_sqlite_with_exact_byte_lines_and_spans(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            first = b"one\r\nbad:\xff\n"
            second = b"two\nlast"
            (root / "events.txt").write_bytes(first)
            (root / "System_MT_logcat_event_04_12_10_00_00.txt").write_bytes(second)
            (root / "traces.txt").write_bytes(b"not selected")
            store_path = base / "evidence.sqlite3"

            legacy_before = load_package_from_path(root)
            original_os_open = os.open
            original_fdopen = os.fdopen
            requests: list[int] = []
            opened: list[str] = []

            def guarded_os_open(path, flags, *args, **kwargs):
                candidate = Path(path)
                descriptor = original_os_open(path, flags, *args, **kwargs)
                if kwargs.get("dir_fd") is not None and candidate.name in {
                    "events.txt", "System_MT_logcat_event_04_12_10_00_00.txt",
                }:
                    opened.append(candidate.name)
                return descriptor

            def guarded_fdopen(descriptor, *args, **kwargs):
                return _GuardedBinaryReader(
                    original_fdopen(descriptor, *args, **kwargs), requests, 4
                )

            with (
                patch.object(Path, "read_bytes", side_effect=AssertionError("full byte read forbidden")),
                patch.object(Path, "read_text", side_effect=AssertionError("full text read forbidden")),
                patch("anr_evidence.loaders.core.os.open", guarded_os_open),
                patch("anr_evidence.loaders.core.os.fdopen", guarded_fdopen),
            ):
                consumer = stream_directory_event_logs_to_store(
                    root,
                    store_path,
                    group_id="group-a",
                    shared_byte_budget=len(first) + len(second),
                    chunk_size=4,
                    max_read_bytes=16,
                )

            self.assertTrue(requests)
            self.assertTrue(all(0 < size <= 4 for size in requests))
            self.assertEqual(set(opened), {
                "events.txt", "System_MT_logcat_event_04_12_10_00_00.txt",
            })
            self.assertEqual([item["path"] for item in consumer.artifacts], [
                "System_MT_logcat_event_04_12_10_00_00.txt",
                "events.txt",
            ])
            artifacts = {item["path"]: item for item in consumer.artifacts}
            first_artifact = artifacts["events.txt"]
            self.assertEqual(first_artifact["sha256"], hashlib.sha256(first).hexdigest())
            self.assertEqual(first_artifact["byteCount"], len(first))
            with EvidenceStore(store_path) as store:
                inline = store.connection.execute(
                    "SELECT content FROM artifacts WHERE id=?",
                    (first_artifact["artifactId"],),
                ).fetchone()[0]
                chunks = store.connection.execute(
                    "SELECT content FROM artifact_chunks WHERE artifact_id=? ORDER BY chunk_index",
                    (first_artifact["artifactId"],),
                ).fetchall()
            self.assertEqual(inline, b"")
            self.assertEqual(b"".join(row[0] for row in chunks), first)

            lines = consumer.read_lines(first_artifact["artifactId"], 0, 2)
            self.assertEqual([line["content"] for line in lines], [b"one\r\n", b"bad:\xff\n"])
            self.assertTrue(all(line["sha256"] == first_artifact["sha256"] for line in lines))
            self.assertEqual(
                [(line["startByte"], line["endByte"]) for line in lines],
                [(0, 5), (5, len(first))],
            )
            span = consumer.read_span(first_artifact["artifactId"], 3, 9)
            self.assertEqual(span["content"], first[3:9])
            self.assertEqual(span["sha256"], hashlib.sha256(first).hexdigest())

            with EvidenceStore(store_path) as store:
                store.add_group("group-b")
                with self.assertRaisesRegex(EvidenceStoreError, "requested group"):
                    store.read_artifact_span(
                        group_id="group-b", artifact_id=first_artifact["artifactId"],
                        source_kind="event_log", start_byte=0, end_byte=1, max_bytes=16,

                        expected_artifact_commitment=first_artifact["artifactCommitment"],)
                with self.assertRaisesRegex(EvidenceStoreError, "outside"):
                    store.read_artifact_span(
                        group_id="group-a", artifact_id=first_artifact["artifactId"],
                        source_kind="event_log", start_byte=0, end_byte=len(first) + 1,
                        max_bytes=16,

                        expected_artifact_commitment=first_artifact["artifactCommitment"],)
                with self.assertRaisesRegex(EvidenceStoreError, "bounded"):
                    store.read_artifact_span(
                        group_id="group-a", artifact_id=first_artifact["artifactId"],
                        source_kind="event_log", start_byte=0, end_byte=len(first), max_bytes=2,

                        expected_artifact_commitment=first_artifact["artifactCommitment"],)

            legacy_after = load_package_from_path(root)
            self.assertEqual(legacy_after, legacy_before)

    def test_directory_stream_rejects_root_replacement_before_publish(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"event\n")
            moved = base / "original-input"
            store_path = base / "evidence.sqlite3"
            original_add = EvidenceStore.add_streamed_group

            def replace_root(store, *args, **kwargs):
                result = original_add(store, *args, **kwargs)
                root.rename(moved)
                root.mkdir()
                (root / "events.txt").write_bytes(b"replacement\n")
                return result

            with patch.object(EvidenceStore, "add_streamed_group", new=replace_root):
                with self.assertRaisesRegex(EvidenceStoreError, "root changed"):
                    stream_directory_event_logs_to_store(
                        root, store_path, group_id="group-a",
                        shared_byte_budget=6, chunk_size=3,
                    )

            self.assertFalse(store_path.exists())

    def test_directory_stream_rejects_store_inside_input_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"event\n")
            with self.assertRaisesRegex(EvidenceStoreError, "outside the input root"):
                stream_directory_event_logs_to_store(
                    root, root / "evidence.sqlite3", group_id="group-a",
                    shared_byte_budget=6, chunk_size=3,
                )

    def test_directory_stream_rejects_actual_parent_switched_inside_input_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"event\n")
            (root / "output").mkdir()
            outside = base / "outside"
            outside.mkdir()
            (outside / "output").mkdir()
            alias = base / "alias"
            try:
                alias.symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            store_path = alias / "output" / "evidence.sqlite3"
            real_open = os.open
            switched = False

            def switch_before_parent_open(path, flags, *args, **kwargs):
                nonlocal switched
                if Path(path) == store_path.parent and kwargs.get("dir_fd") is None and not switched:
                    switched = True
                    alias.unlink()
                    alias.symlink_to(root, target_is_directory=True)
                return real_open(path, flags, *args, **kwargs)

            with patch("anr_evidence.loaders.core.os.open", side_effect=switch_before_parent_open):
                with self.assertRaisesRegex(EvidenceStoreError, "outside the input root"):
                    stream_directory_event_logs_to_store(
                        root, store_path, group_id="group-a",
                        shared_byte_budget=6, chunk_size=3,
                    )

            self.assertTrue(switched)
            self.assertFalse((root / "output" / store_path.name).exists())

    def test_directory_stream_rejects_untrusted_writable_output_parent_before_stream(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"event\n")
            output = base / "output"
            output.mkdir()
            output.chmod(0o777)
            store_path = output / "evidence.sqlite3"

            with patch.object(
                EvidenceStore, "add_streamed_group",
                side_effect=AssertionError("streaming must not start"),
            ):
                with self.assertRaisesRegex(
                    EvidenceStoreError, "owned by the effective UID.*not group/other writable",
                ):
                    stream_directory_event_logs_to_store(
                        root, store_path, group_id="group-a",
                        shared_byte_budget=6, chunk_size=3,
                    )

            self.assertFalse(store_path.exists())
            self.assertFalse((output / f".{store_path.name}.lock").exists())

    @unittest.skipUnless(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        "changing predecessor ownership requires root privileges",
    )
    def test_directory_stream_rejects_foreign_owned_predecessor_before_ingest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"new-event\n")
            store_path = base / "evidence.sqlite3"
            with EvidenceStore(store_path) as store:
                store.add_group("existing")
            before = store_path.read_bytes()
            foreign_uid = 1 if os.geteuid() == 0 else 0
            os.chown(store_path, foreign_uid, -1)

            with patch.object(
                EvidenceStore, "add_streamed_group",
                side_effect=AssertionError("streaming must not start"),
            ):
                with self.assertRaisesRegex(
                    EvidenceStoreError,
                    "Published evidence store must be owned by the effective UID",
                ):
                    stream_directory_event_logs_to_store(
                        root, store_path, group_id="group-new",
                        shared_byte_budget=10, chunk_size=4,
                    )

            self.assertEqual(store_path.read_bytes(), before)
            self.assertFalse(any(".stage-" in path.name for path in base.iterdir()))

    def test_directory_stream_rejects_group_or_world_writable_predecessor_before_ingest(self):
        for mode in (0o620, 0o602):
            with self.subTest(mode=oct(mode)), tempfile.TemporaryDirectory() as tmpdir:
                base = Path(tmpdir)
                root = base / "input"
                root.mkdir()
                (root / "events.txt").write_bytes(b"new-event\n")
                store_path = base / "evidence.sqlite3"
                with EvidenceStore(store_path) as store:
                    store.add_group("existing")
                before = store_path.read_bytes()
                store_path.chmod(mode)

                with patch.object(
                    EvidenceStore, "add_streamed_group",
                    side_effect=AssertionError("streaming must not start"),
                ):
                    with self.assertRaisesRegex(
                        EvidenceStoreError,
                        "Published evidence store must be owned by the effective UID "
                        "and not group/other writable",
                    ):
                        stream_directory_event_logs_to_store(
                            root, store_path, group_id="group-new",
                            shared_byte_budget=10, chunk_size=4,
                        )

                self.assertEqual(store_path.read_bytes(), before)
                self.assertFalse(any(".stage-" in path.name for path in base.iterdir()))

    def test_directory_stream_rejects_untrusted_existing_lock_before_flock_or_ingest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"event\n")
            store_path = base / "evidence.sqlite3"
            lock_path = base / f".{store_path.name}.lock"
            lock_path.write_bytes(b"malicious-lock")
            lock_path.chmod(0o666)

            with (
                patch("anr_evidence.loaders.core.fcntl.flock") as flock,
                patch.object(
                    EvidenceStore, "add_streamed_group",
                    side_effect=AssertionError("streaming must not start"),
                ),
            ):
                with self.assertRaisesRegex(
                    EvidenceStoreError,
                    "publication lock must be owned by the effective UID "
                    "and not group/other writable",
                ):
                    stream_directory_event_logs_to_store(
                        root, store_path, group_id="group-a",
                        shared_byte_budget=6, chunk_size=3,
                    )

            flock.assert_not_called()
            self.assertEqual(lock_path.read_bytes(), b"malicious-lock")
            self.assertFalse(store_path.exists())
            self.assertFalse(any(".stage-" in path.name for path in base.iterdir()))

    def test_root_mismatch_after_staging_close_leaves_new_store_absent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"event\n")
            store_path = base / "evidence.sqlite3"
            moved = base / "original-input"
            real_open_read_only = EvidenceStore.open_read_only
            replaced = False

            def replace_after_staging_close(path):
                nonlocal replaced
                if Path(path) != store_path and not replaced:
                    replaced = True
                    root.rename(moved)
                    root.mkdir()
                    (root / "events.txt").write_bytes(b"replacement\n")
                return real_open_read_only(path)

            with patch.object(EvidenceStore, "open_read_only", side_effect=replace_after_staging_close):
                with self.assertRaisesRegex(EvidenceStoreError, "root changed"):
                    stream_directory_event_logs_to_store(
                        root, store_path, group_id="group-a",
                        shared_byte_budget=6, chunk_size=3,
                    )

            self.assertTrue(replaced)
            self.assertFalse(store_path.exists())
            self.assertFalse(any(Path(f"{store_path}{suffix}").exists()
                                 for suffix in ("-journal", "-wal", "-shm")))

    def test_root_mismatch_after_staging_close_preserves_existing_c2_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"new-event\n")
            store_path = base / "evidence.sqlite3"
            with EvidenceStore(store_path) as store:
                store.add_group("existing")
            before = store_path.read_bytes()
            moved = base / "original-input"
            real_open_read_only = EvidenceStore.open_read_only
            replaced = False

            def replace_after_staging_close(path):
                nonlocal replaced
                if Path(path) != store_path and not replaced:
                    replaced = True
                    root.rename(moved)
                    root.mkdir()
                    (root / "events.txt").write_bytes(b"replacement\n")
                return real_open_read_only(path)

            with patch.object(EvidenceStore, "open_read_only", side_effect=replace_after_staging_close):
                with self.assertRaisesRegex(EvidenceStoreError, "root changed"):
                    stream_directory_event_logs_to_store(
                        root, store_path, group_id="group-new",
                        shared_byte_budget=10, chunk_size=4,
                    )

            self.assertTrue(replaced)
            self.assertEqual(store_path.read_bytes(), before)
            self.assertFalse(any(Path(f"{store_path}{suffix}").exists()
                                 for suffix in ("-journal", "-wal", "-shm")))
            with EvidenceStore.open_read_only(store_path) as store:
                self.assertIsNotNone(store.connection.execute(
                    "SELECT 1 FROM anr_groups WHERE id='existing'"
                ).fetchone())
                self.assertIsNone(store.connection.execute(
                    "SELECT 1 FROM anr_groups WHERE id='group-new'"
                ).fetchone())

    def test_root_mismatch_after_staging_close_preserves_existing_legacy_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"new-event\n")
            store_path = base / "evidence.sqlite3"
            _seed_legacy_database(store_path, [b"legacy\n"])
            before = store_path.read_bytes()
            moved = base / "original-input"
            real_open_read_only = EvidenceStore.open_read_only
            replaced = False

            def replace_after_staging_close(path):
                nonlocal replaced
                if Path(path) != store_path and not replaced:
                    replaced = True
                    root.rename(moved)
                    root.mkdir()
                    (root / "events.txt").write_bytes(b"replacement\n")
                return real_open_read_only(path)

            with patch.object(EvidenceStore, "open_read_only", side_effect=replace_after_staging_close):
                with self.assertRaisesRegex(EvidenceStoreError, "root changed"):
                    stream_directory_event_logs_to_store(
                        root, store_path, group_id="group-new",
                        shared_byte_budget=10, chunk_size=4,
                    )

            self.assertTrue(replaced)
            self.assertEqual(store_path.read_bytes(), before)
            connection = sqlite3.connect(store_path)
            try:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertIsNone(connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifact_chunks'"
                ).fetchone())
                self.assertIsNone(connection.execute(
                    "SELECT 1 FROM anr_groups WHERE id='group-new'"
                ).fetchone())
            finally:
                connection.close()

    def test_replace_failure_preserves_exact_existing_database_bytes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"new-event\n")
            store_path = base / "evidence.sqlite3"
            with EvidenceStore(store_path) as store:
                store.add_group("existing")
            before = store_path.read_bytes()

            with patch("anr_evidence.loaders.core.os.replace", side_effect=OSError("injected replace failure")):
                with self.assertRaisesRegex(OSError, "replace failure"):
                    stream_directory_event_logs_to_store(
                        root, store_path, group_id="group-new",
                        shared_byte_budget=10, chunk_size=4,
                    )

            self.assertEqual(store_path.read_bytes(), before)
            with EvidenceStore.open_read_only(store_path) as store:
                self.assertIsNone(store.connection.execute(
                    "SELECT 1 FROM anr_groups WHERE id='group-new'"
                ).fetchone())

    def test_post_replace_baseexception_preserves_published_generation_without_wipe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            content = b"new-event\n"
            (root / "events.txt").write_bytes(content)
            store_path = base / "evidence.sqlite3"
            with EvidenceStore(store_path) as store:
                store.add_group("existing")
            previous_identity = (store_path.stat().st_dev, store_path.stat().st_ino)
            real_replace = os.replace
            real_close = DirectoryEventLogConsumer.close
            replace_calls = []
            closed_consumers = []

            def replace_then_interrupt(source, destination, **kwargs):
                replace_calls.append((source, destination))
                real_replace(source, destination, **kwargs)
                raise KeyboardInterrupt("injected after completed replace")

            def track_close(consumer):
                closed_consumers.append(consumer)
                return real_close(consumer)

            with (
                patch(
                    "anr_evidence.loaders.core.os.replace",
                    side_effect=replace_then_interrupt,
                ),
                patch("anr_evidence.loaders.core.os.ftruncate") as truncate,
                patch.object(DirectoryEventLogConsumer, "close", new=track_close),
            ):
                with self.assertRaisesRegex(
                    KeyboardInterrupt, "after completed replace",
                ):
                    stream_directory_event_logs_to_store(
                        root, store_path, group_id="group-new",
                        shared_byte_budget=len(content), chunk_size=4,
                    )

            self.assertEqual(len(replace_calls), 1)
            truncate.assert_not_called()
            self.assertEqual(len(closed_consumers), 1)
            self.assertGreater(store_path.stat().st_size, 0)
            self.assertNotEqual(
                (store_path.stat().st_dev, store_path.stat().st_ino),
                previous_identity,
            )
            self.assertFalse(any(".stage-" in path.name for path in base.iterdir()))
            with EvidenceStore.open_read_only(store_path) as store:
                groups = store.connection.execute(
                    "SELECT id FROM anr_groups ORDER BY id"
                ).fetchall()
                artifact = store.connection.execute(
                    "SELECT byte_count FROM artifacts"
                ).fetchone()
            self.assertEqual(groups, [("existing",), ("group-new",)])
            self.assertEqual(artifact, (len(content),))

    def test_clean_persistent_wal_predecessor_clones_without_creating_final_sidecars(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            content = b"new-event\n"
            (root / "events.txt").write_bytes(content)
            store_path = base / "evidence.sqlite3"
            with EvidenceStore(store_path) as store:
                store.add_group("existing")
                self.assertEqual(
                    store.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0],
                    "wal",
                )
                store.connection.commit()
                store.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            sidecars = tuple(Path(f"{store_path}{suffix}") for suffix in ("-wal", "-shm"))
            self.assertFalse(any(path.exists() for path in sidecars))

            consumer = stream_directory_event_logs_to_store(
                root, store_path, group_id="group-new",
                shared_byte_budget=len(content), chunk_size=4,
            )
            self.addCleanup(consumer.close)

            self.assertFalse(any(path.exists() for path in sidecars))
            with EvidenceStore.open_read_only(store_path) as store:
                groups = store.connection.execute(
                    "SELECT id FROM anr_groups ORDER BY id"
                ).fetchall()
            self.assertEqual(groups, [("existing",), ("group-new",)])

    def test_output_parent_replacement_cannot_redirect_staged_publication(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            original_bytes = b"original-event\n"
            (root / "events.txt").write_bytes(original_bytes)
            output = base / "output"
            output.mkdir()
            store_path = output / "evidence.sqlite3"
            moved_output = base / "pinned-output"
            attacker_bytes = b"attacker-controlled-final"
            real_replace = os.replace
            replaced_parent = False

            def replace_parent_then_publish(source, destination, **kwargs):
                nonlocal replaced_parent
                if (destination == store_path.name and kwargs.get("dst_dir_fd") is not None
                        and not replaced_parent):
                    replaced_parent = True
                    output.rename(moved_output)
                    output.mkdir()
                    store_path.write_bytes(attacker_bytes)
                return real_replace(source, destination, **kwargs)

            with patch(
                "anr_evidence.loaders.core.os.replace",
                side_effect=replace_parent_then_publish,
            ):
                consumer = stream_directory_event_logs_to_store(
                    root, store_path, group_id="group-a",
                    shared_byte_budget=len(original_bytes), chunk_size=4,
                )
            self.addCleanup(consumer.close)

            self.assertTrue(replaced_parent)
            self.assertEqual(store_path.read_bytes(), attacker_bytes)
            pinned_final = moved_output / store_path.name
            with EvidenceStore.open_read_only(pinned_final) as store:
                self.assertIsNotNone(store.connection.execute(
                    "SELECT 1 FROM anr_groups WHERE id='group-a'"
                ).fetchone())
            descriptor = consumer.artifacts[0]
            self.assertEqual(
                consumer.read_span(
                    descriptor["artifactId"], 0, len(original_bytes),
                )["content"],
                original_bytes,
            )

    def test_prepublish_failure_surfaces_staging_unlink_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"event\n")
            store_path = base / "evidence.sqlite3"
            real_unlink = os.unlink
            retained_stage: list[str] = []

            def fail_stage_unlink(path, *args, **kwargs):
                if (kwargs.get("dir_fd") is not None
                        and isinstance(path, str) and ".stage-" in path
                        and path.endswith(".sqlite3")):
                    retained_stage.append(path)
                    raise PermissionError("injected staging unlink failure")
                return real_unlink(path, *args, **kwargs)

            with (
                patch(
                    "anr_evidence.loaders.core.os.replace",
                    side_effect=OSError("injected prepublish failure"),
                ),
                patch("anr_evidence.loaders.core.os.unlink", side_effect=fail_stage_unlink),
            ):
                with self.assertRaisesRegex(EvidenceStoreError, "cleanup failed") as raised:
                    stream_directory_event_logs_to_store(
                        root, store_path, group_id="group-a",
                        shared_byte_budget=6, chunk_size=3,
                    )

            self.assertIsInstance(raised.exception.__cause__, OSError)
            self.assertIn("prepublish failure", str(raised.exception.__cause__))
            self.assertIn("staging unlink", str(raised.exception))
            self.assertTrue(retained_stage)
            retained_path = base / retained_stage[0]
            self.assertTrue(retained_path.exists())
            self.assertEqual(retained_path.read_bytes(), b"")
            self.assertFalse(store_path.exists())

    def test_prepublish_failure_surfaces_stage_wipe_errors(self):
        for operation, label in (("ftruncate", "staging wipe truncate"),
                                 ("fsync", "staging wipe fsync")):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as tmpdir:
                base = Path(tmpdir)
                root = base / "input"
                root.mkdir()
                (root / "events.txt").write_bytes(b"event\n")
                store_path = base / "evidence.sqlite3"

                with (
                    patch(
                        "anr_evidence.loaders.core.os.replace",
                        side_effect=OSError("injected prepublish failure"),
                    ),
                    patch(
                        f"anr_evidence.loaders.core.os.{operation}",
                        side_effect=OSError(f"injected {operation} failure"),
                    ),
                ):
                    with self.assertRaisesRegex(EvidenceStoreError, "cleanup failed") as raised:
                        stream_directory_event_logs_to_store(
                            root, store_path, group_id="group-a",
                            shared_byte_budget=6, chunk_size=3,
                        )

                self.assertIn(label, str(raised.exception))
                self.assertIn(f"injected {operation} failure", str(raised.exception))
                self.assertIsInstance(raised.exception.__cause__, OSError)
                self.assertIn("prepublish failure", str(raised.exception.__cause__))
                self.assertFalse(store_path.exists())

    def test_prepublish_failure_wipes_stage_inode_renamed_away_from_known_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            raw_evidence = b"sensitive-raw-evidence\n"
            (root / "events.txt").write_bytes(raw_evidence)
            store_path = base / "evidence.sqlite3"
            retained_name = "renamed-former-stage.sqlite3"
            real_replace = os.replace
            renamed = False

            def rename_stage_then_fail(source, destination, **kwargs):
                nonlocal renamed
                if destination == store_path.name and kwargs.get("src_dir_fd") is not None:
                    os.rename(
                        source, retained_name,
                        src_dir_fd=kwargs["src_dir_fd"],
                        dst_dir_fd=kwargs["dst_dir_fd"],
                    )
                    renamed = True
                    raise OSError("injected prepublish failure after stage rename")
                return real_replace(source, destination, **kwargs)

            with patch(
                "anr_evidence.loaders.core.os.replace",
                side_effect=rename_stage_then_fail,
            ):
                with self.assertRaisesRegex(
                    OSError, "prepublish failure after stage rename",
                ):
                    stream_directory_event_logs_to_store(
                        root, store_path, group_id="group-a",
                        shared_byte_budget=len(raw_evidence), chunk_size=4,
                    )

            self.assertTrue(renamed)
            retained = base / retained_name
            self.assertTrue(retained.exists())
            self.assertEqual(retained.stat().st_size, 0)
            self.assertNotIn(raw_evidence, retained.read_bytes())
            self.assertFalse(store_path.exists())

    def test_root_replacement_inside_atomic_replace_publishes_original_snapshot_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            original_bytes = b"original-event\n"
            original_file = root / "events.txt"
            original_file.write_bytes(original_bytes)
            original_root_stat = root.stat()
            original_file_stat = original_file.stat()
            moved = base / "original-input"
            store_path = base / "evidence.sqlite3"
            real_replace = os.replace
            replaced_root = False

            def replace_root_then_publish(source, destination, **kwargs):
                nonlocal replaced_root
                if destination == store_path.name and kwargs.get("dst_dir_fd") is not None and not replaced_root:
                    replaced_root = True
                    root.rename(moved)
                    root.mkdir()
                    (root / "events.txt").write_bytes(b"replacement-event\n")
                return real_replace(source, destination, **kwargs)

            with patch("anr_evidence.loaders.core.os.replace", side_effect=replace_root_then_publish):
                consumer = stream_directory_event_logs_to_store(
                    root, store_path, group_id="group-a",
                    shared_byte_budget=len(original_bytes), chunk_size=4,
                )
            self.addCleanup(consumer.close)

            self.assertTrue(replaced_root)
            descriptor = consumer.artifacts[0]
            self.assertEqual(
                consumer.read_span(descriptor["artifactId"], 0, len(original_bytes))["content"],
                original_bytes,
            )
            origin = descriptor["origin"]
            self.assertEqual(origin["type"], "directory_fd_snapshot")
            self.assertEqual(origin["scheme"], "anr-directory-fd-snapshot-v1")
            self.assertEqual(origin["lexicalRoot"], str(root.absolute()))
            self.assertEqual(origin["relativePath"], "events.txt")
            self.assertEqual(origin["rootDevice"], str(original_root_stat.st_dev))
            self.assertEqual(origin["rootInode"], str(original_root_stat.st_ino))
            self.assertEqual(origin["fileDevice"], str(original_file_stat.st_dev))
            self.assertEqual(origin["fileInode"], str(original_file_stat.st_ino))
            self.assertNotIn("path", origin)
            self.assertNotEqual((root.stat().st_dev, root.stat().st_ino),
                                (original_root_stat.st_dev, original_root_stat.st_ino))
            self.assertNotEqual(((root / "events.txt").stat().st_dev,
                                 (root / "events.txt").stat().st_ino),
                                (original_file_stat.st_dev, original_file_stat.st_ino))

    def test_published_sqlite_sidecars_are_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"event\n")
            store_path = base / "evidence.sqlite3"
            with EvidenceStore(store_path):
                pass
            database_before = store_path.read_bytes()
            sidecars = {
                Path(f"{store_path}-journal"): b"journal-sentinel",
                Path(f"{store_path}-wal"): b"wal-sentinel",
                Path(f"{store_path}-shm"): b"shm-sentinel",
            }
            for sidecar, content in sidecars.items():
                sidecar.write_bytes(content)

            with self.assertRaisesRegex(EvidenceStoreError, "live SQLite sidecars"):
                stream_directory_event_logs_to_store(
                    root, store_path, group_id="group-a",
                    shared_byte_budget=6, chunk_size=3,
                )

            self.assertEqual(store_path.read_bytes(), database_before)
            self.assertEqual({path: path.read_bytes() for path in sidecars}, sidecars)

    def test_staging_and_lock_hardlink_aliases_are_not_ingested(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            content = b"event\n"
            (root / "events-source.txt").write_bytes(content)
            store_path = base / "evidence.sqlite3"
            real_open = os.open
            aliases_created = False

            def create_aliases(path, flags, *args, **kwargs):
                nonlocal aliases_created
                descriptor = real_open(path, flags, *args, **kwargs)
                if (not aliases_created and kwargs.get("dir_fd") is not None
                        and isinstance(path, str) and ".stage-" in path):
                    aliases_created = True
                    os.link(
                        path, root / "events-stage-alias.txt",
                        src_dir_fd=kwargs["dir_fd"],
                    )
                    os.link(
                        ".evidence.sqlite3.lock", root / "events-lock-alias.txt",
                        src_dir_fd=kwargs["dir_fd"],
                    )
                return descriptor

            with patch("anr_evidence.loaders.core.os.open", side_effect=create_aliases):
                consumer = stream_directory_event_logs_to_store(
                    root, store_path, group_id="group-a",
                    shared_byte_budget=len(content), chunk_size=3,
                )
            self.addCleanup(consumer.close)
            self.assertEqual([item["path"] for item in consumer.artifacts], ["events-source.txt"])

    def test_directory_stream_excludes_output_alias_and_sqlite_sidecars(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            event_content = b"am_anr: legitimate\n"
            (root / "events-source.txt").write_bytes(event_content)
            store_path = base / "output" / "nested" / ".." / "events.sqlite3"

            consumer = stream_directory_event_logs_to_store(
                root, store_path, group_id="group-a",
                shared_byte_budget=len(event_content), chunk_size=4,
            )

            self.assertEqual([item["path"] for item in consumer.artifacts], ["events-source.txt"])
            self.assertEqual(consumer.artifacts[0]["byteCount"], len(event_content))

    def test_directory_stream_rerun_does_not_ingest_existing_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            event_content = b"am_anr: rerun\n"
            (root / "events-source.txt").write_bytes(event_content)
            store_path = base / "events.sqlite3"

            first = stream_directory_event_logs_to_store(
                root, store_path, group_id="group-a",
                shared_byte_budget=len(event_content), chunk_size=4,
            )
            second = stream_directory_event_logs_to_store(
                root, store_path, group_id="group-b",
                shared_byte_budget=len(event_content), chunk_size=4,
            )

            self.assertEqual([item["path"] for item in first.artifacts], ["events-source.txt"])
            self.assertEqual([item["path"] for item in second.artifacts], ["events-source.txt"])
            with EvidenceStore(store_path) as store:
                paths = store.connection.execute(
                    "SELECT logical_path FROM artifacts ORDER BY logical_path"
                ).fetchall()
                groups = store.connection.execute(
                    "SELECT group_id, count(*) FROM group_artifacts GROUP BY group_id ORDER BY group_id"
                ).fetchall()
            self.assertEqual(paths, [("events-source.txt",)])
            self.assertEqual(groups, [("group-a", 1), ("group-b", 1)])

    def test_directory_stream_excludes_hardlink_to_existing_store(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            event_content = b"am_anr: legitimate\n"
            (root / "events-source.txt").write_bytes(event_content)
            store_path = base / "events.sqlite3"
            with EvidenceStore(store_path):
                pass
            try:
                os.link(store_path, root / "events-store-alias.sqlite3")
            except OSError as exc:
                self.skipTest(f"hard links unavailable: {exc}")

            consumer = stream_directory_event_logs_to_store(
                root, store_path, group_id="group-a",
                shared_byte_budget=len(event_content), chunk_size=4,
            )

            self.assertEqual([item["path"] for item in consumer.artifacts], ["events-source.txt"])

    def test_directory_stream_does_not_follow_symlink_files_or_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            event_content = b"am_anr: legitimate\n"
            (root / "events-source.txt").write_bytes(event_content)
            (outside / "events-hidden.txt").write_bytes(b"must not be discovered")
            try:
                (root / "events-file-link.txt").symlink_to(outside / "events-hidden.txt")
                (root / "linked-directory").symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")

            consumer = stream_directory_event_logs_to_store(
                root, base / "events.sqlite3", group_id="group-a",
                shared_byte_budget=len(event_content), chunk_size=4,
            )

            self.assertEqual([item["path"] for item in consumer.artifacts], ["events-source.txt"])

    def test_directory_stream_rejects_queued_directory_replaced_by_outside_symlink(self):
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            self.skipTest("secure descriptor-relative traversal unavailable")
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            queued = root / "queued"
            outside = base / "outside"
            queued.mkdir(parents=True)
            outside.mkdir()
            (queued / "events-inside.txt").write_bytes(b"am_anr: inside\n")
            (outside / "events-outside.txt").write_bytes(b"am_anr: outside\n")
            store_path = base / "events.sqlite3"
            real_open = os.open
            replaced = False

            def replace_queued_before_scan(path, flags, *args, **kwargs):
                nonlocal replaced
                if path == "queued" and kwargs.get("dir_fd") is not None and not replaced:
                    replaced = True
                    queued.rename(base / "queued-original")
                    queued.symlink_to(outside, target_is_directory=True)
                return real_open(path, flags, *args, **kwargs)

            with patch("anr_evidence.loaders.core.os.open", replace_queued_before_scan):
                with self.assertRaisesRegex(EvidenceStoreError, "Directory changed after discovery"):
                    stream_directory_event_logs_to_store(
                        root, store_path, group_id="group-a",
                        shared_byte_budget=1024, chunk_size=4,
                    )

            self.assertTrue(replaced)
            if store_path.exists():
                with EvidenceStore(store_path) as store:
                    self.assertEqual(
                        store.connection.execute("SELECT count(*) FROM artifacts").fetchone()[0],
                        0,
                    )

    def test_directory_stream_rejects_shard_switched_to_output_symlink_before_open(self):
        if not hasattr(os, "O_NOFOLLOW"):
            self.skipTest("O_NOFOLLOW unavailable")
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            victim = root / "events-victim.txt"
            victim.write_bytes(b"am_anr: original\n")
            store_path = base / "events.sqlite3"
            real_open = os.open
            switched = False

            def switch_before_open(path, flags, *args, **kwargs):
                nonlocal switched
                if path == victim.name and kwargs.get("dir_fd") is not None and not switched:
                    switched = True
                    victim.unlink()
                    victim.symlink_to(store_path)
                return real_open(path, flags, *args, **kwargs)

            with patch("anr_evidence.loaders.core.os.open", switch_before_open):
                with self.assertRaisesRegex(EvidenceStoreError, "safely opened"):
                    stream_directory_event_logs_to_store(
                        root, store_path, group_id="group-a",
                        shared_byte_budget=1024, chunk_size=4,
                    )
            self.assertTrue(switched)
            with EvidenceStore(store_path) as store:
                self.assertIsNone(store.connection.execute(
                    "SELECT 1 FROM anr_groups WHERE id='group-a'"
                ).fetchone())

    def test_consumer_reuses_one_read_only_connection_without_per_read_validation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"alpha\nbeta\ngamma\n")
            store_path = Path(tmpdir) / "evidence.sqlite3"
            consumer = stream_directory_event_logs_to_store(
                root, store_path, group_id="group-a", shared_byte_budget=64,
                chunk_size=4, max_read_bytes=16,
            )
            artifact_id = consumer.artifacts[0]["artifactId"]
            statements: list[str] = []
            consumer._store.connection.set_trace_callback(statements.append)
            try:
                self.assertEqual(
                    consumer.read_span(artifact_id, 1, 4)["content"], b"lph"
                )
                self.assertEqual(
                    [row["content"] for row in consumer.read_lines(artifact_id, 1, 1)],
                    [b"beta\n"],
                )
                self.assertEqual(
                    consumer._store.connection.execute("PRAGMA query_only").fetchone()[0], 1
                )
                writer = sqlite3.connect(store_path, timeout=0)
                try:
                    writer.execute("BEGIN IMMEDIATE")
                    writer.rollback()
                finally:
                    writer.close()
            finally:
                consumer.close()
            normalized = [statement.upper() for statement in statements]
            self.assertFalse(any("BEGIN IMMEDIATE" in statement for statement in normalized))
            self.assertFalse(any("FOREIGN_KEY_CHECK" in statement for statement in normalized))
            self.assertFalse(any("CONSTRAINT-PROBE" in statement for statement in normalized))
            self.assertFalse(any("SQLITE_MASTER" in statement for statement in normalized))

    def test_shared_budget_overflow_rolls_back_all_shards(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            (root / "events-1.txt").write_bytes(b"1234")
            (root / "events-2.txt").write_bytes(b"5678")
            store_path = base / "evidence.sqlite3"

            with self.assertRaisesRegex(EvidenceStoreBudgetExceeded, "budget exceeded"):
                stream_directory_event_logs_to_store(
                    root,
                    store_path,
                    group_id="group-a",
                    shared_byte_budget=6,
                    chunk_size=3,
                    max_read_bytes=8,
                )

            with EvidenceStore(store_path) as store:
                for table in ("artifacts", "artifact_chunks", "anr_groups", "group_artifacts"):
                    count = store.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    self.assertEqual(count, 0, table)


    def test_small_span_fetches_only_intersecting_chunks_within_cumulative_budget(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "evidence.sqlite3"
            content = bytes(range(128))
            with EvidenceStore(store_path) as store:
                artifact = store.add_streamed_group(
                    "group-a",
                    [{
                        "reader": io.BytesIO(content),
                        "source_kind": "event_log",
                        "logical_path": "events.txt",
                        "origin": {"type": "file", "path": "/events.txt"},
                    }],
                    shared_byte_budget=len(content),
                    chunk_size=16,
                )[0]
                recorder = _BlobReadRecorder()
                store.connection.row_factory = recorder.row_factory
                span = store.read_artifact_span(
                    group_id="group-a",
                    artifact_id=artifact["artifactId"],
                    source_kind="event_log",
                    start_byte=35,
                    end_byte=39,
                    max_bytes=16,

                           expected_artifact_commitment=artifact["artifactCommitment"],)
                self.assertEqual(span["content"], content[35:39])
                self.assertLessEqual(recorder.total_bytes, 16)
                self.assertLess(recorder.total_bytes, len(content))

                recorder.byte_counts.clear()
                with self.assertRaisesRegex(EvidenceStoreError, "read budget"):
                    store.read_artifact_span(
                        group_id="group-a",
                        artifact_id=artifact["artifactId"],
                        source_kind="event_log",
                        start_byte=35,
                        end_byte=39,
                        max_bytes=8,

                        expected_artifact_commitment=artifact["artifactCommitment"],)
                self.assertEqual(recorder.total_bytes, 0)

    def test_late_line_read_uses_index_and_obeys_cumulative_chunk_budget(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "evidence.sqlite3"
            lines = [f"L{index:03d}\n".encode() for index in range(120)]
            content = b"".join(lines)
            with EvidenceStore(store_path) as store:
                artifact = store.add_streamed_group(
                    "group-a",
                    [{
                        "reader": io.BytesIO(content),
                        "source_kind": "event_log",
                        "logical_path": "events.txt",
                        "origin": {"type": "file", "path": "/events.txt"},
                    }],
                    shared_byte_budget=len(content),
                    chunk_size=16,
                )[0]
                recorder = _BlobReadRecorder()
                store.connection.row_factory = recorder.row_factory
                selected = store.read_artifact_lines(
                    group_id="group-a",
                    artifact_id=artifact["artifactId"],
                    source_kind="event_log",
                    start_line=110,
                    line_count=1,
                    max_bytes=16,

                               expected_artifact_commitment=artifact["artifactCommitment"],)
                self.assertEqual([row["content"] for row in selected], [lines[110]])
                self.assertLessEqual(recorder.total_bytes, 16)
                self.assertLess(recorder.total_bytes, sum(map(len, lines[:111])))

                recorder.byte_counts.clear()
                with self.assertRaisesRegex(EvidenceStoreError, "read budget"):
                    store.read_artifact_lines(
                        group_id="group-a",
                        artifact_id=artifact["artifactId"],
                        source_kind="event_log",
                        start_line=110,
                        line_count=1,
                        max_bytes=8,

                        expected_artifact_commitment=artifact["artifactCommitment"],)
                self.assertEqual(recorder.total_bytes, 0)


    def test_committed_legacy_line_read_rejects_before_fetch_when_chunk_exceeds_budget(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "legacy-lines.sqlite3"
            content = b"".join(f"legacy-{index:03d}\n".encode() for index in range(100))
            with EvidenceStore(store_path) as store:
                store.add_group("group-a")
                artifact_id = store.add_artifact(
                    content, source_kind="event_log", logical_path="legacy.txt",
                    origin={"type": "file", "path": "/legacy.txt"},
                )
                store.attach_artifact("group-a", artifact_id)
                recorder = _BlobReadRecorder()
                store.connection.row_factory = recorder.row_factory

                with self.assertRaisesRegex(EvidenceStoreError, "chunk read budget"):
                    store.read_artifact_lines(
                        group_id="group-a", artifact_id=artifact_id,
                        source_kind="event_log", start_line=90, line_count=1,
                        max_bytes=32,

                        expected_artifact_commitment=store.artifact_commitment("group-a", artifact_id),)
                self.assertEqual(recorder.total_bytes, 0)

    def test_legacy_pre_c2_database_is_migrated_to_committed_storage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "legacy.sqlite3"
            content = b"first\nsecond\n"
            origin = {"type": "file", "path": "/legacy/events.txt"}
            artifact_id = _legacy_artifact_id("event_log", "events.txt", origin, content)
            connection = sqlite3.connect(store_path)
            connection.executescript("""
                CREATE TABLE artifacts (
                    id TEXT PRIMARY KEY,
                    source_kind TEXT NOT NULL,
                    logical_path TEXT NOT NULL,
                    origin_json TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    byte_count INTEGER NOT NULL,
                    content BLOB NOT NULL
                );
                CREATE TABLE anr_groups (id TEXT PRIMARY KEY);
                CREATE TABLE group_artifacts (
                    group_id TEXT NOT NULL REFERENCES anr_groups(id),
                    artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                    PRIMARY KEY (group_id, artifact_id)
                );
                CREATE TABLE spans (
                    id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    start_byte INTEGER NOT NULL CHECK (start_byte >= 0),
                    end_byte INTEGER NOT NULL CHECK (end_byte >= start_byte),
                    FOREIGN KEY (group_id, artifact_id)
                        REFERENCES group_artifacts(group_id, artifact_id)
                );
            """)
            connection.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (artifact_id, "event_log", "events.txt", json.dumps(origin, sort_keys=True),
                 hashlib.sha256(content).hexdigest(), len(content), content),
            )
            connection.execute("INSERT INTO anr_groups VALUES ('group-a')")
            connection.execute("INSERT INTO group_artifacts VALUES ('group-a', ?)", (artifact_id,))
            connection.commit()
            connection.close()

            with EvidenceStore(store_path) as store:
                columns = [row[1] for row in store.connection.execute("PRAGMA table_info(artifact_chunks)")]
                self.assertEqual(columns, ["artifact_id", "chunk_index", "start_byte", "content", "sha256"])
                range_index = tuple(
                    row[2] for row in store.connection.execute(
                        "PRAGMA index_info(artifact_chunks_range_idx)"
                    )
                )
                self.assertEqual(range_index, ("artifact_id", "start_byte"))
                span = store.read_artifact_span(
                    group_id="group-a", artifact_id=artifact_id, source_kind="event_log",
                    start_byte=6, end_byte=12, max_bytes=len(content),

                           expected_artifact_commitment=store.artifact_commitment("group-a", artifact_id),)
                self.assertEqual(span["content"], b"second")

    def test_inline_and_streamed_same_length_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "integrity.sqlite3"
            with EvidenceStore(store_path) as store:
                store.add_group("group-a")
                inline_id = store.add_artifact(
                    b"inline-data", source_kind="event_log", logical_path="inline.txt",
                    origin={"type": "file", "path": "/inline.txt"},
                )
                store.attach_artifact("group-a", inline_id)
                inline_span = store.add_span("group-a", inline_id, 0, 6)
                store.connection.execute(
                    "UPDATE artifacts SET content=? WHERE id=?", (b"INLINE-data", inline_id)
                )
                with self.assertRaisesRegex(EvidenceStoreError, "content changed"):
                    store.read_span_internal_integrity_only(inline_span, group_id="group-a", source_kind="event_log")

                streamed = store.add_streamed_group(
                    "group-a", [{
                        "reader": io.BytesIO(b"first\nsecond\n"),
                        "source_kind": "event_log", "logical_path": "streamed.txt",
                        "origin": {"type": "file", "path": "/streamed.txt"},
                    }], shared_byte_budget=32, chunk_size=6,
                )[0]
                streamed_span = store.add_span(
                    "group-a", streamed["artifactId"], 0, 5,
                )
                store.connection.execute(
                    "UPDATE artifact_chunks SET content=? WHERE artifact_id=? AND chunk_index=0",
                    (b"FIRST\n", streamed["artifactId"]),
                )
                with self.assertRaisesRegex(EvidenceStoreError, "chunk integrity"):
                    store.read_span(
                        streamed_span, group_id="group-a", source_kind="event_log", max_bytes=6,
                        expected_artifact_commitment=streamed["artifactCommitment"],
                    )
                with self.assertRaisesRegex(EvidenceStoreError, "chunk integrity"):
                    store.read_artifact_lines(
                        group_id="group-a", artifact_id=streamed["artifactId"],
                        source_kind="event_log", start_line=0, line_count=1, max_bytes=6,

                        expected_artifact_commitment=streamed["artifactCommitment"],)

    def test_public_read_span_requires_finite_bound_for_streamed_artifact(self):
        with EvidenceStore(":memory:") as store:
            artifact = store.add_streamed_group(
                "group-a", [{
                    "reader": io.BytesIO(b"bounded\n"), "source_kind": "event_log",
                    "logical_path": "events.txt",
                    "origin": {"type": "file", "path": "/events.txt"},
                }], shared_byte_budget=16, chunk_size=8,
            )[0]
            span_id = store.add_span("group-a", artifact["artifactId"], 0, 7)
            with self.assertRaisesRegex(EvidenceStoreError, "external artifact commitment"):
                store.read_span(span_id, group_id="group-a", source_kind="event_log")
            store.connection.execute(
                "UPDATE artifacts SET content=? WHERE id=?",
                (EvidenceStore._LEGACY_CHUNKED_MARKER, artifact["artifactId"]),
            )
            with self.assertRaisesRegex(EvidenceStoreError, "external artifact commitment"):
                store.read_span(
                    span_id, group_id="group-a", source_kind="event_log", max_bytes=8,
                )
            recorder = _BlobReadRecorder()
            store.connection.row_factory = recorder.row_factory
            with self.assertRaisesRegex(EvidenceStoreError, "explicit finite read limit"):
                store.read_span(
                    span_id, group_id="group-a", source_kind="event_log",
                    expected_artifact_commitment=artifact["artifactCommitment"],
                )
            self.assertEqual(recorder.total_bytes, 0)
            store.connection.row_factory = None
            self.assertEqual(
                store.read_span(
                    span_id, group_id="group-a", source_kind="event_log", max_bytes=8,
                    expected_artifact_commitment=artifact["artifactCommitment"],
                )["content"],
                b"bounded",
            )

    def test_chunk_range_reads_use_index_without_artifact_wide_metadata_scan(self):
        with EvidenceStore(":memory:") as store:
            artifacts = {}
            for chunk_count in (64, 4096):
                content = b"123456789012345\n" * chunk_count
                group_id = f"group-{chunk_count}"
                artifacts[chunk_count] = store.add_streamed_group(
                    group_id, [{
                        "reader": io.BytesIO(content), "source_kind": "event_log",
                        "logical_path": "events.txt",
                        "origin": {"type": "file", "path": f"/{chunk_count}/events.txt"},
                    }], shared_byte_budget=len(content), chunk_size=16,
                )[0]

            artifact_id = artifacts[4096]["artifactId"]
            predecessor_plan = store.connection.execute(
                "EXPLAIN QUERY PLAN " + store._CHUNK_PREDECESSOR_SQL,
                (artifact_id, 4093 * 16 + 3),
            ).fetchall()
            range_plan = store.connection.execute(
                "EXPLAIN QUERY PLAN " + store._CHUNK_RANGE_SQL,
                (artifact_id, 4093 * 16, 4093 * 16 + 5),
            ).fetchall()
            predecessor_detail = " ".join(row[3] for row in predecessor_plan)
            range_detail = " ".join(row[3] for row in range_plan)
            self.assertIn(
                "SEARCH artifact_chunks USING COVERING INDEX artifact_chunks_range_idx",
                predecessor_detail,
            )
            self.assertIn("artifact_id=? AND start_byte<?", predecessor_detail)
            self.assertIn(
                "SEARCH artifact_chunks USING INDEX artifact_chunks_range_idx", range_detail,
            )
            self.assertIn("artifact_id=? AND start_byte>? AND start_byte<?", range_detail)

            costs = {}
            for chunk_count, artifact in artifacts.items():
                group_id = f"group-{chunk_count}"
                start = (chunk_count - 2) * 16 + 3
                span_result = []
                span_steps = _sqlite_steps(
                    store.connection,
                    lambda: span_result.append(store.read_artifact_span(
                        group_id=group_id, artifact_id=artifact["artifactId"],
                        source_kind="event_log", start_byte=start, end_byte=start + 2,
                        max_bytes=16,

                                                   expected_artifact_commitment=artifact["artifactCommitment"],)),
                )
                line_result = []
                line_steps = _sqlite_steps(
                    store.connection,
                    lambda: line_result.extend(store.read_artifact_lines(
                        group_id=group_id, artifact_id=artifact["artifactId"],
                        source_kind="event_log", start_line=chunk_count - 2,
                        line_count=1, max_bytes=32,

                                                   expected_artifact_commitment=artifact["artifactCommitment"],)),
                )
                self.assertEqual(span_result[0]["content"], b"45")
                self.assertEqual(line_result[0]["content"], b"123456789012345\n")
                costs[chunk_count] = (span_steps, line_steps)

            small_span, small_line = costs[64]
            large_span, large_line = costs[4096]
            # Authenticated range proofs add logarithmic tree work, but never scan
            # artifact-wide chunk or line metadata.
            self.assertLess(large_span, small_span * 2)
            self.assertLess(large_line, small_line * 2)

    def test_missing_or_truncated_streamed_line_rows_fail_closed(self):
        for mutation, delete_sql, start_line in (
            ("all", "DELETE FROM artifact_lines WHERE artifact_id=?", 0),
            (
                "suffix",
                "DELETE FROM artifact_lines WHERE artifact_id=? AND line_number>=2",
                2,
            ),
        ):
            with self.subTest(mutation=mutation), EvidenceStore(":memory:") as store:
                artifact = store.add_streamed_group(
                    "group-a", [{
                        "reader": io.BytesIO(b"zero\none\ntwo\nthree\n"),
                        "source_kind": "event_log", "logical_path": "events.txt",
                        "origin": {"type": "file", "path": "/events.txt"},
                    }], shared_byte_budget=64, chunk_size=5,
                )[0]
                store.connection.execute(delete_sql, (artifact["artifactId"],))
                with self.assertRaisesRegex(EvidenceStoreError, "line index is incomplete"):
                    store.read_artifact_lines(
                        group_id="group-a", artifact_id=artifact["artifactId"],
                        source_kind="event_log", start_line=start_line,
                        line_count=2, max_bytes=32,

                        expected_artifact_commitment=artifact["artifactCommitment"],)

    def test_line_index_tampering_is_rejected_by_bounds_and_validated_bytes(self):
        with EvidenceStore(":memory:") as store:
            artifact = store.add_streamed_group(
                "group-a", [{
                    "reader": io.BytesIO(b"one\ntwo\n"), "source_kind": "event_log",
                    "logical_path": "events.txt",
                    "origin": {"type": "file", "path": "/events.txt"},
                }], shared_byte_budget=16, chunk_size=4,
            )[0]
            store.connection.execute(
                "UPDATE artifact_lines SET start_byte=1 WHERE artifact_id=? AND line_number=0",
                (artifact["artifactId"],),
            )
            with self.assertRaisesRegex(EvidenceStoreError, "line index"):
                store.read_artifact_lines(
                    group_id="group-a", artifact_id=artifact["artifactId"],
                    source_kind="event_log", start_line=0, line_count=1, max_bytes=8,

                    expected_artifact_commitment=artifact["artifactCommitment"],)

    def test_each_c2_table_and_required_index_is_validated(self):
        for target in (
            "artifact_chunks", "artifact_chunk_integrity", "artifact_lines",
            "artifact_integrity_nodes",
        ):
            with self.subTest(table=target), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad.sqlite3"
                with EvidenceStore(path):
                    pass
                connection = sqlite3.connect(path)
                connection.execute("PRAGMA foreign_keys=OFF")
                if target == "artifact_chunks":
                    connection.execute("DROP INDEX artifact_chunks_range_idx")
                if target == "artifact_lines":
                    connection.execute("DROP INDEX artifact_lines_byte_idx")
                connection.execute(f"DROP TABLE {target}")
                if target == "artifact_chunks":
                    connection.execute("CREATE TABLE artifact_chunks (artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON UPDATE CASCADE ON DELETE CASCADE, chunk_index INTEGER NOT NULL, start_byte INTEGER NOT NULL, content BLOB NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY (artifact_id, chunk_index))")
                    connection.execute("CREATE INDEX artifact_chunks_range_idx ON artifact_chunks(artifact_id, start_byte)")
                elif target == "artifact_chunk_integrity":
                    connection.execute("CREATE TABLE artifact_chunk_integrity (artifact_id TEXT NOT NULL PRIMARY KEY REFERENCES artifacts(id) ON UPDATE CASCADE ON DELETE CASCADE, chunk_count INTEGER NOT NULL, byte_count INTEGER NOT NULL, sha256 TEXT NOT NULL)")
                elif target == "artifact_lines":
                    connection.execute("CREATE TABLE artifact_lines (artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON UPDATE CASCADE ON DELETE CASCADE, line_number INTEGER NOT NULL, start_byte INTEGER NOT NULL, end_byte INTEGER NOT NULL, PRIMARY KEY (artifact_id, line_number))")
                    connection.execute("CREATE INDEX artifact_lines_byte_idx ON artifact_lines(artifact_id, start_byte, end_byte)")
                else:
                    connection.execute("CREATE TABLE artifact_integrity_nodes (artifact_id TEXT NOT NULL REFERENCES artifacts(id) ON UPDATE CASCADE ON DELETE CASCADE, tree_kind TEXT NOT NULL, level INTEGER NOT NULL, node_index INTEGER NOT NULL, hash TEXT NOT NULL, PRIMARY KEY (artifact_id, tree_kind, level, node_index))")
                connection.commit()
                connection.close()
                with self.assertRaisesRegex(
                    EvidenceStoreError, f"Incompatible {target} schema|constraint behavior|canonical C2 schema SQL"
                ):
                    EvidenceStore(path)

        malformed_indexes = {
            "artifact_chunks_range_idx": (
                "artifact_chunks",
                "CREATE INDEX artifact_chunks_range_idx "
                "ON artifact_chunks(artifact_id, chunk_index)",
            ),
            "artifact_lines_byte_idx": (
                "artifact_lines",
                "CREATE INDEX artifact_lines_byte_idx "
                "ON artifact_lines(artifact_id, line_number, end_byte)",
            ),
        }
        for index_name, (_, replacement_sql) in malformed_indexes.items():
            with self.subTest(index=index_name), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad-index.sqlite3"
                with EvidenceStore(path):
                    pass
                connection = sqlite3.connect(path)
                connection.execute(f"DROP INDEX {index_name}")
                connection.execute(replacement_sql)
                connection.commit()
                connection.close()
                with self.assertRaisesRegex(EvidenceStoreError, index_name):
                    EvidenceStore(path)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "partial-index.sqlite3"
            with EvidenceStore(path):
                pass
            connection = sqlite3.connect(path)
            connection.execute("DROP INDEX artifact_chunks_range_idx")
            connection.execute(
                "CREATE INDEX artifact_chunks_range_idx "
                "ON artifact_chunks(artifact_id, start_byte) WHERE start_byte >= 0"
            )
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(EvidenceStoreError, "artifact_chunks_range_idx"):
                EvidenceStore(path)

    def test_missing_required_check_constraint_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "missing-check.sqlite3"
            with EvidenceStore(path):
                pass
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("DROP INDEX artifact_chunks_range_idx")
            connection.execute("DROP TABLE artifact_chunks")
            connection.execute(
                "CREATE TABLE artifact_chunks ("
                "artifact_id TEXT NOT NULL REFERENCES artifacts(id) "
                "ON UPDATE CASCADE ON DELETE CASCADE, "
                "chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0), "
                "start_byte INTEGER NOT NULL, "
                "content BLOB NOT NULL CHECK (length(content) > 0), "
                "sha256 TEXT NOT NULL CHECK (length(sha256) = 64), "
                "PRIMARY KEY (artifact_id, chunk_index))"
            )
            connection.execute(
                "CREATE INDEX artifact_chunks_range_idx "
                "ON artifact_chunks(artifact_id, start_byte)"
            )
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(
                EvidenceStoreError, "Incompatible artifact_chunks schema|constraint behavior|canonical C2 schema SQL"
            ):
                EvidenceStore(path)

    def test_constraint_probe_rejects_trigger_spoof_and_unique_collision_unchanged(self):
        mutations = {
            "trigger_spoof": """
                CREATE TRIGGER spoof_span_check BEFORE INSERT ON spans
                WHEN NEW.start_byte < 0
                BEGIN SELECT RAISE(ABORT, 'spoofed check'); END
            """,
            "unique_collision": """
                CREATE TRIGGER collide_probe_artifact AFTER INSERT ON artifacts
                WHEN NEW.source_kind = 'probe'
                BEGIN
                    INSERT INTO artifacts VALUES (
                        NEW.id, NEW.source_kind, NEW.logical_path, NEW.origin_json,
                        NEW.sha256, NEW.byte_count, NEW.content
                    );
                END
            """,
        }
        for mutation, trigger_sql in mutations.items():
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "probe-spoof.sqlite3"
                with EvidenceStore(path):
                    pass
                connection = sqlite3.connect(path)
                connection.execute(trigger_sql)
                before_schema = connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                ).fetchall()
                before_rows = {
                    table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                    for table in ("artifacts", "anr_groups", "group_artifacts", "spans")
                }
                connection.commit()
                connection.close()

                with self.assertRaisesRegex(EvidenceStoreError, "Unexpected C2.*schema object"):
                    EvidenceStore(path)

                connection = sqlite3.connect(path)
                self.assertEqual(connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                ).fetchall(), before_schema)
                self.assertEqual({
                    table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                    for table in ("artifacts", "anr_groups", "group_artifacts", "spans")
                }, before_rows)
                connection.close()

    def test_version_two_trigger_is_rejected_before_execution_and_database_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "malicious-trigger.sqlite3"
            with EvidenceStore(path):
                pass
            connection = sqlite3.connect(path)
            connection.execute("""
                CREATE TRIGGER attacker_probe AFTER INSERT ON artifacts
                BEGIN
                    INSERT INTO anr_groups VALUES ('attacker-trigger-fired');
                END
            """)
            connection.commit()
            before_schema = connection.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            ).fetchall()
            before_rows = {
                table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("artifacts", "anr_groups", "group_artifacts", "spans")
            }
            connection.close()
            before_bytes = path.read_bytes()

            with self.assertRaisesRegex(EvidenceStoreError, "Unexpected C2.*schema object"):
                EvidenceStore(path)

            self.assertEqual(path.read_bytes(), before_bytes)
            connection = sqlite3.connect(path)
            self.assertEqual(connection.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            ).fetchall(), before_schema)
            self.assertEqual({
                table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in ("artifacts", "anr_groups", "group_artifacts", "spans")
            }, before_rows)
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM anr_groups WHERE id='attacker-trigger-fired'"
            ).fetchone())
            connection.close()

    def test_old_predictable_probe_ids_cannot_collide_and_probes_leave_rows_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "old-probe-ids.sqlite3"
            with EvidenceStore(path):
                pass
            connection = sqlite3.connect(path)
            empty_hash = hashlib.sha256(b"").hexdigest()
            connection.execute(
                "INSERT INTO artifacts VALUES (?, 'probe', 'probe', '{}', ?, 0, X'')",
                ("constraint-probe-artifact", empty_hash),
            )
            connection.execute("INSERT INTO anr_groups VALUES ('constraint-probe-group')")
            connection.execute(
                "INSERT INTO group_artifacts VALUES "
                "('constraint-probe-group', 'constraint-probe-artifact')"
            )
            before = {
                table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in (
                    "artifacts", "anr_groups", "group_artifacts", "spans",
                    "artifact_chunks", "artifact_chunk_integrity", "artifact_lines",
                    "artifact_integrity_nodes", "artifact_commitments",
                )
            }
            connection.commit()
            connection.close()

            with EvidenceStore(path):
                pass

            connection = sqlite3.connect(path)
            self.assertEqual({
                table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in (
                    "artifacts", "anr_groups", "group_artifacts", "spans",
                    "artifact_chunks", "artifact_chunk_integrity", "artifact_lines",
                    "artifact_integrity_nodes", "artifact_commitments",
                )
            }, before)
            connection.close()

    def test_unrecognized_nonempty_version_zero_database_is_rejected_unchanged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "foreign-v0.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript("""
                CREATE TABLE unrelated (id INTEGER PRIMARY KEY);
                CREATE INDEX unrelated_idx ON unrelated(id);
                CREATE VIEW unrelated_view AS SELECT id FROM unrelated;
                CREATE TRIGGER unrelated_trigger AFTER INSERT ON unrelated BEGIN
                    UPDATE unrelated SET id=id WHERE id=NEW.id;
                END;
                INSERT INTO unrelated VALUES (7);
            """)
            before_schema = connection.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            ).fetchall()
            before_rows = connection.execute("SELECT * FROM unrelated").fetchall()
            connection.commit()
            connection.close()

            with self.assertRaisesRegex(EvidenceStoreError, "Unrecognized nonempty version-0"):
                EvidenceStore(path)

            connection = sqlite3.connect(path)
            self.assertEqual(connection.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            ).fetchall(), before_schema)
            self.assertEqual(connection.execute("SELECT * FROM unrelated").fetchall(), before_rows)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            connection.close()

    def test_incomplete_or_wrong_version_c2_schema_is_rejected_without_migration(self):
        for mutation, expected in (("partial", "Incomplete"), ("version", "version")):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad-schema.sqlite3"
                with EvidenceStore(path):
                    pass
                connection = sqlite3.connect(path)
                if mutation == "partial":
                    connection.execute("DROP INDEX artifact_lines_byte_idx")
                    connection.execute("DROP TABLE artifact_lines")
                else:
                    connection.execute("PRAGMA user_version=1")
                before = connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                ).fetchall()
                connection.commit()
                connection.close()

                with self.assertRaisesRegex(EvidenceStoreError, expected):
                    EvidenceStore(path)

                connection = sqlite3.connect(path)
                after = connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                ).fetchall()
                self.assertEqual(after, before)
                if mutation == "partial":
                    self.assertIsNone(connection.execute(
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type='table' AND name='artifact_lines'"
                    ).fetchone())
                else:
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                connection.close()

    def test_foreign_key_check_rejects_dangling_c2_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad-fk.sqlite3"
            with EvidenceStore(path):
                pass
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute(
                "INSERT INTO artifact_lines VALUES ('missing', 0, 0, 1)"
            )
            connection.commit()
            connection.close()
            with self.assertRaisesRegex(EvidenceStoreError, "foreign key check failed"):
                EvidenceStore(path)

    def test_chunk_and_db_hash_tampering_fails_against_authenticated_artifact_root(self):
        with EvidenceStore(":memory:") as store:
            artifact = store.add_streamed_group(
                "group-a", [{
                    "reader": io.BytesIO(b"alpha\nbeta\n"),
                    "source_kind": "event_log", "logical_path": "events.txt",
                    "origin": {"type": "file", "path": "/events.txt"},
                }], shared_byte_budget=32, chunk_size=6,
            )[0]
            artifact_id = artifact["artifactId"]
            replacement = b"ALPHA\n"
            store.connection.execute(
                "UPDATE artifact_chunks SET content=?, sha256=? "
                "WHERE artifact_id=? AND chunk_index=0",
                (replacement, hashlib.sha256(replacement).hexdigest(), artifact_id),
            )
            recorder = _BlobReadRecorder()
            store.connection.row_factory = recorder.row_factory
            with self.assertRaisesRegex(EvidenceStoreError, "chunk integrity"):
                store.read_artifact_span(
                    group_id="group-a", artifact_id=artifact_id, source_kind="event_log",
                    start_byte=0, end_byte=5, max_bytes=6,

                    expected_artifact_commitment=store.artifact_commitment("group-a", artifact_id),)
            self.assertEqual(recorder.total_bytes, len(replacement))

    def test_fetched_chunk_range_proof_tampering_is_rejected(self):
        with EvidenceStore(":memory:") as store:
            artifact = store.add_streamed_group(
                "group-a", [{
                    "reader": io.BytesIO(b"abcdefgh"),
                    "source_kind": "event_log", "logical_path": "events.txt",
                    "origin": {"type": "file", "path": "/events.txt"},
                }], shared_byte_budget=8, chunk_size=4,
            )[0]
            artifact_id = artifact["artifactId"]
            store.connection.execute(
                "UPDATE artifact_integrity_nodes SET hash=? "
                "WHERE artifact_id=? AND tree_kind='chunk' AND level=0 AND node_index=1",
                ("f" * 64, artifact_id),
            )
            with self.assertRaisesRegex(EvidenceStoreError, "chunk integrity"):
                store.read_artifact_span(
                    group_id="group-a", artifact_id=artifact_id, source_kind="event_log",
                    start_byte=0, end_byte=2, max_bytes=4,

                    expected_artifact_commitment=store.artifact_commitment("group-a", artifact_id),)

    def test_coordinated_line_index_relabel_is_rejected_by_authenticated_line_root(self):
        with EvidenceStore(":memory:") as store:
            artifact = store.add_streamed_group(
                "group-a", [{
                    "reader": io.BytesIO(b"one\ntwo\nthree\nfour\n"),
                    "source_kind": "event_log", "logical_path": "events.txt",
                    "origin": {"type": "file", "path": "/events.txt"},
                }], shared_byte_budget=64, chunk_size=8,
            )[0]
            artifact_id = artifact["artifactId"]
            store.connection.execute(
                "UPDATE artifact_lines SET "
                "start_byte=CASE line_number WHEN 1 THEN 8 WHEN 2 THEN 14 ELSE start_byte END, "
                "end_byte=CASE line_number WHEN 0 THEN 8 WHEN 1 THEN 14 WHEN 2 THEN 19 ELSE end_byte END "
                "WHERE artifact_id=? AND line_number<=2",
                (artifact_id,),
            )
            with self.assertRaisesRegex(EvidenceStoreError, "line integrity"):
                store.read_artifact_lines(
                    group_id="group-a", artifact_id=artifact_id, source_kind="event_log",
                    start_line=1, line_count=1, max_bytes=16,

                    expected_artifact_commitment=store.artifact_commitment("group-a", artifact_id),)

    def test_c2_constraints_are_validated_behaviorally_not_by_schema_comments(self):
        with EvidenceStore(":memory:") as store:
            artifact_id = store.add_artifact(
                b"x", source_kind="event_log", logical_path="x",
                origin={"type": "file", "path": "/x"},
            )
            good_hash = "0" * 64
            bad_hash = "g" * 64
            rejected = (
                ("INSERT INTO artifact_chunks VALUES (?, -1, 0, X'01', ?)",
                 (artifact_id, good_hash)),
                ("INSERT INTO artifact_chunks VALUES (?, 0, -1, X'01', ?)",
                 (artifact_id, good_hash)),
                ("INSERT INTO artifact_chunks VALUES (?, 0, 0, X'', ?)",
                 (artifact_id, good_hash)),
                ("INSERT INTO artifact_chunks VALUES (?, 0, 0, X'01', ?)",
                 (artifact_id, bad_hash)),
                ("INSERT INTO artifact_chunk_integrity VALUES (?, -1, 0, 0, ?, ?, ?, ?)",
                 (artifact_id, good_hash, good_hash, good_hash, good_hash)),
                ("INSERT INTO artifact_chunk_integrity VALUES (?, 0, -1, 0, ?, ?, ?, ?)",
                 (artifact_id, good_hash, good_hash, good_hash, good_hash)),
                ("INSERT INTO artifact_chunk_integrity VALUES (?, 0, 0, -1, ?, ?, ?, ?)",
                 (artifact_id, good_hash, good_hash, good_hash, good_hash)),
                ("INSERT INTO artifact_chunk_integrity VALUES (?, 0, 0, 0, ?, ?, ?, ?)",
                 (artifact_id, bad_hash, good_hash, good_hash, good_hash)),
                ("INSERT INTO artifact_chunk_integrity VALUES (?, 0, 0, 0, ?, ?, ?, ?)",
                 (artifact_id, good_hash, bad_hash, good_hash, good_hash)),
                ("INSERT INTO artifact_chunk_integrity VALUES (?, 0, 0, 0, ?, ?, ?, ?)",
                 (artifact_id, good_hash, good_hash, bad_hash, good_hash)),
                ("INSERT INTO artifact_chunk_integrity VALUES (?, 0, 0, 0, ?, ?, ?, ?)",
                 (artifact_id, good_hash, good_hash, good_hash, bad_hash)),
                ("INSERT INTO artifact_lines VALUES (?, -1, 0, 1)", (artifact_id,)),
                ("INSERT INTO artifact_lines VALUES (?, 0, -1, 1)", (artifact_id,)),
                ("INSERT INTO artifact_lines VALUES (?, 0, 0, -1)", (artifact_id,)),
                ("INSERT INTO artifact_integrity_nodes VALUES (?, 'invalid', 0, 0, ?)",
                 (artifact_id, good_hash)),
                ("INSERT INTO artifact_integrity_nodes VALUES (?, 'chunk', -1, 0, ?)",
                 (artifact_id, good_hash)),
                ("INSERT INTO artifact_integrity_nodes VALUES (?, 'chunk', 0, -1, ?)",
                 (artifact_id, good_hash)),
                ("INSERT INTO artifact_integrity_nodes VALUES (?, 'chunk', 0, 0, ?)",
                 (artifact_id, bad_hash)),
                ("INSERT INTO artifact_commitments VALUES (?, ?)",
                 (artifact_id, bad_hash)),
            )
            for statement, parameters in rejected:
                with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError):
                    store.connection.execute(statement, parameters)

    def test_each_declared_c2_check_is_required_for_writable_and_read_only_open(self):
        constraint_removals = (
            (0, "chunk_index", "CHECK (chunk_index >= 0)"),
            (0, "chunk_start_byte", "CHECK (start_byte >= 0)"),
            (0, "chunk_content", "CHECK (length(content) > 0)"),
            (0, "chunk_sha256", "CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*')"),
            (1, "integrity_chunk_count", "CHECK (chunk_count >= 0)"),
            (1, "integrity_line_count", "CHECK (line_count >= 0)"),
            (1, "integrity_byte_count", "CHECK (byte_count >= 0)"),
            (1, "integrity_sha256", "CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*')"),
            (1, "integrity_chunk_root", "CHECK (length(chunk_root) = 64 AND chunk_root NOT GLOB '*[^0-9a-f]*')"),
            (1, "integrity_line_root", "CHECK (length(line_root) = 64 AND line_root NOT GLOB '*[^0-9a-f]*')"),
            (1, "integrity_authenticated_root", "CHECK (length(authenticated_root) = 64 AND authenticated_root NOT GLOB '*[^0-9a-f]*')"),
            (2, "line_number", "CHECK (line_number >= 0)"),
            (2, "line_start_byte", "CHECK (start_byte >= 0)"),
            (2, "line_end_byte", "CHECK (end_byte > start_byte)"),
            (3, "node_tree_kind", "CHECK (tree_kind IN ('chunk', 'line'))"),
            (3, "node_level", "CHECK (level >= 0)"),
            (3, "node_index", "CHECK (node_index >= 0)"),
            (3, "node_hash", "CHECK (length(hash) = 64 AND hash NOT GLOB '*[^0-9a-f]*')"),
            (6, "commitment_authenticated_root", "CHECK (length(authenticated_root) = 64 AND authenticated_root NOT GLOB '*[^0-9a-f]*')"),
        )
        for statement_index, name, constraint_sql in constraint_removals:
            with self.subTest(constraint=name), tempfile.TemporaryDirectory() as tmpdir:
                statements = list(EvidenceStore._C2_SCHEMA)
                self.assertEqual(statements[statement_index].count(constraint_sql), 1)
                statements[statement_index] = statements[statement_index].replace(
                    constraint_sql, "", 1
                )
                path = Path(tmpdir) / f"missing-{name}.sqlite3"
                connection = sqlite3.connect(path)
                connection.executescript(
                    ";\n".join((*EvidenceStore._BASE_SCHEMA, *statements))
                    + f";\nPRAGMA user_version={EvidenceStore.C2_SCHEMA_VERSION};"
                )
                connection.close()

                with self.assertRaisesRegex(
                    EvidenceStoreError, "canonical C2 schema SQL|constraint behavior"
                ):
                    EvidenceStore(path)
                with self.assertRaisesRegex(
                    EvidenceStoreError, "canonical C2 schema SQL"
                ):
                    EvidenceStore.open_read_only(path)

    def test_unknown_or_malformed_legacy_base_rejects_transactionally_unchanged(self):
        mutations = ("unknown_version", "missing_table", "missing_fk", "missing_check")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "legacy.sqlite3"
                connection = sqlite3.connect(path)
                connection.executescript("""
                    CREATE TABLE artifacts (id TEXT PRIMARY KEY, source_kind TEXT NOT NULL, logical_path TEXT NOT NULL, origin_json TEXT NOT NULL, sha256 TEXT NOT NULL, byte_count INTEGER NOT NULL, content BLOB NOT NULL);
                    CREATE TABLE anr_groups (id TEXT PRIMARY KEY);
                    CREATE TABLE group_artifacts (group_id TEXT NOT NULL REFERENCES anr_groups(id), artifact_id TEXT NOT NULL REFERENCES artifacts(id), PRIMARY KEY (group_id, artifact_id));
                    CREATE TABLE spans (id TEXT PRIMARY KEY, group_id TEXT NOT NULL, artifact_id TEXT NOT NULL, start_byte INTEGER NOT NULL CHECK (start_byte >= 0), end_byte INTEGER NOT NULL CHECK (end_byte >= start_byte), FOREIGN KEY (group_id, artifact_id) REFERENCES group_artifacts(group_id, artifact_id));
                """)
                if mutation == "unknown_version":
                    connection.execute("PRAGMA user_version=77")
                elif mutation == "missing_table":
                    connection.execute("DROP TABLE spans")
                elif mutation == "missing_fk":
                    connection.execute("DROP TABLE group_artifacts")
                    connection.execute(
                        "CREATE TABLE group_artifacts (group_id TEXT NOT NULL, artifact_id TEXT NOT NULL REFERENCES artifacts(id), PRIMARY KEY (group_id, artifact_id))"
                    )
                else:
                    connection.execute("DROP TABLE spans")
                    connection.execute(
                        "CREATE TABLE spans (id TEXT PRIMARY KEY, group_id TEXT NOT NULL, artifact_id TEXT NOT NULL, start_byte INTEGER NOT NULL, end_byte INTEGER NOT NULL CHECK (end_byte >= start_byte), FOREIGN KEY (group_id, artifact_id) REFERENCES group_artifacts(group_id, artifact_id))"
                    )
                connection.commit()
                before = connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                ).fetchall()
                before_version = connection.execute("PRAGMA user_version").fetchone()[0]
                connection.close()

                with self.assertRaises(EvidenceStoreError):
                    EvidenceStore(path)

                connection = sqlite3.connect(path)
                self.assertEqual(connection.execute(
                    "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
                ).fetchall(), before)
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], before_version)
                self.assertIsNone(connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='artifact_chunks'"
                ).fetchone())
                connection.close()

    def test_inline_stream_dedup_is_canonical_in_both_insertion_orders(self):
        content = b"same\nartifact\n"
        origin = {"type": "file", "path": "/events.txt"}
        for order in ("stream_then_inline", "inline_then_stream"):
            with self.subTest(order=order), EvidenceStore(":memory:") as store:
                store.add_group("group-a")
                if order == "stream_then_inline":
                    streamed = store.add_streamed_group(
                        "group-a", [{
                            "reader": io.BytesIO(content), "source_kind": "event_log",
                            "logical_path": "events.txt", "origin": origin,
                        }], shared_byte_budget=len(content), chunk_size=4,
                    )[0]
                    artifact_id = store.add_artifact(
                        content, source_kind="event_log", logical_path="events.txt", origin=origin,
                    )
                    self.assertEqual(artifact_id, streamed["artifactId"])
                else:
                    artifact_id = store.add_artifact(
                        content, source_kind="event_log", logical_path="events.txt", origin=origin,
                    )
                    store.attach_artifact("group-a", artifact_id)
                    streamed = store.add_streamed_group(
                        "group-a", [{
                            "reader": io.BytesIO(content), "source_kind": "event_log",
                            "logical_path": "events.txt", "origin": origin,
                        }], shared_byte_budget=len(content), chunk_size=4,
                    )[0]
                    self.assertEqual(artifact_id, streamed["artifactId"])
                store.attach_artifact("group-a", artifact_id)
                self.assertIsNotNone(store.connection.execute(
                    "SELECT 1 FROM artifact_chunk_integrity WHERE artifact_id=?", (artifact_id,)
                ).fetchone())
                marker = store.connection.execute(
                    "SELECT content FROM artifacts WHERE id=?", (artifact_id,)
                ).fetchone()[0]
                self.assertIn(marker, (b"", EvidenceStore._LEGACY_CHUNKED_MARKER))
                self.assertEqual(store.connection.execute(
                    "SELECT count(*) FROM artifacts"
                ).fetchone()[0], 1)
                self.assertEqual(store.read_artifact_span(
                    group_id="group-a", artifact_id=artifact_id, source_kind="event_log",
                    start_byte=0, end_byte=len(content), max_bytes=len(content),

                                     expected_artifact_commitment=store.artifact_commitment("group-a", artifact_id),)["content"], content)

    def test_empty_shards_cannot_bypass_artifact_count_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "input"
            root.mkdir()
            (root / "events-a.txt").write_bytes(b"")
            (root / "events-b.txt").write_bytes(b"")
            store_path = Path(tmpdir) / "evidence.sqlite3"
            with EvidenceStore(store_path) as store:
                store.add_group("seed")
                seed_id = store.add_artifact(
                    b"seed", source_kind="event_log", logical_path="seed.txt",
                    origin={"type": "file", "path": "/seed.txt"},
                )
                store.attach_artifact("seed", seed_id)
            with self.assertRaisesRegex(EvidenceStoreBudgetExceeded, "artifact count"):
                stream_directory_event_logs_to_store(
                    root, store_path, group_id="group-a", shared_byte_budget=0,
                    chunk_size=4, max_artifacts=1,
                )
            with EvidenceStore(store_path) as store:
                self.assertEqual(
                    store.read_artifact_span(
                        group_id="seed", artifact_id=seed_id, source_kind="event_log",
                        start_byte=0, end_byte=4, max_bytes=4,

                        expected_artifact_commitment=store.artifact_commitment("seed", seed_id),)["content"],
                    b"seed",
                )
                self.assertIsNone(store.connection.execute(
                    "SELECT 1 FROM anr_groups WHERE id='group-a'"
                ).fetchone())

    def test_discovery_and_line_index_limits_fail_without_partial_consumer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"a\nb\nc\n")
            (root / "unrelated.bin").write_bytes(b"x")
            store_path = Path(tmpdir) / "evidence.sqlite3"
            with self.assertRaisesRegex(EvidenceStoreBudgetExceeded, "discovery entry"):
                stream_directory_event_logs_to_store(
                    root, store_path, group_id="group-a", shared_byte_budget=8,
                    max_discovery_entries=1,
                )
            self.assertFalse(store_path.exists())

            with EvidenceStore(store_path) as store:
                store.add_group("seed")
                seed_id = store.add_artifact(
                    b"seed", source_kind="event_log", logical_path="seed.txt",
                    origin={"type": "file", "path": "/seed.txt"},
                )
                store.attach_artifact("seed", seed_id)
            with self.assertRaisesRegex(EvidenceStoreBudgetExceeded, "Line-index row"):
                stream_directory_event_logs_to_store(
                    root, store_path, group_id="group-a", shared_byte_budget=8,
                    chunk_size=4, max_line_index_rows=2,
                )
            with EvidenceStore(store_path) as store:
                self.assertEqual(
                    store.read_artifact_span(
                        group_id="seed", artifact_id=seed_id, source_kind="event_log",
                        start_byte=0, end_byte=4, max_bytes=4,

                        expected_artifact_commitment=store.artifact_commitment("seed", seed_id),)["content"], b"seed",
                )
                self.assertIsNone(store.connection.execute(
                    "SELECT 1 FROM anr_groups WHERE id='group-a'"
                ).fetchone())

    def test_later_ingestion_failure_preserves_existing_rows_and_removes_all_new_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "evidence.sqlite3"
            with EvidenceStore(store_path) as store:
                store.add_group("legacy-group")
                legacy_id = store.add_artifact(
                    b"legacy\n", source_kind="event_log", logical_path="legacy.txt",
                    origin={"type": "file", "path": "/legacy.txt"},
                )
                store.attach_artifact("legacy-group", legacy_id)
                streamed_id = store.add_streamed_group(
                    "streamed-group",
                    [{
                        "reader": io.BytesIO(b"streamed\n"),
                        "source_kind": "event_log",
                        "logical_path": "streamed.txt",
                        "origin": {"type": "file", "path": "/streamed.txt"},
                    }],
                    shared_byte_budget=32,
                    chunk_size=4,
                )[0]["artifactId"]
                tables = (
                    "artifacts", "artifact_chunks", "artifact_chunk_integrity",
                    "artifact_lines", "anr_groups", "group_artifacts", "spans",
                )
                before = {
                    table: store.connection.execute(
                        f"SELECT * FROM {table} ORDER BY 1, 2" if table not in ("anr_groups", "spans")
                        else f"SELECT * FROM {table} ORDER BY 1"
                    ).fetchall()
                    for table in tables
                }

                with self.assertRaisesRegex(OSError, "later ingestion failure"):
                    store.add_streamed_group(
                        "new-group",
                        [
                            {
                                "reader": io.BytesIO(b"new-complete\n"),
                                "source_kind": "event_log",
                                "logical_path": "new-complete.txt",
                                "origin": {"type": "file", "path": "/new-complete.txt"},
                            },
                            {
                                "reader": _FailingReader(b"partial"),
                                "source_kind": "event_log",
                                "logical_path": "new-partial.txt",
                                "origin": {"type": "file", "path": "/new-partial.txt"},
                            },
                        ],
                        shared_byte_budget=64,
                        chunk_size=4,
                    )

                after = {
                    table: store.connection.execute(
                        f"SELECT * FROM {table} ORDER BY 1, 2" if table not in ("anr_groups", "spans")
                        else f"SELECT * FROM {table} ORDER BY 1"
                    ).fetchall()
                    for table in tables
                }
                self.assertEqual(after, before)
                self.assertEqual(
                    store.read_artifact_span(
                        group_id="legacy-group", artifact_id=legacy_id,
                        source_kind="event_log", start_byte=0, end_byte=6, max_bytes=7,

                        expected_artifact_commitment=store.artifact_commitment("legacy-group", legacy_id),)["content"],
                    b"legacy",
                )
                self.assertEqual(
                    store.read_artifact_lines(
                        group_id="streamed-group", artifact_id=streamed_id,
                        source_kind="event_log", start_line=0, line_count=1, max_bytes=16,

                        expected_artifact_commitment=store.artifact_commitment("streamed-group", streamed_id),)[0]["content"],
                    b"streamed\n",
                )

    def test_coordinated_stream_rewrite_cannot_rebind_original_artifact_identity(self):
        with EvidenceStore(":memory:") as store:
            artifact = store.add_streamed_group(
                "group-a", [{
                    "reader": io.BytesIO(b"abcdefgh"), "source_kind": "event_log",
                    "logical_path": "events.txt",
                    "origin": {"type": "file", "path": "/events.txt"},
                }], shared_byte_budget=8, chunk_size=4,
            )[0]
            artifact_id = artifact["artifactId"]
            replacements = (b"ABCD", b"EFGH")
            for index, replacement in enumerate(replacements):
                store.connection.execute(
                    "UPDATE artifact_chunks SET content=?, sha256=? "
                    "WHERE artifact_id=? AND chunk_index=?",
                    (replacement, hashlib.sha256(replacement).hexdigest(), artifact_id, index),
                )
            rewritten_sha256 = hashlib.sha256(b"ABCDEFGH").hexdigest()
            store.connection.execute(
                "UPDATE artifacts SET sha256=? WHERE id=?",
                (rewritten_sha256, artifact_id),
            )
            store.connection.execute(
                "DELETE FROM artifact_integrity_nodes WHERE artifact_id=?", (artifact_id,)
            )
            integrity_rows = [0]
            chunk_count, chunk_root = store._build_integrity_tree(
                artifact_id, rewritten_sha256, "chunk", integrity_rows=integrity_rows,
                max_integrity_rows=100,
            )
            line_count, line_root = store._build_integrity_tree(
                artifact_id, rewritten_sha256, "line", integrity_rows=integrity_rows,
                max_integrity_rows=100,
            )
            rewritten_root = store._authenticated_root(
                artifact_id, rewritten_sha256, 8, chunk_count, line_count,
                chunk_root, line_root,
            )
            store.connection.execute(
                "UPDATE artifact_chunk_integrity SET sha256=?, chunk_root=?, line_root=?, "
                "authenticated_root=? WHERE artifact_id=?",
                (rewritten_sha256, chunk_root, line_root, rewritten_root, artifact_id),
            )
            store.connection.execute(
                "UPDATE artifact_commitments SET authenticated_root=? WHERE artifact_id=?",
                (rewritten_root, artifact_id),
            )
            recorder = _BlobReadRecorder()
            store.connection.row_factory = recorder.row_factory
            with self.assertRaisesRegex(EvidenceStoreError, "identity changed"):
                store.read_artifact_span(
                    group_id="group-a", artifact_id=artifact_id, source_kind="event_log",
                    start_byte=0, end_byte=2, max_bytes=2,

                    expected_artifact_commitment=store.artifact_commitment("group-a", artifact_id),)
            self.assertEqual(recorder.total_bytes, 0)

    def test_corrupt_stream_is_not_discarded_or_converted_on_inline_reingestion(self):
        content = b"same\nartifact\n"
        origin = {"type": "file", "path": "/events.txt"}
        with EvidenceStore(":memory:") as store:
            artifact_id = store.add_streamed_group(
                "group-a", [{
                    "reader": io.BytesIO(content), "source_kind": "event_log",
                    "logical_path": "events.txt", "origin": origin,
                }], shared_byte_budget=len(content), chunk_size=4,
            )[0]["artifactId"]
            store.connection.execute(
                "UPDATE artifact_chunks SET content=? WHERE artifact_id=? AND chunk_index=0",
                (b"FAIL", artifact_id),
            )
            before = self._snapshot_store(store)
            with self.assertRaisesRegex(EvidenceStoreError, "chunk integrity"):
                store.add_artifact(
                    content, source_kind="event_log", logical_path="events.txt", origin=origin
                )
            self.assertEqual(self._snapshot_store(store), before)

    def test_corrupt_legacy_is_not_discarded_on_stream_reingestion(self):
        content = b"same\nartifact\n"
        origin = {"type": "file", "path": "/events.txt"}
        with EvidenceStore(":memory:") as store:
            store.add_group("legacy-group")
            artifact_id = store.add_artifact(
                content, source_kind="event_log", logical_path="events.txt", origin=origin
            )
            store.attach_artifact("legacy-group", artifact_id)
            store.connection.execute(
                "UPDATE artifacts SET content=? WHERE id=?",
                (b"SAME\nartifact\n", artifact_id),
            )
            before = self._snapshot_store(store)
            with self.assertRaisesRegex(EvidenceStoreError, "content changed"):
                store.add_streamed_group(
                    "new-group", [{
                        "reader": io.BytesIO(content), "source_kind": "event_log",
                        "logical_path": "events.txt", "origin": origin,
                    }], shared_byte_budget=len(content), chunk_size=1,
                )
            self.assertEqual(self._snapshot_store(store), before)

    def test_oversized_span_and_line_ranges_fail_before_chunk_metadata_query(self):
        with EvidenceStore(":memory:") as store:
            artifact = store.add_streamed_group(
                "group-a", [{
                    "reader": io.BytesIO(b"abcdefgh"), "source_kind": "event_log",
                    "logical_path": "events.txt",
                    "origin": {"type": "file", "path": "/events.txt"},
                }], shared_byte_budget=8, chunk_size=1,
            )[0]
            statements: list[str] = []
            store.connection.set_trace_callback(statements.append)
            with self.assertRaisesRegex(EvidenceStoreError, "bounded read size"):
                store.read_artifact_span(
                    group_id="group-a", artifact_id=artifact["artifactId"],
                    source_kind="event_log", start_byte=0, end_byte=8, max_bytes=4,

                    expected_artifact_commitment=artifact["artifactCommitment"],)
            with self.assertRaisesRegex(EvidenceStoreError, "bounded read size"):
                store.read_artifact_lines(
                    group_id="group-a", artifact_id=artifact["artifactId"],
                    source_kind="event_log", start_line=0, line_count=1, max_bytes=4,

                    expected_artifact_commitment=artifact["artifactCommitment"],)
            store.connection.set_trace_callback(None)
            metadata_queries = [
                statement for statement in statements
                if "FROM artifact_chunks INDEXED BY" in statement
            ]
            self.assertEqual(metadata_queries, [])

    def test_tiny_chunks_and_large_line_requests_have_finite_read_row_limits(self):
        with EvidenceStore(":memory:") as store:
            artifact = store.add_streamed_group(
                "group-a", [{
                    "reader": io.BytesIO(b"abcdefgh"), "source_kind": "event_log",
                    "logical_path": "events.txt",
                    "origin": {"type": "file", "path": "/events.txt"},
                }], shared_byte_budget=8, chunk_size=1,
            )[0]
            with patch.object(EvidenceStore, "DEFAULT_MAX_READ_CHUNK_ROWS", 4):
                with self.assertRaisesRegex(EvidenceStoreError, "metadata row limit"):
                    store.read_artifact_span(
                        group_id="group-a", artifact_id=artifact["artifactId"],
                        source_kind="event_log", start_byte=0, end_byte=5, max_bytes=5,

                        expected_artifact_commitment=artifact["artifactCommitment"],)
            with patch.object(EvidenceStore, "DEFAULT_MAX_READ_LINE_ROWS", 4):
                with self.assertRaisesRegex(EvidenceStoreError, "bounded metadata"):
                    store.read_artifact_lines(
                        group_id="group-a", artifact_id=artifact["artifactId"],
                        source_kind="event_log", start_line=0, line_count=5, max_bytes=8,

                        expected_artifact_commitment=artifact["artifactCommitment"],)


    def test_v2_canonical_sql_rejects_nocase_group_authorization_columns(self):
        for column in ("group_id", "artifact_id"):
            with self.subTest(column=column), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / f"nocase-{column}.sqlite3"
                connection = sqlite3.connect(path)
                for statement in EvidenceStore._BASE_SCHEMA:
                    if statement.startswith("CREATE TABLE group_artifacts"):
                        statement = statement.replace(
                            f"{column} TEXT NOT NULL",
                            f"{column} TEXT COLLATE NOCASE NOT NULL",
                        )
                    connection.execute(statement)
                for statement in EvidenceStore._C2_SCHEMA:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version={EvidenceStore.C2_SCHEMA_VERSION}")
                connection.commit()
                connection.close()

                for read_only in (False, True):
                    with self.subTest(column=column, read_only=read_only):
                        with self.assertRaisesRegex(
                            EvidenceStoreError,
                            "canonical C2 schema SQL|legacy base index",
                        ):
                            if read_only:
                                EvidenceStore.open_read_only(path)
                            else:
                                EvidenceStore(path)

    def test_read_only_open_rejects_dangling_base_and_c2_foreign_keys(self):
        corruptions = (
            (
                "base",
                "INSERT INTO group_artifacts VALUES ('missing-group', 'missing-artifact')",
            ),
            ("c2", "INSERT INTO artifact_lines VALUES ('missing-artifact', 0, 0, 1)"),
        )
        for kind, statement in corruptions:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / f"dangling-{kind}.sqlite3"
                with EvidenceStore(path):
                    pass
                connection = sqlite3.connect(path)
                connection.execute("PRAGMA foreign_keys=OFF")
                connection.execute(statement)
                connection.commit()
                connection.close()

                with self.assertRaisesRegex(EvidenceStoreError, "foreign key check failed"):
                    EvidenceStore.open_read_only(path)

    def test_canonical_identity_is_equal_across_chunk_sizes_and_storage_orders(self):
        content = b"same canonical bytes\nwith another line\n"
        origin = {"type": "file", "path": "/events.txt"}
        expected_id = _legacy_artifact_id("event_log", "events.txt", origin, content)
        roots: list[str] = []
        for chunk_size in (3, 17):
            with self.subTest(chunk_size=chunk_size), EvidenceStore(":memory:") as store:
                artifact = store.add_streamed_group(
                    "group-a", [{
                        "reader": io.BytesIO(content), "source_kind": "event_log",
                        "logical_path": "events.txt", "origin": origin,
                    }], shared_byte_budget=len(content), chunk_size=chunk_size,
                )[0]
                self.assertEqual(artifact["artifactId"], expected_id)
                roots.append(store.connection.execute(
                    "SELECT authenticated_root FROM artifact_commitments WHERE artifact_id=?",
                    (expected_id,),
                ).fetchone()[0])
        self.assertNotEqual(roots[0], roots[1])

        for order in ("inline_first", "stream_first"):
            with self.subTest(order=order), EvidenceStore(":memory:") as store:
                store.add_group("group-a")
                if order == "inline_first":
                    first_id = store.add_artifact(
                        content, source_kind="event_log", logical_path="events.txt",
                        origin=origin,
                    )
                    store.attach_artifact("group-a", first_id)
                    second_id = store.add_streamed_group(
                        "group-a", [{
                            "reader": io.BytesIO(content), "source_kind": "event_log",
                            "logical_path": "events.txt", "origin": origin,
                        }], shared_byte_budget=len(content), chunk_size=5,
                    )[0]["artifactId"]
                else:
                    first_id = store.add_streamed_group(
                        "group-a", [{
                            "reader": io.BytesIO(content), "source_kind": "event_log",
                            "logical_path": "events.txt", "origin": origin,
                        }], shared_byte_budget=len(content), chunk_size=11,
                    )[0]["artifactId"]
                    second_id = store.add_artifact(
                        content, source_kind="event_log", logical_path="events.txt",
                        origin=origin,
                    )
                self.assertEqual((first_id, second_id), (expected_id, expected_id))
                self.assertEqual(store.connection.execute(
                    "SELECT count(*) FROM artifacts"
                ).fetchone()[0], 1)

    def test_large_inline_migration_is_atomic_bounded_and_preserves_references(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "large-inline.sqlite3"
            content = b"first\n" + (b"x" * (2 * 1024 * 1024 + 123)) + b"\nlast\n"
            origin = {"type": "file", "path": "/legacy/large-events.txt"}
            artifact_id = _legacy_artifact_id("event_log", "events.txt", origin, content)
            last_start = len(content) - len(b"last\n")
            span_id = _span_id("group-a", artifact_id, last_start, len(content))
            connection = sqlite3.connect(path)
            for statement in EvidenceStore._BASE_SCHEMA:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                (artifact_id, "event_log", "events.txt", json.dumps(origin, sort_keys=True),
                 hashlib.sha256(content).hexdigest(), len(content), content),
            )
            connection.execute("INSERT INTO anr_groups VALUES ('group-a')")
            connection.execute(
                "INSERT INTO group_artifacts VALUES ('group-a', ?)", (artifact_id,),
            )
            connection.execute(
                "INSERT INTO spans VALUES (?, 'group-a', ?, ?, ?)",
                (span_id, artifact_id, last_start, len(content)),
            )
            connection.commit()
            connection.close()

            with EvidenceStore(path) as store:
                self.assertEqual(
                    store.connection.execute("PRAGMA user_version").fetchone()[0],
                    EvidenceStore.C2_SCHEMA_VERSION,
                )
                self.assertEqual(store.connection.execute(
                    "SELECT content FROM artifacts WHERE id=?", (artifact_id,),
                ).fetchone()[0], EvidenceStore._LEGACY_CHUNKED_MARKER)
                self.assertIsNotNone(store.connection.execute(
                    "SELECT 1 FROM artifact_chunk_integrity WHERE artifact_id=?",
                    (artifact_id,),
                ).fetchone())
                recorder = _BlobReadRecorder()
                store.connection.row_factory = recorder.row_factory

                span = store.read_span_internal_integrity_only(
                    span_id, group_id="group-a", source_kind="event_log",
                    max_bytes=EvidenceStore._MIGRATION_CHUNK_SIZE,
                )
                self.assertEqual(span["artifactId"], artifact_id)
                self.assertEqual(span["content"], b"last\n")
                self.assertLessEqual(recorder.total_bytes, EvidenceStore._MIGRATION_CHUNK_SIZE)

                recorder.byte_counts.clear()
                lines = store.read_artifact_lines(
                    group_id="group-a", artifact_id=artifact_id,
                    source_kind="event_log", start_line=2, line_count=1,
                    max_bytes=EvidenceStore._MIGRATION_CHUNK_SIZE,

                            expected_artifact_commitment=store.artifact_commitment("group-a", artifact_id),)
                self.assertEqual(lines[0]["content"], b"last\n")
                self.assertLessEqual(recorder.total_bytes, EvidenceStore._MIGRATION_CHUNK_SIZE)

            with EvidenceStore.open_read_only(path) as store:
                self.assertEqual(store.read_artifact_span(
                    group_id="group-a", artifact_id=artifact_id, source_kind="event_log",
                    start_byte=last_start, end_byte=len(content),
                    max_bytes=EvidenceStore._MIGRATION_CHUNK_SIZE,

                                     expected_artifact_commitment=store.artifact_commitment("group-a", artifact_id),)["content"], b"last\n")

    def test_chunk_and_integrity_ingestion_limits_rollback_and_preserve_seed(self):
        for limit_kind in ("chunk", "integrity"):
            with self.subTest(limit_kind=limit_kind), EvidenceStore(":memory:") as store:
                store.add_group("seed")
                seed_id = store.add_artifact(
                    b"seed", source_kind="event_log", logical_path="seed.txt",
                    origin={"type": "file", "path": "/seed.txt"},
                )
                store.attach_artifact("seed", seed_id)
                before = self._snapshot_store(store)
                kwargs = {"max_chunk_rows": 3, "max_integrity_rows": 100}
                expected = "Chunk row"
                if limit_kind == "integrity":
                    kwargs = {"max_chunk_rows": 10, "max_integrity_rows": 5}
                    expected = "Integrity row"
                with self.assertRaisesRegex(EvidenceStoreBudgetExceeded, expected):
                    store.add_streamed_group(
                        "new-group", [{
                            "reader": io.BytesIO(b"abcd"), "source_kind": "event_log",
                            "logical_path": "events.txt",
                            "origin": {"type": "file", "path": "/events.txt"},
                        }], shared_byte_budget=4, chunk_size=1, **kwargs,
                    )
                self.assertEqual(self._snapshot_store(store), before)

    def test_legacy_internal_read_span_requires_finite_chunk_bound(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "legacy-span.sqlite3"
            content = bytes(range(256)) * 800
            artifact_id = _seed_legacy_database(path, [content])[0]
            connection = sqlite3.connect(path)
            connection.execute("INSERT INTO anr_groups VALUES ('group-a')")
            connection.execute("INSERT INTO group_artifacts VALUES ('group-a', ?)", (artifact_id,))
            span_id = _span_id("group-a", artifact_id, 10_000, 110_000)
            connection.execute(
                "INSERT INTO spans VALUES (?, 'group-a', ?, 10000, 110000)",
                (span_id, artifact_id),
            )
            connection.commit()
            connection.close()

            with EvidenceStore(path) as store:
                with self.assertRaisesRegex(EvidenceStoreError, "explicit finite read limit"):
                    store.read_span_internal_integrity_only(
                        span_id, group_id="group-a", source_kind="event_log",
                    )
                self.assertEqual(
                    store.read_span_internal_integrity_only(
                        span_id, group_id="group-a", source_kind="event_log",
                        max_bytes=200_000,
                    )["content"],
                    content[10_000:110_000],
                )
                with self.assertRaisesRegex(EvidenceStoreError, "chunk read budget"):
                    store.read_span_internal_integrity_only(
                        span_id, group_id="group-a", source_kind="event_log",
                        max_bytes=100_000,
                    )
                with patch.object(EvidenceStore, "DEFAULT_MAX_READ_CHUNK_ROWS", 1):
                    with self.assertRaisesRegex(EvidenceStoreError, "metadata row limit"):
                        store.read_span_internal_integrity_only(
                            span_id, group_id="group-a", source_kind="event_log",
                            max_bytes=200_000,
                        )

    def test_stream_dedup_inline_migration_uses_same_aggregate_row_budgets(self):
        content = b"a\nb\n"
        origin = {"type": "file", "path": "/legacy/events.txt"}
        cases = (
            ("chunks", {"max_chunk_rows": 1}),
            ("lines", {"max_line_index_rows": 2}),
            ("integrity", {"max_integrity_rows": 6}),
        )
        for kind, limits in cases:
            with self.subTest(kind=kind), EvidenceStore(":memory:") as store:
                store.add_group("seed")
                artifact_id = store.add_artifact(
                    content, source_kind="event_log", logical_path="events.txt", origin=origin,
                )
                store.attach_artifact("seed", artifact_id)
                for table in (
                    "artifact_commitments", "artifact_integrity_nodes", "artifact_lines",
                    "artifact_chunk_integrity", "artifact_chunks",
                ):
                    store.connection.execute(
                        f"DELETE FROM {table} WHERE artifact_id=?", (artifact_id,),
                    )
                store.connection.execute(
                    "UPDATE artifacts SET content=? WHERE id=?", (content, artifact_id),
                )
                before = self._snapshot_store(store)

                with self.assertRaisesRegex(EvidenceStoreBudgetExceeded, "limit"):
                    store.add_streamed_group(
                        "dedup-group", [{
                            "reader": io.BytesIO(content), "source_kind": "event_log",
                            "logical_path": "events.txt", "origin": origin,
                        }], shared_byte_budget=len(content), chunk_size=len(content), **limits,
                    )

                self.assertEqual(self._snapshot_store(store), before)

    def test_writable_open_auto_migration_enforces_aggregate_budgets_atomically(self):
        cases = (
            ("artifacts", {"max_migration_artifacts": 1}),
            ("bytes", {"max_migration_bytes": 3}),
            ("chunks", {"max_migration_chunk_rows": 1}),
            ("lines", {"max_migration_line_rows": 1}),
            ("integrity", {"max_migration_integrity_rows": 7}),
        )
        for kind, limits in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / f"legacy-{kind}.sqlite3"
                _seed_legacy_database(path, [b"a\n", b"b\n"])
                connection = sqlite3.connect(path)
                before = list(connection.iterdump())
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                connection.close()

                with self.assertRaisesRegex(EvidenceStoreBudgetExceeded, "limit"):
                    EvidenceStore(path, **limits)

                connection = sqlite3.connect(path)
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
                self.assertEqual(list(connection.iterdump()), before)
                self.assertEqual(
                    connection.execute(
                        "SELECT count(*) FROM sqlite_master WHERE name IN ("
                        "'artifact_chunks', 'artifact_chunk_integrity', 'artifact_lines', "
                        "'artifact_integrity_nodes', 'artifact_commitments')"
                    ).fetchone()[0],
                    0,
                )
                connection.close()

    def test_writable_open_auto_migration_traverses_artifact_ids_incrementally(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "legacy-incremental.sqlite3"
            artifact_ids = _seed_legacy_database(path, [b"first\n", b"second\n", b"third\n"])

            def guarded_connect(*args, **kwargs):
                kwargs["factory"] = _IncrementalMigrationConnection
                return _REAL_SQLITE_CONNECT(*args, **kwargs)

            with patch("anr_evidence.evidence_store.sqlite3.connect", side_effect=guarded_connect):
                with EvidenceStore(path) as store:
                    migrated = [row[0] for row in store.connection.execute(
                        "SELECT artifact_id FROM artifact_chunk_integrity ORDER BY artifact_id"
                    )]
            self.assertEqual(migrated, sorted(artifact_ids))

    def test_c2_reads_require_and_validate_external_artifact_commitment(self):
        with EvidenceStore(":memory:") as store:
            first = store.add_streamed_group(
                "group-a", [{
                    "reader": io.BytesIO(b"first\n"), "source_kind": "event_log",
                    "logical_path": "first.txt",
                    "origin": {"type": "file", "path": "/first.txt"},
                }], shared_byte_budget=6, chunk_size=3,
            )[0]
            second = store.add_streamed_group(
                "group-a", [{
                    "reader": io.BytesIO(b"other\n"), "source_kind": "event_log",
                    "logical_path": "other.txt",
                    "origin": {"type": "file", "path": "/other.txt"},
                }], shared_byte_budget=6, chunk_size=3,
            )[0]
            valid = first["artifactCommitment"]
            value = store.read_artifact_span(
                group_id="group-a", artifact_id=first["artifactId"],
                source_kind="event_log", start_byte=0, end_byte=5, max_bytes=6,
                expected_artifact_commitment=valid,
            )
            self.assertEqual(value["content"], b"first")
            self.assertEqual(value["artifactCommitment"], valid)

            recorder = _BlobReadRecorder()
            store.connection.row_factory = recorder.row_factory
            with self.assertRaises(TypeError):
                store.read_artifact_span(
                    group_id="group-a", artifact_id=first["artifactId"],
                    source_kind="event_log", start_byte=0, end_byte=1, max_bytes=1,
                )
            with self.assertRaises(TypeError):
                store.read_artifact_lines(
                    group_id="group-a", artifact_id=first["artifactId"],
                    source_kind="event_log", start_line=0, line_count=1, max_bytes=6,
                )
            invalid = (
                None,
                {"scheme": "unknown", "root": valid["root"]},
                {"scheme": valid["scheme"], "root": "A" * 64},
                {"scheme": valid["scheme"], "root": "0" * 64},
                {**valid, "extra": "not-allowed"},
                second["artifactCommitment"],
            )
            for commitment in invalid:
                with self.subTest(commitment=commitment), self.assertRaises(EvidenceStoreError):
                    store.read_artifact_span(
                        group_id="group-a", artifact_id=first["artifactId"],
                        source_kind="event_log", start_byte=0, end_byte=1, max_bytes=1,
                        expected_artifact_commitment=commitment,
                    )
            self.assertEqual(recorder.total_bytes, 0)

    def test_external_commitment_rejects_coherent_sqlite_rewrite_for_span_and_lines(self):
        with EvidenceStore(":memory:") as store:
            artifact = store.add_streamed_group(
                "group-a", [{
                    "reader": io.BytesIO(b"aa\nbb\n"), "source_kind": "event_log",
                    "logical_path": "events.txt",
                    "origin": {"type": "file", "path": "/events.txt"},
                }], shared_byte_budget=6, chunk_size=3,
            )[0]
            artifact_id = artifact["artifactId"]
            original_commitment = dict(artifact["artifactCommitment"])
            span_id = store.add_span("group-a", artifact_id, 0, 2)
            replacements = (b"xx\n", b"yy\n")
            for index, replacement in enumerate(replacements):
                store.connection.execute(
                    "UPDATE artifact_chunks SET content=?, sha256=? "
                    "WHERE artifact_id=? AND chunk_index=?",
                    (replacement, hashlib.sha256(replacement).hexdigest(), artifact_id, index),
                )
            store.connection.execute(
                "DELETE FROM artifact_integrity_nodes WHERE artifact_id=?", (artifact_id,)
            )
            integrity_rows = [0]
            chunk_count, chunk_root = store._build_integrity_tree(
                artifact_id, artifact["sha256"], "chunk", integrity_rows=integrity_rows,
                max_integrity_rows=100,
            )
            line_count, line_root = store._build_integrity_tree(
                artifact_id, artifact["sha256"], "line", integrity_rows=integrity_rows,
                max_integrity_rows=100,
            )
            rewritten_root = store._authenticated_root(
                artifact_id, artifact["sha256"], artifact["byteCount"],
                chunk_count, line_count, chunk_root, line_root,
            )
            store.connection.execute(
                "UPDATE artifact_chunk_integrity SET chunk_root=?, line_root=?, "
                "authenticated_root=? WHERE artifact_id=?",
                (chunk_root, line_root, rewritten_root, artifact_id),
            )
            store.connection.execute(
                "UPDATE artifact_commitments SET authenticated_root=? WHERE artifact_id=?",
                (rewritten_root, artifact_id),
            )
            recorder = _BlobReadRecorder()
            store.connection.row_factory = recorder.row_factory
            with self.assertRaisesRegex(EvidenceStoreError, "commitment"):
                store.read_span(
                    span_id, group_id="group-a", source_kind="event_log", max_bytes=3,
                    expected_artifact_commitment=original_commitment,
                )
            with self.assertRaisesRegex(EvidenceStoreError, "commitment"):
                store.read_artifact_span(
                    group_id="group-a", artifact_id=artifact_id, source_kind="event_log",
                    start_byte=0, end_byte=2, max_bytes=3,
                    expected_artifact_commitment=original_commitment,
                )
            with self.assertRaisesRegex(EvidenceStoreError, "commitment"):
                store.read_artifact_lines(
                    group_id="group-a", artifact_id=artifact_id, source_kind="event_log",
                    start_line=0, line_count=1, max_bytes=3,
                    expected_artifact_commitment=original_commitment,
                )
            self.assertEqual(recorder.total_bytes, 0)

    def test_directory_consumer_rejects_missing_commitment_and_unknown_id_before_store_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            root = base / "input"
            root.mkdir()
            (root / "events.txt").write_bytes(b"event\n")
            path = base / "evidence.sqlite3"
            consumer = stream_directory_event_logs_to_store(
                root, path, group_id="group-a", shared_byte_budget=6, chunk_size=3,
            )
            self.addCleanup(consumer.close)
            descriptor = consumer.artifacts[0]
            with patch.object(consumer._store, "read_artifact_span") as read:
                with self.assertRaisesRegex(EvidenceStoreError, "Unknown streamed artifact"):
                    consumer.read_span("artifact-unknown", 0, 1)
                read.assert_not_called()
            missing = dict(descriptor)
            missing.pop("artifactCommitment")
            with self.assertRaisesRegex(EvidenceStoreError, "commitment"):
                DirectoryEventLogConsumer(
                    store_path=path, group_id="group-a", artifacts=(missing,), max_read_bytes=8,
                )

    def test_same_call_new_artifact_duplicate_succeeds_at_exact_build_budget(self):
        content = b"a\n"
        origin = {"type": "file", "path": "/same-call.txt"}
        specs = [{
            "reader": io.BytesIO(content), "source_kind": "event_log",
            "logical_path": "same-call.txt", "origin": origin,
        } for _ in range(2)]
        with EvidenceStore(":memory:") as store:
            completed = store.add_streamed_group(
                "group-a", specs, shared_byte_budget=len(content) * 2, chunk_size=2,
                max_chunk_rows=2, max_line_index_rows=2, max_integrity_rows=8,
            )
            self.assertEqual(completed[0]["artifactId"], completed[1]["artifactId"])
            self.assertEqual(
                store.connection.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0],
                1,
            )

    def test_stream_dedup_validation_budgets_are_aggregate_and_cached_by_target(self):
        content = b"a\nb\n"
        origin = {"type": "file", "path": "/same.txt"}
        with EvidenceStore(":memory:") as store:
            store.add_streamed_group(
                "seed", [{"reader": io.BytesIO(content), "source_kind": "event_log",
                          "logical_path": "same.txt", "origin": origin}],
                shared_byte_budget=len(content), chunk_size=4,
            )
            specs = [
                {"reader": io.BytesIO(content), "source_kind": "event_log",
                 "logical_path": "same.txt", "origin": origin}
                for _ in range(2)
            ]
            completed = store.add_streamed_group(
                "group-a", specs, shared_byte_budget=len(content) * 2, chunk_size=4,
                max_chunk_rows=3, max_line_index_rows=6, max_integrity_rows=14,
            )
            self.assertEqual(completed[0]["artifactId"], completed[1]["artifactId"])

        with EvidenceStore(":memory:") as store:
            store.add_streamed_group(
                "seed", [{"reader": io.BytesIO(content), "source_kind": "event_log",
                          "logical_path": "same.txt", "origin": origin}],
                shared_byte_budget=len(content), chunk_size=4,
            )
            with self.assertRaises(EvidenceStoreBudgetExceeded):
                store.add_streamed_group(
                    "group-a", [{"reader": io.BytesIO(content), "source_kind": "event_log",
                                 "logical_path": "same.txt", "origin": origin}],
                    shared_byte_budget=len(content), chunk_size=4,
                    max_chunk_rows=1, max_line_index_rows=4, max_integrity_rows=10,
                )

        with EvidenceStore(":memory:") as store:
            for name in ("one.txt", "two.txt"):
                store.add_streamed_group(
                    "seed", [{"reader": io.BytesIO(b"x\n"),
                              "source_kind": "event_log", "logical_path": name,
                              "origin": {"type": "file", "path": f"/{name}"}}],
                    shared_byte_budget=2, chunk_size=2,
                )
            duplicates = [
                {"reader": io.BytesIO(b"x\n"), "source_kind": "event_log",
                 "logical_path": name, "origin": {"type": "file", "path": f"/{name}"}}
                for name in ("one.txt", "two.txt")
            ]
            with self.assertRaises(EvidenceStoreBudgetExceeded):
                store.add_streamed_group(
                    "group-a", duplicates, shared_byte_budget=4, chunk_size=2,
                    max_chunk_rows=3, max_line_index_rows=4, max_integrity_rows=12,
                )

    def test_stream_dedup_target_migrated_in_operation_is_not_rescanned(self):
        content = b"a\n"
        origin = {"type": "file", "path": "/legacy.txt"}
        with EvidenceStore(":memory:") as store:
            artifact_id = store._artifact_id(
                "event_log", "legacy.txt", origin, hashlib.sha256(content).hexdigest()
            )

            def specs():
                store.connection.execute(
                    "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (artifact_id, "event_log", "legacy.txt", json.dumps(origin, sort_keys=True),
                     hashlib.sha256(content).hexdigest(), len(content), content),
                )
                yield {"reader": io.BytesIO(content), "source_kind": "event_log",
                       "logical_path": "legacy.txt", "origin": origin}

            completed = store.add_streamed_group(
                "group-a", specs(), shared_byte_budget=len(content), chunk_size=2,
                max_chunk_rows=2, max_line_index_rows=2, max_integrity_rows=6,
            )
            self.assertEqual(completed[0]["artifactId"], artifact_id)

    @staticmethod
    def _snapshot_store(store: EvidenceStore) -> dict[str, list[tuple]]:
        tables = (
            "artifacts", "artifact_chunks", "artifact_chunk_integrity",
            "artifact_lines", "artifact_integrity_nodes", "artifact_commitments",
            "anr_groups", "group_artifacts", "spans",
        )
        return {
            table: sorted(store.connection.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
            for table in tables
        }



if __name__ == "__main__":
    unittest.main()
