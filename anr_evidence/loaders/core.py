"""Input loaders for fixtures, directories, archives, and mixed paths."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager, ExitStack
import bz2
import base64
import gzip
try:
    import fcntl
except ImportError:  # pragma: no cover - exercised via isolated import regression
    fcntl = None
import lzma
import os
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import stat
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import uuid
from typing import Any
from types import MappingProxyType
import zipfile

from ..am_anr import package_name_from_am_anr_line
from ..constants import OPTIONAL_SOURCE_KINDS, SOURCE_KINDS
from ..discovery import try_smart_monkey_discovery
from ..evidence_store import (
    EvidenceStore, EvidenceStoreBudgetExceeded, EvidenceStoreError,
)
from ..log_filter import parse_log_timestamp
from ..sources.shared.detection import detect_source_kind
from .package import build_package_from_entries


class ArchiveLoadError(ValueError):
    """Raised when an archive cannot be read or does not contain usable log sources."""


class ArchivePolicyError(ArchiveLoadError):
    """A safety rejection, which directory fallback must not silently ignore."""


# These limits apply to the uncompressed archive view.  Keeping them centralized
# preserves the public loader API and lets tests exercise each gate with tiny
# fixtures.
MAX_ARCHIVE_MEMBERS = 10_000
MAX_ARCHIVE_MEMBER_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_STREAM_DISCOVERY_ENTRIES = 100_000
MAX_STREAM_ARTIFACTS = 10_000
MAX_STREAM_LINE_INDEX_ROWS = 1_000_000


def load_package_from_fixture(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclass
class DirectoryEventLogConsumer:
    """Group-scoped bounded reader backed by one validated read-only store."""

    store_path: Path
    group_id: str
    artifacts: tuple[dict[str, Any], ...]
    max_read_bytes: int
    _prepared_store: EvidenceStore | None = field(default=None, repr=False, compare=False)
    _store: EvidenceStore = field(init=False, repr=False, compare=False)
    _commitments: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        commitments: dict[str, tuple[str, str]] = {}
        for descriptor in self.artifacts:
            artifact_id = descriptor.get("artifactId") if isinstance(descriptor, dict) else None
            if not isinstance(artifact_id, str) or not artifact_id or artifact_id in commitments:
                raise EvidenceStoreError("Streamed descriptors require unique artifact IDs")
            commitment = EvidenceStore.validate_artifact_commitment(
                descriptor.get("artifactCommitment")
            )
            commitments[artifact_id] = (commitment["scheme"], commitment["root"])
        self._commitments = MappingProxyType(commitments)
        self._store = self._prepared_store or EvidenceStore.open_read_only(self.store_path)
        self._prepared_store = None

    def _commitment_for(self, artifact_id: str) -> dict[str, str]:
        try:
            scheme, root = self._commitments[artifact_id]
        except (KeyError, TypeError) as exc:
            raise EvidenceStoreError("Unknown streamed artifact ID") from exc
        return {"scheme": scheme, "root": root}

    def close(self) -> None:
        self._store.close()

    def __del__(self) -> None:
        store = getattr(self, "_store", None)
        if store is not None:
            store.close()

    def __enter__(self) -> "DirectoryEventLogConsumer":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def read_span(self, artifact_id: str, start_byte: int, end_byte: int) -> dict[str, Any]:
        return self._store.read_artifact_span(
            group_id=self.group_id, artifact_id=artifact_id, source_kind="event_log",
            start_byte=start_byte, end_byte=end_byte, max_bytes=self.max_read_bytes,
            expected_artifact_commitment=self._commitment_for(artifact_id),
        )

    def read_lines(self, artifact_id: str, start_line: int, line_count: int) -> list[dict[str, Any]]:
        return self._store.read_artifact_lines(
            group_id=self.group_id, artifact_id=artifact_id, source_kind="event_log",
            start_line=start_line, line_count=line_count, max_bytes=self.max_read_bytes,
            expected_artifact_commitment=self._commitment_for(artifact_id),
        )


def stream_directory_event_logs_to_store(
    path: str | Path,
    store_path: str | Path,
    *,
    group_id: str,
    shared_byte_budget: int,
    chunk_size: int = 64 * 1024,
    max_read_bytes: int = 256 * 1024,
    max_discovery_entries: int = MAX_STREAM_DISCOVERY_ENTRIES,
    max_artifacts: int = MAX_STREAM_ARTIFACTS,
    max_line_index_rows: int = MAX_STREAM_LINE_INDEX_ROWS,
) -> DirectoryEventLogConsumer:
    """Stage, validate, and atomically publish descriptor-rooted EventLog shards."""
    root = Path(path)
    sqlite_path = Path(store_path)
    if str(sqlite_path) == ":memory:":
        raise EvidenceStoreError("Directory EventLog consumer requires a persistent SQLite path")
    for value, label, allow_zero in (
        (max_read_bytes, "Maximum consumer read size", False),
        (max_discovery_entries, "Maximum discovery entry count", True),
        (max_artifacts, "Maximum artifact count", True),
        (max_line_index_rows, "Maximum line-index row count", True),
    ):
        minimum = 0 if allow_zero else 1
        if type(value) is not int or value < minimum:
            qualifier = "nonnegative" if allow_zero else "positive"
            raise EvidenceStoreError(f"{label} must be a {qualifier} integer")

    required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
    if fcntl is None or any(not hasattr(os, name) for name in required_flags):
        raise EvidenceStoreError("Secure directory EventLog publication primitives are unavailable")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    create_file_flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    create_lock_flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    existing_lock_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC
        create_file_flags |= os.O_CLOEXEC
        create_lock_flags |= os.O_CLOEXEC
        existing_lock_flags |= os.O_CLOEXEC

    root_absolute = Path(os.path.abspath(os.fspath(root)))
    sqlite_path = Path(os.path.abspath(os.fspath(sqlite_path)))

    def is_within(candidate: Path, directory: Path) -> bool:
        try:
            return os.path.commonpath((os.fspath(candidate), os.fspath(directory))) == os.fspath(directory)
        except ValueError:
            return False

    if is_within(sqlite_path, root_absolute):
        raise EvidenceStoreError("Directory EventLog store must be outside the input root")
    try:
        resolved_root = root_absolute.resolve(strict=True)
        resolved_store = sqlite_path.resolve(strict=False)
    except OSError as exc:
        raise EvidenceStoreError("Directory EventLog paths could not be safely resolved") from exc
    if is_within(resolved_store, resolved_root):
        raise EvidenceStoreError("Directory EventLog store must be outside the input root")

    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    sqlite_name = sqlite_path.name
    lock_name = f".{sqlite_name}.lock"
    final_sidecar_names = tuple(f"{sqlite_name}{suffix}" for suffix in ("-journal", "-wal", "-shm"))
    root_descriptor = -1
    parent_descriptor = -1
    lock_descriptor = -1
    stage_descriptor = -1
    final_source_descriptor = -1
    staged_name: str | None = None
    staged_path: Path | None = None
    consumer: DirectoryEventLogConsumer | None = None
    published = False
    consumer_delivered = False
    publication_state_uncertain = False
    publication_reconciliation_failure: BaseException | None = None

    def descriptor_namespace_path(descriptor: int, name: str) -> Path:
        metadata = os.fstat(descriptor)
        identity = (metadata.st_dev, metadata.st_ino)
        proc_descriptor = Path("/proc/self/fd") / str(descriptor)
        try:
            candidate = os.stat(proc_descriptor)
        except OSError:
            candidate = None
        if (candidate is not None and stat.S_ISDIR(candidate.st_mode)
                and (candidate.st_dev, candidate.st_ino) == identity):
            return proc_descriptor / name
        if sys.platform == "darwin":
            try:
                raw_path = fcntl.fcntl(descriptor, 50, b"\0" * 1024)  # F_GETPATH
                parent_path = Path(raw_path.split(b"\0", 1)[0].decode())
                candidate = os.stat(parent_path, follow_symlinks=False)
            except (OSError, UnicodeError) as exc:
                raise EvidenceStoreError(
                    "Secure SQLite descriptor-derived path is unavailable"
                ) from exc
            if (not stat.S_ISDIR(candidate.st_mode)
                    or (candidate.st_dev, candidate.st_ino) != identity):
                raise EvidenceStoreError("SQLite output parent identity changed")
            return parent_path / name
        raise EvidenceStoreError("Secure SQLite descriptor namespace is unavailable")

    def descriptor_is_within(
        candidate_descriptor: int, ancestor_identity: tuple[int, int],
    ) -> bool:
        """Walk actual directory ancestry without trusting path resolution."""
        current_descriptor = os.dup(candidate_descriptor)
        try:
            while True:
                current_metadata = os.fstat(current_descriptor)
                current_identity = (current_metadata.st_dev, current_metadata.st_ino)
                if current_identity == ancestor_identity:
                    return True
                parent = os.open("..", directory_flags, dir_fd=current_descriptor)
                parent_metadata = os.fstat(parent)
                parent_identity = (parent_metadata.st_dev, parent_metadata.st_ino)
                if parent_identity == current_identity:
                    os.close(parent)
                    return False
                os.close(current_descriptor)
                current_descriptor = parent
        finally:
            os.close(current_descriptor)

    def relative_metadata(name: str) -> os.stat_result | None:
        try:
            return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def relative_exists(name: str) -> bool:
        return relative_metadata(name) is not None

    def reject_final_sidecars() -> None:
        if any(relative_exists(name) for name in final_sidecar_names):
            raise EvidenceStoreError("Published evidence store has live SQLite sidecars")

    def require_trusted_regular(metadata: os.stat_result, label: str) -> None:
        if not stat.S_ISREG(metadata.st_mode):
            raise EvidenceStoreError(f"{label} must be a regular file")
        if (metadata.st_uid != os.geteuid()
                or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
            raise EvidenceStoreError(
                f"{label} must be owned by the effective UID and not group/other writable"
            )

    def staged_sidecar_names(candidate: str) -> tuple[str, ...]:
        return tuple(f"{candidate}{suffix}" for suffix in ("-journal", "-wal", "-shm"))

    def remove_staging(candidate: str | None) -> list[BaseException]:
        failures: list[BaseException] = []
        if candidate is None or parent_descriptor < 0:
            return failures
        for item in (*staged_sidecar_names(candidate), candidate):
            try:
                os.unlink(item, dir_fd=parent_descriptor)
            except FileNotFoundError:
                continue
            except OSError as exc:
                failures.append(exc)
        return failures

    try:
        try:
            root_descriptor = os.open(root_absolute, directory_flags)
            root_metadata = os.fstat(root_descriptor)
        except OSError as exc:
            raise EvidenceStoreError("Directory EventLog streaming requires a safe directory") from exc
        if not stat.S_ISDIR(root_metadata.st_mode):
            raise EvidenceStoreError("Directory EventLog streaming requires a directory")
        root_identity = (root_metadata.st_dev, root_metadata.st_ino)

        try:
            parent_descriptor = os.open(sqlite_path.parent, directory_flags)
            parent_metadata = os.fstat(parent_descriptor)
        except OSError as exc:
            raise EvidenceStoreError("Directory EventLog output parent could not be pinned") from exc
        if not stat.S_ISDIR(parent_metadata.st_mode):
            raise EvidenceStoreError("Directory EventLog output parent must be a directory")
        if (parent_metadata.st_uid != os.geteuid()
                or parent_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
            raise EvidenceStoreError(
                "Directory EventLog output parent must be owned by the effective UID "
                "and not group/other writable"
            )
        parent_identity = (parent_metadata.st_dev, parent_metadata.st_ino)
        try:
            parent_is_within_root = descriptor_is_within(
                parent_descriptor, root_identity,
            )
        except OSError as exc:
            raise EvidenceStoreError(
                "Directory EventLog output ancestry could not be safely verified"
            ) from exc
        if parent_is_within_root:
            raise EvidenceStoreError("Directory EventLog store must be outside the input root")
        parent_namespace = descriptor_namespace_path(parent_descriptor, "")

        try:
            try:
                lock_descriptor = os.open(
                    lock_name, create_lock_flags, 0o600, dir_fd=parent_descriptor,
                )
                lock_metadata = os.fstat(lock_descriptor)
                require_trusted_regular(lock_metadata, "Directory EventLog publication lock")
            except FileExistsError:
                lock_namespace_metadata = relative_metadata(lock_name)
                if lock_namespace_metadata is None:
                    raise EvidenceStoreError(
                        "Directory EventLog publication lock identity changed"
                    )
                require_trusted_regular(
                    lock_namespace_metadata, "Directory EventLog publication lock",
                )
                lock_descriptor = os.open(
                    lock_name, existing_lock_flags, dir_fd=parent_descriptor,
                )
                lock_metadata = os.fstat(lock_descriptor)
                require_trusted_regular(lock_metadata, "Directory EventLog publication lock")
                current_lock_metadata = relative_metadata(lock_name)
                if (current_lock_metadata is None
                        or (current_lock_metadata.st_dev, current_lock_metadata.st_ino)
                        != (lock_metadata.st_dev, lock_metadata.st_ino)):
                    raise EvidenceStoreError(
                        "Directory EventLog publication lock identity changed"
                    )
                require_trusted_regular(
                    current_lock_metadata, "Directory EventLog publication lock",
                )
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        except EvidenceStoreError:
            raise
        except OSError as exc:
            raise EvidenceStoreError("Directory EventLog publication lock could not be acquired") from exc

        reject_final_sidecars()
        final_metadata = relative_metadata(sqlite_name)
        if final_metadata is not None:
            require_trusted_regular(final_metadata, "Published evidence store")
            try:
                final_source_descriptor = os.open(
                    sqlite_name, file_flags, dir_fd=parent_descriptor,
                )
            except OSError as exc:
                raise EvidenceStoreError(
                    "Published evidence store could not be safely opened"
                ) from exc
            opened_final_metadata = os.fstat(final_source_descriptor)
            require_trusted_regular(opened_final_metadata, "Published evidence store")
            if ((opened_final_metadata.st_dev, opened_final_metadata.st_ino)
                    != (final_metadata.st_dev, final_metadata.st_ino)):
                raise EvidenceStoreError("Published evidence store identity changed")

        for _ in range(128):
            candidate = f".{sqlite_name}.stage-{uuid.uuid4().hex}.sqlite3"
            try:
                stage_descriptor = os.open(
                    candidate, create_file_flags, 0o600, dir_fd=parent_descriptor,
                )
            except FileExistsError:
                continue
            staged_identity = os.fstat(stage_descriptor)
            if not stat.S_ISREG(staged_identity.st_mode):
                raise EvidenceStoreError("Staged evidence store must be a regular file")
            staged_name = candidate
            staged_path = parent_namespace / staged_name
            break
        else:
            raise EvidenceStoreError("Unique staged evidence store name could not be allocated")

        if final_metadata is not None:
            def revalidate_final_source() -> None:
                descriptor_metadata = os.fstat(final_source_descriptor)
                namespace_metadata = relative_metadata(sqlite_name)
                expected_identity = (final_metadata.st_dev, final_metadata.st_ino)
                require_trusted_regular(descriptor_metadata, "Published evidence store")
                if namespace_metadata is None:
                    raise EvidenceStoreError("Published evidence store identity changed")
                require_trusted_regular(namespace_metadata, "Published evidence store")
                if ((descriptor_metadata.st_dev, descriptor_metadata.st_ino)
                        != expected_identity
                        or (namespace_metadata.st_dev, namespace_metadata.st_ino)
                        != expected_identity):
                    raise EvidenceStoreError("Published evidence store identity changed")

            revalidate_final_source()
            source = sqlite3.connect(
                (parent_namespace / sqlite_name).as_uri() + "?mode=ro&immutable=1", uri=True,
            )
            destination = sqlite3.connect(staged_path)
            try:
                destination.execute("PRAGMA journal_mode=DELETE")
                revalidate_final_source()
                source.backup(destination)
                revalidate_final_source()
                destination.commit()
            finally:
                destination.close()
                source.close()

        def revalidate_output_namespace() -> None:
            current_parent = os.fstat(parent_descriptor)
            if (not stat.S_ISDIR(current_parent.st_mode)
                    or (current_parent.st_dev, current_parent.st_ino) != parent_identity):
                raise EvidenceStoreError("Directory EventLog output parent descriptor changed")
            held_stage = os.fstat(stage_descriptor)
            current_stage = relative_metadata(staged_name)
            expected_stage_identity = (staged_identity.st_dev, staged_identity.st_ino)
            if (not stat.S_ISREG(held_stage.st_mode)
                    or (held_stage.st_dev, held_stage.st_ino) != expected_stage_identity
                    or current_stage is None
                    or not stat.S_ISREG(current_stage.st_mode)
                    or (current_stage.st_dev, current_stage.st_ino)
                    != expected_stage_identity):
                raise EvidenceStoreError("Staged evidence store identity changed")

        def revalidate_root_identity() -> None:
            try:
                current = os.stat(root_absolute, follow_symlinks=False)
            except (FileNotFoundError, NotADirectoryError, OSError) as exc:
                raise EvidenceStoreError("Directory EventLog root changed after open") from exc
            if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != root_identity:
                raise EvidenceStoreError("Directory EventLog root changed after open")

        internal_names = {
            sqlite_name, lock_name, staged_name, *final_sidecar_names,
            *staged_sidecar_names(staged_name),
        }
        internal_identities: set[tuple[int, int]] = set()
        for internal in internal_names:
            metadata = relative_metadata(internal)
            if metadata is not None:
                internal_identities.add((metadata.st_dev, metadata.st_ino))

        def current_internal_identities() -> set[tuple[int, int]]:
            identities = set(internal_identities)
            for internal in internal_names:
                metadata = relative_metadata(internal)
                if metadata is not None:
                    identities.add((metadata.st_dev, metadata.st_ino))
            return identities

        def open_relative_directory(relative: Path) -> int:
            descriptor = os.dup(root_descriptor)
            try:
                for component in relative.parts:
                    next_descriptor = os.open(component, directory_flags, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = next_descriptor
                return descriptor
            except OSError as exc:
                os.close(descriptor)
                raise EvidenceStoreError(f"Directory changed after discovery: {relative}") from exc

        def open_relative_file(relative: Path) -> int:
            parent_descriptor = open_relative_directory(relative.parent)
            try:
                return os.open(relative.name, file_flags, dir_fd=parent_descriptor)
            except OSError as exc:
                raise EvidenceStoreError(
                    f"Directory EventLog shard could not be safely opened: {relative}"
                ) from exc
            finally:
                os.close(parent_descriptor)

        revalidate_root_identity()
        shards: list[tuple[Path, tuple[int, int]]] = []
        discovered = 0
        pending_directories: list[tuple[Path, tuple[int, int]]] = [
            (Path(), root_identity)
        ]
        while pending_directories:
            current, discovered_identity = pending_directories.pop()
            descriptor = open_relative_directory(current)
            try:
                opened_metadata = os.fstat(descriptor)
                if (opened_metadata.st_dev, opened_metadata.st_ino) != discovered_identity:
                    raise EvidenceStoreError(f"Directory changed after discovery: {current}")
                with os.scandir(descriptor) as entries:
                    for entry in entries:
                        discovered += 1
                        if discovered > max_discovery_entries:
                            raise EvidenceStoreBudgetExceeded(
                                f"Recursive discovery entry count exceeds limit {max_discovery_entries}"
                            )
                        relative = current / entry.name
                        try:
                            metadata = entry.stat(follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        identity = (metadata.st_dev, metadata.st_ino)
                        if stat.S_ISDIR(metadata.st_mode):
                            pending_directories.append((relative, identity))
                            continue
                        if (
                            not stat.S_ISREG(metadata.st_mode)
                            or is_archive_path(relative)
                            or identity in current_internal_identities()
                        ):
                            continue
                        if detect_source_kind(relative, "") != "event_log":
                            continue
                        if len(shards) >= max_artifacts:
                            raise EvidenceStoreBudgetExceeded(
                                f"Streamed artifact count exceeds limit {max_artifacts}"
                            )
                        shards.append((relative, identity))
            finally:
                os.close(descriptor)

        shards.sort(key=lambda item: str(item[0]))
        if not shards:
            raise EvidenceStoreError("No directory EventLog shards selected")

        def artifact_streams():
            for relative, discovered_identity in shards:
                descriptor = open_relative_file(relative)
                try:
                    opened_metadata = os.fstat(descriptor)
                    opened_identity = (opened_metadata.st_dev, opened_metadata.st_ino)
                    if (
                        not stat.S_ISREG(opened_metadata.st_mode)
                        or opened_identity != discovered_identity
                        or opened_identity in current_internal_identities()
                    ):
                        raise EvidenceStoreError(
                            f"Directory EventLog shard changed after discovery: {relative}"
                        )
                    reader = os.fdopen(descriptor, "rb")
                    descriptor = -1
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
                with reader:
                    yield {
                        "reader": reader,
                        "source_kind": "event_log",
                        "logical_path": str(relative),
                        "origin": {
                            "type": "directory_fd_snapshot",
                            "scheme": "anr-directory-fd-snapshot-v1",
                            "lexicalRoot": str(root_absolute),
                            "relativePath": str(relative),
                            "rootDevice": str(root_identity[0]),
                            "rootInode": str(root_identity[1]),
                            "fileDevice": str(opened_identity[0]),
                            "fileInode": str(opened_identity[1]),
                        },
                    }

        revalidate_output_namespace()
        with EvidenceStore(staged_path) as store:
            store.connection.execute("PRAGMA journal_mode=DELETE")
            try:
                store.connection.execute("BEGIN IMMEDIATE")
                artifacts = store.add_streamed_group(
                    group_id, artifact_streams(), shared_byte_budget=shared_byte_budget,
                    chunk_size=chunk_size, max_artifacts=max_artifacts,
                    max_line_index_rows=max_line_index_rows,
                )
                revalidate_root_identity()
                store.connection.commit()
            except BaseException:
                store.connection.rollback()
                raise

        if any(relative_exists(name) for name in staged_sidecar_names(staged_name)):
            raise EvidenceStoreError("Staged evidence store has live SQLite sidecars")
        revalidate_root_identity()
        revalidate_output_namespace()
        prepared_store = EvidenceStore.open_read_only(staged_path)
        try:
            consumer = DirectoryEventLogConsumer(
                store_path=sqlite_path, group_id=group_id, artifacts=tuple(artifacts),
                max_read_bytes=max_read_bytes, _prepared_store=prepared_store,
            )
        except BaseException as exc:
            try:
                prepared_store.close()
            except BaseException as close_exc:
                raise EvidenceStoreError(
                    f"Prepared evidence-store close failed: {close_exc}"
                ) from exc
            raise
        revalidate_root_identity()
        reject_final_sidecars()
        if any(relative_exists(name) for name in staged_sidecar_names(staged_name)):
            raise EvidenceStoreError("Staged evidence store has live SQLite sidecars")
        revalidate_output_namespace()

        try:
            os.replace(
                staged_name, sqlite_name,
                src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor,
            )
        except BaseException:
            try:
                held_stage = os.fstat(stage_descriptor)
                held_identity = (held_stage.st_dev, held_stage.st_ino)
                final_after_replace = relative_metadata(sqlite_name)
                stage_after_replace = relative_metadata(staged_name)
                if (stat.S_ISREG(held_stage.st_mode)
                        and final_after_replace is not None
                        and stat.S_ISREG(final_after_replace.st_mode)
                        and (final_after_replace.st_dev, final_after_replace.st_ino)
                        == held_identity
                        and stage_after_replace is None):
                    published = True
                elif (final_after_replace is None
                        or (final_after_replace.st_dev, final_after_replace.st_ino)
                        != held_identity):
                    published = False
                else:
                    publication_state_uncertain = True
                    publication_reconciliation_failure = EvidenceStoreError(
                        "Directory EventLog publication state could not be reconciled"
                    )
            except BaseException as exc:
                publication_state_uncertain = True
                publication_reconciliation_failure = exc
            raise
        published = True
        consumer_delivered = True
        return consumer
    finally:
        active_error = sys.exception()
        cleanup_failures: list[tuple[str, BaseException]] = []
        if publication_reconciliation_failure is not None:
            cleanup_failures.append((
                "publication reconciliation", publication_reconciliation_failure,
            ))
        if consumer is not None and not consumer_delivered:
            try:
                consumer.close()
            except BaseException as exc:
                cleanup_failures.append(("consumer close", exc))
        if not published and not publication_state_uncertain:
            if stage_descriptor >= 0:
                try:
                    os.ftruncate(stage_descriptor, 0)
                except OSError as exc:
                    cleanup_failures.append(("staging wipe truncate", exc))
                else:
                    try:
                        os.fsync(stage_descriptor)
                    except OSError as exc:
                        cleanup_failures.append(("staging wipe fsync", exc))
            for exc in remove_staging(staged_name):
                cleanup_failures.append(("staging unlink", exc))
        for label, descriptor in (
            ("existing output close", final_source_descriptor),
            ("staging close", stage_descriptor),
            ("publication lock close", lock_descriptor),
            ("output parent close", parent_descriptor),
            ("input root close", root_descriptor),
        ):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError as exc:
                    cleanup_failures.append((label, exc))
        if cleanup_failures:
            details = "; ".join(f"{label}: {exc}" for label, exc in cleanup_failures)
            cleanup_error = EvidenceStoreError(
                f"Directory EventLog cleanup failed ({details})"
            )
            if active_error is not None:
                raise cleanup_error from active_error
            raise cleanup_error


def load_package_from_directory(
    path: str | Path,
    package_name: str | None = None,
    event_anchor_dt: datetime | None = None,
    *,
    capture_originals: bool = False,
) -> dict[str, Any]:
    root = Path(path)
    if event_anchor_dt is None:
        event_anchor_dt = find_event_anr_timestamp_by_command(root, package_name)
    # Try smart Monkey-test discovery first (System_log/ directory with
    # System_MT_logcat* files). Falls back to full recursive traversal.
    smart_entries = None if capture_originals else try_smart_monkey_discovery(root, package_name=package_name)
    if smart_entries is not None:
        smart_package = build_package_from_entries(
            root.name,
            smart_entries,
            package_name=package_name,
            event_anchor_dt=event_anchor_dt,
        )
        if _has_core_anr_sources(smart_package):
            return smart_package
    # Original fallback: load everything recursively.
    entries = []
    for file_path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        original = {}
        if capture_originals:
            raw = file_path.read_bytes()
            try:
                content = raw.decode("utf-8")
                readable = True
            except UnicodeDecodeError:
                content = raw.decode("utf-8", errors="replace")
                readable = False
            # Keep the legacy text view's universal-newline behavior.
            content = content.replace("\r\n", "\n").replace("\r", "\n")
            original = {
                "originalBase64": base64.b64encode(raw).decode("ascii"),
                "origin": {"type": "file", "path": str(file_path.absolute())},
            }
        else:
            try:
                content = file_path.read_text(encoding="utf-8")
                readable = True
            except UnicodeDecodeError:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                readable = False
        entries.append(
            {
                **original,
                "path": str(file_path.relative_to(root)),
                "content": content,
                "readable": readable,
            }
        )
    return build_package_from_entries(root.name, entries, package_name=package_name, event_anchor_dt=event_anchor_dt)


def _load_loose_package_from_directory(
    path: Path,
    package_name: str | None = None,
    event_anchor_dt: datetime | None = None,
    *,
    capture_originals: bool = False,
) -> dict[str, Any] | None:
    """Load non-archive files that sit beside bugreport archives.

    Monkey/bugreport result directories often contain both a partial zip and
    richer loose files (event logs, System_MT_logcat shards, ``anr/`` traces,
    meminfo).  When archives are present the top-level loader cannot simply
    ignore those loose files, otherwise the generated AI context may fall back
    to a timeless or stale ``anr-unanchored`` group.
    """

    entries = []
    for file_path in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        if is_archive_path(file_path):
            continue
        original = {}
        if capture_originals:
            raw = file_path.read_bytes()
            try:
                content = raw.decode("utf-8")
                readable = True
            except UnicodeDecodeError:
                content = raw.decode("utf-8", errors="replace")
                readable = False
            # Keep the legacy text view's universal-newline behavior.
            content = content.replace("\r\n", "\n").replace("\r", "\n")
            original = {
                "originalBase64": base64.b64encode(raw).decode("ascii"),
                "origin": {"type": "file", "path": str(file_path.absolute())},
            }
        else:
            try:
                content = file_path.read_text(encoding="utf-8")
                readable = True
            except UnicodeDecodeError:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                readable = False
        entries.append(
            {
                **original,
                "path": str(file_path.relative_to(path)),
                "content": content,
                "readable": readable,
            }
        )
    if not entries:
        return None
    package = build_package_from_entries(path.name, entries, package_name=package_name, event_anchor_dt=event_anchor_dt)
    return package if package.get("sources") else None


def load_package_from_archive(path: str | Path, package_name: str | None = None, *,
                              capture_originals: bool = False) -> dict[str, Any]:
    archive_path = Path(path)
    suffixes = [suffix.lower() for suffix in archive_path.suffixes]
    entries: list[dict[str, Any]] = []
    if suffixes and suffixes[-1] == ".zip":
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                _validate_archive_member_count(archive_path, len(members))
                for member in members:
                    _validate_archive_member_name(archive_path, member.filename)
                    if _zip_member_is_link(member):
                        raise ArchivePolicyError(
                            f"Archive `{archive_path.name}` contains unsupported link member `{member.filename}`."
                        )
                files = [member for member in members if not member.is_dir()]
                _validate_archive_metadata_sizes(
                    archive_path,
                    ((member.filename, member.file_size) for member in files),
                )
                actual_total = 0
                for member in sorted(files, key=lambda item: item.filename):
                    original = {}
                    try:
                        with archive.open(member) as extracted:
                            content, readable, actual_size = _read_archive_member(
                                extracted,
                                member.filename,
                                max_bytes=min(MAX_ARCHIVE_MEMBER_BYTES, MAX_ARCHIVE_TOTAL_BYTES - actual_total),
                                original_out=original if capture_originals else None,
                            )
                    except OSError as exc:
                        raise ArchiveLoadError(f"Failed to read zip member `{member.filename}` from `{archive_path.name}`: {exc}") from exc
                    actual_total += actual_size
                    if capture_originals:
                        original["origin"] = {"type": "archive_member", "container": str(archive_path.absolute()),
                                              "member": member.filename, "memberOffset": str(member.header_offset)}
                    entries.append({"path": member.filename, "content": content, "readable": readable, **original})
        except zipfile.BadZipFile as exc:
            raise ArchiveLoadError(f"Archive `{archive_path.name}` is not a readable zip file or is corrupted.") from exc
    elif any(suffix in {".tar", ".gz", ".tgz", ".bz2", ".xz"} for suffix in suffixes):
        try:
            with _open_bounded_tar(archive_path) as archive:
                actual_total = 0
                declared_total = 0
                for count, member in enumerate(archive, 1):
                    _validate_archive_member_count(archive_path, count)
                    _validate_archive_member_name(archive_path, member.name)
                    if member.issym() or member.islnk():
                        raise ArchivePolicyError(
                            f"Archive `{archive_path.name}` contains unsupported link member `{member.name}`."
                        )
                    if not member.isfile() and not member.isdir():
                        raise ArchivePolicyError(
                            f"Archive `{archive_path.name}` contains unsupported special member `{member.name}`."
                        )
                    if not member.isfile():
                        continue
                    # Check before advancing to the next header: in compressed
                    # TARs, skipping an unchecked payload still decompresses it.
                    _validate_archive_metadata_sizes(archive_path, [(member.name, member.size)])
                    declared_total += member.size
                    _validate_archive_total_size(archive_path, declared_total)
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise ArchiveLoadError("Unable to read declared TAR file member")
                    original = {}
                    try:
                        with extracted:
                            content, readable, actual_size = _read_archive_member(
                                extracted,
                                member.name,
                                max_bytes=min(MAX_ARCHIVE_MEMBER_BYTES, MAX_ARCHIVE_TOTAL_BYTES - actual_total),
                                original_out=original if capture_originals else None,
                            )
                    except OSError as exc:
                        raise ArchiveLoadError(f"Failed to read tar member `{member.name}` from `{archive_path.name}`: {exc}") from exc
                    actual_total += actual_size
                    if capture_originals:
                        original["origin"] = {"type": "archive_member", "container": str(archive_path.absolute()),
                                              "member": member.name, "memberOffset": str(member.offset)}
                    entries.append({"path": member.name, "content": content, "readable": readable, **original})
                entries.sort(key=lambda entry: entry["path"])
        except (tarfile.TarError, OSError, EOFError, lzma.LZMAError) as exc:
            raise ArchiveLoadError(f"Archive `{archive_path.name}` is not a readable tar archive or is corrupted.") from exc
    else:
        raise ArchiveLoadError(f"Unsupported archive type: `{archive_path.name}`. Supported formats: .zip, .tar, .tar.gz, .tgz, .tar.bz2, .tar.xz.")
    package = build_package_from_entries(archive_path.stem, entries, package_name=package_name)
    if not package["sources"]:
        raise ArchiveLoadError(f"Archive `{archive_path.name}` was readable, but no recognizable ANR log sources were found.")
    return package


def try_load_smart_package(
    path: Path,
    package_name: str | None = None,
    event_anchor_dt: datetime | None = None,
) -> dict[str, Any] | None:
    """Try smart Monkey-test discovery; return a package dict or None."""
    entries = try_smart_monkey_discovery(path, package_name=package_name)
    if entries is None:
        return None
    if event_anchor_dt is None:
        event_anchor_dt = find_event_anr_timestamp_by_command(path, package_name)
    return build_package_from_entries(path.name, entries, package_name=package_name, event_anchor_dt=event_anchor_dt)


class _BoundedArchiveMemberReader:
    """Expose one archive member as a bounded binary stream for store ingestion."""

    def __init__(self, handle, *, archive_path: Path, member_name: str,
                 archive_kind: str, max_bytes: int):
        self._handle = handle
        self._archive_path = archive_path
        self._member_name = member_name
        self._archive_kind = archive_kind
        self._max_bytes = max_bytes
        self._remaining = max_bytes

    def read(self, size: int = -1) -> bytes:
        if type(size) is not int or size <= 0:
            raise EvidenceStoreError("Archive member stream requires a positive bounded read size")
        request_size = min(size, self._remaining + 1)
        try:
            data = self._handle.read(request_size)
        except OSError as exc:
            raise ArchiveLoadError(
                f"Failed to read {self._archive_kind} member `{self._member_name}` "
                f"from `{self._archive_path.name}`: {exc}"
            ) from exc
        if not isinstance(data, bytes):
            raise EvidenceStoreError("Archive member stream must return bytes")
        if len(data) > self._remaining:
            raise ArchivePolicyError(
                f"Archive member `{self._member_name}` actual data exceeds bounded-read "
                f"limit {self._max_bytes}."
            )
        self._remaining -= len(data)
        return data


def stream_archive_to_store(
    path: str | Path,
    store: EvidenceStore,
    *,
    group_id: str,
    shared_byte_budget: int,
    chunk_size: int = 64 * 1024,
    max_artifacts: int = MAX_STREAM_ARTIFACTS,
    max_line_index_rows: int = MAX_STREAM_LINE_INDEX_ROWS,
) -> list[dict[str, Any]]:
    """Stream one ZIP/TAR's original member bytes into an open evidence store.

    This opt-in path deliberately does not invoke the legacy text-package
    loader: each regular member is opened once, never Base64 encoded or joined
    into a full text view, and is committed as an ``archive_member`` artifact.
    Path-recognized members retain their detected source kind; all other
    regular files use ``archive_member`` so the external baseline is retained.

    ``shared_byte_budget`` is additionally capped by the archive-wide hard
    limit.  Metadata validation rejects declared policy violations before
    reading, while ``EvidenceStore.add_streamed_group`` and the bounded member
    reader enforce actual decompressed total and per-member limits in the same
    atomic store transaction.
    """
    if type(shared_byte_budget) is not int or shared_byte_budget < 0:
        raise EvidenceStoreError("Shared byte budget must be a nonnegative integer")
    archive_path = Path(path)
    effective_budget = min(shared_byte_budget, MAX_ARCHIVE_TOTAL_BYTES)

    def spec(handle, *, member_name: str, member_offset: int, archive_kind: str):
        return {
            "reader": _BoundedArchiveMemberReader(
                handle,
                archive_path=archive_path,
                member_name=member_name,
                archive_kind=archive_kind,
                max_bytes=MAX_ARCHIVE_MEMBER_BYTES,
            ),
            "source_kind": detect_source_kind(Path(member_name), "") or "archive_member",
            "logical_path": member_name,
            "origin": {
                "type": "archive_member",
                "container": str(archive_path.absolute()),
                "member": member_name,
                "memberOffset": str(member_offset),
            },
        }

    suffixes = [suffix.lower() for suffix in archive_path.suffixes]
    if suffixes and suffixes[-1] == ".zip":
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                _validate_archive_member_count(archive_path, len(members))
                for member in members:
                    _validate_archive_member_name(archive_path, member.filename)
                    if _zip_member_is_link(member):
                        raise ArchivePolicyError(
                            f"Archive `{archive_path.name}` contains unsupported link member "
                            f"`{member.filename}`."
                        )
                files = sorted((member for member in members if not member.is_dir()),
                               key=lambda member: member.filename)
                _validate_archive_metadata_sizes(
                    archive_path,
                    ((member.filename, member.file_size) for member in files),
                )
                if not files:
                    raise ArchiveLoadError(
                        f"Archive `{archive_path.name}` does not contain regular file members."
                    )

                def artifact_streams():
                    for member in files:
                        try:
                            with archive.open(member) as extracted:
                                yield spec(
                                    extracted,
                                    member_name=member.filename,
                                    member_offset=member.header_offset,
                                    archive_kind="zip",
                                )
                        except OSError as exc:
                            raise ArchiveLoadError(
                                f"Failed to open zip member `{member.filename}` from "
                                f"`{archive_path.name}`: {exc}"
                            ) from exc

                return store.add_streamed_group(
                    group_id, artifact_streams(), shared_byte_budget=effective_budget,
                    chunk_size=chunk_size, max_artifacts=max_artifacts,
                    max_line_index_rows=max_line_index_rows,
                )
        except zipfile.BadZipFile as exc:
            raise ArchiveLoadError(
                f"Archive `{archive_path.name}` is not a readable zip file or is corrupted."
            ) from exc

    if any(suffix in {".tar", ".gz", ".tgz", ".bz2", ".xz"} for suffix in suffixes):
        try:
            with _open_bounded_tar(archive_path) as archive:
                def artifact_streams():
                    declared_total = 0
                    found_file = False
                    for count, member in enumerate(archive, 1):
                        _validate_archive_member_count(archive_path, count)
                        _validate_archive_member_name(archive_path, member.name)
                        if member.issym() or member.islnk():
                            raise ArchivePolicyError(
                                f"Archive `{archive_path.name}` contains unsupported link member "
                                f"`{member.name}`."
                            )
                        if not member.isfile() and not member.isdir():
                            raise ArchivePolicyError(
                                f"Archive `{archive_path.name}` contains unsupported special member "
                                f"`{member.name}`."
                            )
                        if not member.isfile():
                            continue
                        _validate_archive_metadata_sizes(archive_path, [(member.name, member.size)])
                        declared_total += member.size
                        _validate_archive_total_size(archive_path, declared_total)
                        try:
                            extracted = archive.extractfile(member)
                        except OSError as exc:
                            raise ArchiveLoadError(
                                f"Failed to open tar member `{member.name}` from "
                                f"`{archive_path.name}`: {exc}"
                            ) from exc
                        if extracted is None:
                            raise ArchiveLoadError("Unable to read declared TAR file member")
                        found_file = True
                        with extracted:
                            yield spec(
                                extracted,
                                member_name=member.name,
                                member_offset=member.offset,
                                archive_kind="tar",
                            )
                    if not found_file:
                        raise ArchiveLoadError(
                            f"Archive `{archive_path.name}` does not contain regular file members."
                        )

                return store.add_streamed_group(
                    group_id, artifact_streams(), shared_byte_budget=effective_budget,
                    chunk_size=chunk_size, max_artifacts=max_artifacts,
                    max_line_index_rows=max_line_index_rows,
                )
        except (tarfile.TarError, OSError, EOFError, lzma.LZMAError) as exc:
            raise ArchiveLoadError(
                f"Archive `{archive_path.name}` is not a readable tar archive or is corrupted."
            ) from exc

    raise ArchiveLoadError(
        f"Unsupported archive type: `{archive_path.name}`. Supported formats: "
        ".zip, .tar, .tar.gz, .tgz, .tar.bz2, .tar.xz."
    )

def find_archives_in_directory(directory: Path) -> list[Path]:
    """Find archive files in a directory (non-recursive)."""
    archives: list[Path] = []
    for child in sorted(directory.iterdir()):
        if child.is_file() and is_archive_path(child):
            archives.append(child)
    return archives


def load_package_from_path(input_path: str | Path, package_name: str | None = None, *,
                           capture_originals: bool = False) -> dict[str, Any]:
    """Universal loader that auto-detects the input type.

    Handles:
    - A JSON fixture file → loads directly
    - An archive (.zip/.tar/.tar.gz/.tgz/.tar.bz2/.tar.xz) → extracts and loads
    - A directory containing extracted bugreport files → loads directory
    - A directory containing one or more archives → auto-extracts all, merges
    """
    path = Path(input_path)
    if not path.exists():
        raise ArchiveLoadError(f"Path does not exist: `{path}`")

    if path.is_file():
        if path.suffix.lower() == ".json":
            return load_package_from_fixture(path)
        if is_archive_path(path):
            return load_package_from_archive(path, package_name=package_name, capture_originals=capture_originals)
        raise ArchiveLoadError(f"Unsupported file type: `{path.name}`")

    if path.is_dir():
        archives = find_archives_in_directory(path)
        event_anchor_dt = find_event_anr_timestamp_by_command(path, package_name)
        # Try smart Monkey-test discovery regardless of whether archives
        # exist — the directory may contain System_log/ files that are more
        # complete than what's in the archive (especially for Monkey test
        # results where the zip only contains a partial bugreport snapshot).
        smart_package = None if capture_originals else try_load_smart_package(path, package_name=package_name, event_anchor_dt=event_anchor_dt)
        if smart_package is not None and _has_core_anr_sources(smart_package):
            return smart_package
        if not archives:
            if smart_package is not None:
                return smart_package
            # No archives — load directory directly
            return load_package_from_directory(path, package_name=package_name, event_anchor_dt=event_anchor_dt, capture_originals=capture_originals)

        # Extract all archives found and merge into a single package.
        all_entries: list[dict[str, Any]] = []
        for archive_path in archives:
            try:
                package = load_package_from_archive(archive_path, package_name=package_name, capture_originals=capture_originals)
            except ArchivePolicyError:
                raise
            except ArchiveLoadError:
                continue
            sources = package.get("sources", {})
            for source_kind in SOURCE_KINDS + OPTIONAL_SOURCE_KINDS:
                src = sources.get(source_kind)
                if src and src.get("content"):
                    all_entries.append({
                        "path": f"{archive_path.name}/{src.get('path', source_kind)}",
                        "content": src["content"],
                        "readable": src.get("readable", True),
                        "originalArtifacts": src.get("originalArtifacts", []),
                    })

        if not all_entries and smart_package is not None:
            return smart_package
        if not all_entries:
            # Fallback: try loading directory directly
            return load_package_from_directory(path, package_name=package_name, event_anchor_dt=event_anchor_dt, capture_originals=capture_originals)

        archive_pkg = build_package_from_entries(
            path.name,
            all_entries,
            package_name=package_name,
            event_anchor_dt=event_anchor_dt,
        )
        loose_package = _load_loose_package_from_directory(path, package_name=package_name, event_anchor_dt=event_anchor_dt, capture_originals=capture_originals)
        if smart_package is None:
            if loose_package is None:
                return archive_pkg
            smart_package = loose_package
        # Merge: use smart/loose discovery to fill in sources that are missing
        # or appear stale in the archive package. Prefer time-aligned loose or
        # smart trace evidence when an EventLog am_anr anchor was found; this
        # keeps ANR1-style loose ``anr/anr_YYYY...`` traces from being shadowed
        # by a partial archive containing only later traces.
        merged_sources = dict(archive_pkg.get("sources", {}))
        overlay_packages = [pkg for pkg in (loose_package, smart_package) if pkg is not None]
        if smart_package is loose_package:
            overlay_packages = [loose_package]
        for source_kind in SOURCE_KINDS + OPTIONAL_SOURCE_KINDS:
            for overlay_pkg in overlay_packages:
                overlay_src = (overlay_pkg.get("sources", {}) or {}).get(source_kind)
                if overlay_src is None or not overlay_src.get("content"):
                    continue
                archive_src = merged_sources.get(source_kind)
                if archive_src is None or not archive_src.get("content"):
                    merged_sources[source_kind] = overlay_src
                elif source_kind in ("event_log", "logcat", "meminfo"):
                    # For log/memory data, prefer time-aligned loose/smart
                    # files over stale archive snapshots.
                    merged_sources[source_kind] = overlay_src
                elif source_kind == "trace" and event_anchor_dt is not None:
                    merged_sources[source_kind] = overlay_src
        return {
            "package_id": archive_pkg.get("package_id", path.name),
            "provided_type": archive_pkg.get("provided_type"),
            "sources": merged_sources,
        }

    raise ArchiveLoadError(f"Unsupported input type: `{path}`")


def is_archive_path(path: Path) -> bool:
    suffixes = [suffix.lower() for suffix in path.suffixes]
    if not suffixes:
        return False
    if suffixes[-1] == ".zip":
        return True
    return any(suffix in {".tar", ".gz", ".tgz", ".bz2", ".xz"} for suffix in suffixes)


def _has_core_anr_sources(package: dict[str, Any]) -> bool:
    """Return True when a package has enough evidence to build ANR context.

    Kernel logs are valuable but not required by the AI-context completeness
    gate.  Treating trace/EventLog/logcat as the core set lets Monkey result
    directories with complete loose evidence avoid loading duplicate archive
    snapshots that are often much larger and staler.
    """

    sources = package.get("sources", {}) or {}
    return all(sources.get(kind, {}).get("content") for kind in ("trace", "event_log", "logcat"))


def find_event_anr_timestamp_by_command(root: Path, package_name: str | None) -> datetime | None:
    """Return the first usable ``am_anr`` timestamp using fg/rg/grep.

    This is an optional directory fast path used before creating
    ``anr_ai_context``.  When *package_name* is provided, only matching
    ``am_anr`` lines are considered.  Without a package filter, the first
    timestamped ``am_anr`` line with an identifiable package/process is used
    as the EventLog anchor.  Command
    failures deliberately fall through to the next command so the caller can
    still fall back to the in-memory Python scanner.
    """

    for command in _event_anr_search_commands(root):
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue

        # grep-style tools return 1 for no matches.  Other non-zero statuses
        # usually mean unsupported flags or I/O errors; try the next command.
        if completed.returncode not in (0, 1):
            continue

        timestamp = _event_anr_timestamp_from_command_output(completed.stdout.splitlines(), package_name)
        if timestamp is not None:
            return timestamp
    return None


def _event_anr_timestamp_from_command_output(lines: Iterable[str], package_name: str | None) -> datetime | None:
    timestamps: list[datetime] = []
    for line in lines:
        if "am_anr" not in line.lower():
            continue
        if package_name:
            if package_name not in line:
                continue
        elif package_name_from_am_anr_line(line) is None:
            continue
        timestamp = parse_log_timestamp(line)
        if timestamp is not None:
            timestamps.append(timestamp)
    return min(timestamps) if timestamps else None


def _event_anr_search_commands(root: Path) -> list[list[str]]:
    commands: list[list[str]] = []

    fg = shutil.which("fg")
    if fg:
        commands.append([fg, "-n", "--fixed-strings", "am_anr", str(root)])

    rg = shutil.which("rg")
    if rg:
        commands.append([rg, "-n", "--fixed-strings", "am_anr", str(root)])

    grep = shutil.which("grep")
    if grep:
        commands.append([grep, "-R", "-n", "-F", "-I", "am_anr", str(root)])

    return commands


def _event_anr_search_command(root: Path) -> list[str] | None:
    """Backward-compatible single-command helper for older tests/callers."""

    commands = _event_anr_search_commands(root)
    return commands[0] if commands else None


class _BoundedTarStream:
    """Limit decompressed bytes including PAX/GNU headers, not only file data."""

    def __init__(self, source, limit: int):
        self.source = source
        self.remaining = limit

    def read(self, size: int) -> bytes:
        data = self.source.read(min(size, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ArchivePolicyError("TAR decompressed stream exceeds byte budget (including metadata)")
        return data


@contextmanager
def _open_bounded_tar(path: Path):
    with ExitStack() as stack:
        raw = stack.enter_context(path.open("rb"))
        magic = raw.read(6)
        raw.seek(0)
        if magic.startswith(b"\x1f\x8b"):
            source = stack.enter_context(gzip.GzipFile(fileobj=raw))
        elif magic.startswith(b"BZh"):
            source = stack.enter_context(bz2.BZ2File(raw))
        elif magic.startswith(b"\xfd7zXZ\x00"):
            source = stack.enter_context(lzma.LZMAFile(raw))
        else:
            source = raw
        # Allow normal headers/padding in addition to the declared data budget.
        # Oversized extension metadata is rejected even before TarInfo exists.
        limit = MAX_ARCHIVE_TOTAL_BYTES + MAX_ARCHIVE_MEMBERS * 1024 + 10240
        bounded = _BoundedTarStream(source, limit)
        archive = stack.enter_context(tarfile.open(fileobj=bounded, mode="r|"))
        yield archive


def _validate_archive_member_count(archive_path: Path, count: int) -> None:
    if count > MAX_ARCHIVE_MEMBERS:
        raise ArchivePolicyError(
            f"Archive `{archive_path.name}` member count {count} exceeds limit {MAX_ARCHIVE_MEMBERS}."
        )


def _validate_archive_member_name(archive_path: Path, name: str) -> None:
    normalized = name.replace("\\", "/")
    parts = normalized.split("/")
    has_drive_prefix = bool(parts and len(parts[0]) >= 2 and parts[0][1] == ":")
    if (
        not normalized
        or "\x00" in normalized
        or normalized.startswith("/")
        or has_drive_prefix
        or any(part == ".." for part in parts)
    ):
        raise ArchivePolicyError(
            f"Archive `{archive_path.name}` contains unsafe member name `{name}`."
        )


def _zip_member_is_link(member: zipfile.ZipInfo) -> bool:
    if member.create_system != 3:
        return False
    return stat.S_IFMT(member.external_attr >> 16) == stat.S_IFLNK


def _validate_archive_metadata_sizes(archive_path: Path, members: Iterable[tuple[str, int]]) -> None:
    total = 0
    for name, size in members:
        if size < 0:
            raise ArchivePolicyError(
                f"Archive `{archive_path.name}` member `{name}` has invalid negative uncompressed size {size}."
            )
        if size > MAX_ARCHIVE_MEMBER_BYTES:
            raise ArchivePolicyError(
                f"Archive `{archive_path.name}` member `{name}` is {size} bytes uncompressed, "
                f"exceeding per-member limit {MAX_ARCHIVE_MEMBER_BYTES}."
            )
        total += size
        _validate_archive_total_size(archive_path, total)


def _validate_archive_total_size(archive_path: Path, total: int) -> None:
    if total > MAX_ARCHIVE_TOTAL_BYTES:
        raise ArchivePolicyError(
            f"Archive `{archive_path.name}` total uncompressed size {total} bytes exceeds limit "
            f"{MAX_ARCHIVE_TOTAL_BYTES}."
        )


def _read_archive_member(handle, name: str, *, max_bytes: int,
                         original_out: dict[str, Any] | None = None) -> tuple[str, bool, int]:
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        chunk = handle.read(min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > max_bytes:
        raise ArchivePolicyError(
            f"Archive member `{name}` actual data exceeds bounded-read limit {max_bytes}."
        )
    if original_out is not None:
        original_out["originalBase64"] = base64.b64encode(data).decode("ascii")
    try:
        return data.decode("utf-8"), True, len(data)
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), False, len(data)
