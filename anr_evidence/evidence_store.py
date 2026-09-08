"""Local immutable evidence snapshots and group-scoped byte references.

This first store is deliberately independent of filtered package strings.
Callers must supply original artifact bytes; streaming ingestion is C2 work.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


class EvidenceStoreError(ValueError):
    """An invalid, changed, or out-of-scope evidence reference."""


def _identity(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(",", ":"),
                                     sort_keys=True).encode("utf-8")).hexdigest()


class EvidenceStore:
    """Context-managed snapshot store. No network, shell, or model execution."""

    def __init__(self, path: str | Path):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS artifacts (
                id TEXT PRIMARY KEY,
                source_kind TEXT NOT NULL,
                logical_path TEXT NOT NULL,
                origin_json TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                byte_count INTEGER NOT NULL,
                content BLOB NOT NULL
            );
            CREATE TABLE IF NOT EXISTS anr_groups (id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS group_artifacts (
                group_id TEXT NOT NULL REFERENCES anr_groups(id),
                artifact_id TEXT NOT NULL REFERENCES artifacts(id),
                PRIMARY KEY (group_id, artifact_id)
            );
            CREATE TABLE IF NOT EXISTS spans (
                id TEXT PRIMARY KEY,
                group_id TEXT NOT NULL,
                artifact_id TEXT NOT NULL,
                start_byte INTEGER NOT NULL CHECK (start_byte >= 0),
                end_byte INTEGER NOT NULL CHECK (end_byte >= start_byte),
                FOREIGN KEY (group_id, artifact_id)
                    REFERENCES group_artifacts(group_id, artifact_id)
            );
        """)

    def __enter__(self) -> EvidenceStore:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def add_group(self, group_id: str) -> None:
        if not isinstance(group_id, str) or not group_id:
            raise EvidenceStoreError("A nonempty group ID is required")
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO anr_groups VALUES (?)", (group_id,))

    def add_artifact(self, content: bytes, *, source_kind: str, logical_path: str,
                     origin: dict[str, str]) -> str:
        """Store exact bytes without decoding, newline normalization or dedup by text.

        origin.type is 'file' or 'archive_member'. Archive origins must name both
        the container and member; the SHA here always hashes member bytes.
        """
        if not isinstance(content, bytes):
            raise EvidenceStoreError("Artifact content must be original bytes")
        if not isinstance(source_kind, str) or not source_kind or not isinstance(logical_path, str) or not logical_path:
            raise EvidenceStoreError("Source kind and logical path are required")
        if not isinstance(origin, dict) or origin.get("type") not in ("file", "archive_member"):
            raise EvidenceStoreError("Explicit file or archive_member origin required")
        required = {"type", "path"} if origin["type"] == "file" else {"type", "container", "member"}
        allowed = required | ({"memberOffset"} if origin["type"] == "archive_member" else set())
        if not required <= set(origin) <= allowed or any(not isinstance(value, str) or not value for value in origin.values()):
            raise EvidenceStoreError("Invalid artifact origin fields")
        digest = hashlib.sha256(content).hexdigest()
        origin_json = json.dumps(origin, ensure_ascii=False, sort_keys=True)
        artifact_id = "artifact-" + _identity(source_kind, logical_path, origin, digest)
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?)",
                                    (artifact_id, source_kind, logical_path, origin_json, digest, len(content), content))
        return artifact_id

    def attach_artifact(self, group_id: str, artifact_id: str) -> None:
        """Explicit attribution, never inferred from overlapping timestamps."""
        try:
            with self.connection:
                self.connection.execute("INSERT OR IGNORE INTO group_artifacts VALUES (?, ?)",
                                        (group_id, artifact_id))
        except sqlite3.IntegrityError as exc:
            raise EvidenceStoreError("Unknown group or artifact") from exc

    def _artifact(self, group_id: str, artifact_id: str):
        row = self.connection.execute(
            "SELECT a.source_kind, a.logical_path, a.origin_json, a.sha256, a.byte_count, a.content "
            "FROM artifacts a JOIN group_artifacts g ON g.artifact_id=a.id "
            "WHERE g.group_id=? AND a.id=?", (group_id, artifact_id)).fetchone()
        if row is None:
            raise EvidenceStoreError("Artifact does not belong to requested group")
        if len(row[5]) != row[4] or hashlib.sha256(row[5]).hexdigest() != row[3]:
            raise EvidenceStoreError("Stored artifact content changed")
        expected = "artifact-" + _identity(row[0], row[1], json.loads(row[2]), row[3])
        if artifact_id != expected:
            raise EvidenceStoreError("Stored artifact identity changed")
        return row

    def add_span(self, group_id: str, artifact_id: str, start_byte: int, end_byte: int) -> str:
        row = self._artifact(group_id, artifact_id)
        if (type(start_byte) is not int or type(end_byte) is not int
                or not 0 <= start_byte <= end_byte <= row[4]):
            raise EvidenceStoreError("Byte range outside original artifact")
        span_id = "span-" + _identity(group_id, artifact_id, start_byte, end_byte)
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO spans VALUES (?, ?, ?, ?, ?)",
                                    (span_id, group_id, artifact_id, start_byte, end_byte))
        return span_id

    def read_span(self, span_id: str, *, group_id: str, source_kind: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT group_id, artifact_id, start_byte, end_byte FROM spans WHERE id=?", (span_id,)).fetchone()
        if row is None or row[0] != group_id:
            raise EvidenceStoreError("Unknown or cross-group span")
        artifact = self._artifact(group_id, row[1])
        if artifact[0] != source_kind:
            raise EvidenceStoreError("Cross-source span")
        if (not 0 <= row[2] <= row[3] <= artifact[4]
                or span_id != "span-" + _identity(*row)):
            raise EvidenceStoreError("Stored span identity or range changed")
        return {
            "spanId": span_id, "groupId": group_id, "artifactId": row[1],
            "sourceKind": source_kind, "path": artifact[1], "origin": json.loads(artifact[2]),
            "sha256": artifact[3], "startByte": row[2], "endByte": row[3],
            "coordinateSpace": "original_artifact_bytes", "content": artifact[5][row[2]:row[3]],
        }
