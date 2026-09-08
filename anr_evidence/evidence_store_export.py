"""Opt-in bridge from captured original bytes to grouped artifact references."""
from __future__ import annotations

import base64
import os
from pathlib import Path
import tempfile
from typing import Any

from .evidence_store import EvidenceStore


_SECTION_SOURCES = {
    "trace": "trace", "eventLog": "event_log", "logcat": "logcat",
    "anrManager": "logcat", "meminfo": "meminfo",
}


def _remove_sqlite_sidecars(path: Path) -> None:
    """Remove SQLite journals belonging to a snapshot being replaced."""

    for suffix in ("-journal", "-shm", "-wal"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()


def export_evidence_store(
    package: dict[str, Any],
    groups: list[dict[str, Any]],
    out_dir: Path,
    *,
    published_path: Path | None = None,
) -> dict[str, Any]:
    """Snapshot supplied originals, never reconstruct original bytes from text.

    A span is resolved only when the exact displayed line has one original
    occurrence. Ambiguous, transformed, missing and undecodable lines remain
    unresolved; no baseline is removed. Store bytes are local sensitive data.
    """
    fd, temporary = tempfile.mkstemp(prefix=".evidence-", suffix=".sqlite3", dir=out_dir)
    os.close(fd)
    path = out_dir / "evidence.sqlite3"
    refs: dict[str, list[dict[str, Any]]] = {}
    try:
        with EvidenceStore(temporary) as store:
            line_locations: dict[tuple[str, str], set[tuple[str, int, int]]] = {}
            for kind, source in package.get("sources", {}).items():
                for captured in source.get("originalArtifacts", []):
                    raw = base64.b64decode(captured["originalBase64"], validate=True)
                    artifact_id = store.add_artifact(raw, source_kind=kind, logical_path=captured["path"],
                                                     origin=captured["origin"])
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    offset = 0
                    for line in text.splitlines(keepends=True):
                        size = len(line.encode("utf-8"))
                        displayed = line.rstrip("\r\n")
                        line_locations.setdefault((kind, displayed), set()).add((artifact_id, offset, offset + size))
                        offset += size
            seen_groups = set()
            for group in groups:
                group_id = group["id"]
                if group_id in seen_groups:
                    raise ValueError("Cannot export duplicate ANR group IDs")
                seen_groups.add(group_id)
                store.add_group(group_id)
                group_refs = []
                for section, kind in _SECTION_SOURCES.items():
                    for index, line in enumerate(group.get(section, {}).get("lines", [])):
                        candidates = line_locations.get((kind, line), set())
                        ref = {"section": section, "sourceKind": kind, "viewLineIndex": index,
                               "status": "unresolved", "reason": "original_line_unavailable"}
                        if len(candidates) > 1:
                            ref["reason"] = "ambiguous_original_occurrence"
                        elif len(candidates) == 1:
                            artifact_id, start, end = next(iter(candidates))
                            store.attach_artifact(group_id, artifact_id)
                            span_id = store.add_span(group_id, artifact_id, start, end)
                            ref = {**ref, "status": "resolved", "reason": None, "spanId": span_id,
                                   "artifactId": artifact_id, "startByte": start, "endByte": end,
                                   "coordinateSpace": "original_artifact_bytes"}
                        group_refs.append(ref)
                refs[group_id] = group_refs
        # A staged workspace may contain WAL/SHM files copied from its prior
        # generation.  They are coupled to the old database bytes and must not
        # survive replacement of the main snapshot file.
        _remove_sqlite_sidecars(path)
        os.replace(temporary, path)
    finally:
        _remove_sqlite_sidecars(Path(temporary))
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {
        "path": str(path),
        "publishedPath": str(published_path or path),
        "groups": refs,
        "warning": "Contains unfiltered original bytes; local-only, not a redacted model payload",
    }
