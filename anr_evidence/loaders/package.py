"""Build normalized source packages from discovered file entries."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ..am_anr import package_name_from_am_anr_line
from ..constants import OPTIONAL_SOURCE_KINDS, SOURCE_KINDS
from ..log_filter import parse_log_timestamp
from ..sources.shared import select_preceding_entries_for_anchor
from ..sources.shared.detection import dedupe_and_rank_entries, detect_source_kind
from ..sources.trace import parse_trace_content_timestamp, parse_trace_filename_timestamp, trace_anr_timestamp_from_entries


def build_package_from_entries(
    package_id: str,
    entries: list[dict[str, Any]],
    package_name: str | None = None,
    event_anchor_dt: datetime | None = None,
) -> dict[str, Any]:
    source_kinds = SOURCE_KINDS + OPTIONAL_SOURCE_KINDS
    grouped: dict[str, list[dict[str, Any]]] = {kind: [] for kind in source_kinds}
    for entry in entries:
        source_kind = detect_source_kind(Path(entry["path"]), entry["content"])
        if not source_kind:
            continue
        grouped[source_kind].append(entry)

    ranked_trace_entries = dedupe_and_rank_entries("trace", grouped["trace"])
    ranked_event_entries = dedupe_and_rank_entries("event_log", grouped["event_log"])
    if event_anchor_dt is None:
        event_anchor_dt = _event_anr_timestamp_from_entries(ranked_event_entries, package_name)
    if package_name:
        package_trace_entries = [
            entry for entry in ranked_trace_entries
            if package_name in entry.get("content", "") or package_name in entry.get("path", "")
        ]
    else:
        package_trace_entries = []
    trace_anchor_entries = package_trace_entries or ranked_trace_entries
    trace_anchor_dt = event_anchor_dt or trace_anr_timestamp_from_entries(trace_anchor_entries)
    sources: dict[str, dict[str, Any]] = {}
    for source_kind, source_entries in grouped.items():
        if not source_entries:
            continue
        source_entries = dedupe_and_rank_entries(source_kind, source_entries)
        if source_kind == "trace":
            if event_anchor_dt:
                source_entries = _select_trace_entries_for_anchor(source_entries, event_anchor_dt)
            elif package_trace_entries:
                source_entries = package_trace_entries
        if source_kind in ("event_log", "logcat"):
            source_entries = select_preceding_entries_for_anchor(source_entries, trace_anchor_dt)
        sources[source_kind] = {
            "path": ",".join(entry["path"] for entry in source_entries),
            "content": "\n".join(entry["content"] for entry in source_entries if entry["content"]),
            "readable": all(entry["readable"] for entry in source_entries),
        }
    return {
        "package_id": package_id,
        "provided_type": None,
        "sources": sources,
    }


def trace_anr_timestamp_from_entry_list(entries: list[dict[str, Any]]) -> datetime | None:
    """Return the trace ANR time used to align sharded log files."""
    return trace_anr_timestamp_from_entries(dedupe_and_rank_entries("trace", entries))


def _event_anr_timestamp_from_entries(entries: list[dict[str, Any]], package_name: str | None) -> datetime | None:
    """Return the first EventLog ``am_anr`` timestamp.

    When the caller provides a target package, only package-matching ``am_anr``
    lines are considered.  Without a package filter, the first timestamped
    ``am_anr`` line with an identifiable package/process becomes the EventLog
    anchor so archives and command-less environments use the same
    pre-``anr_ai_context`` anchoring semantics as the fg/rg/grep fast path.
    """

    for entry in entries:
        for line in entry.get("content", "").splitlines():
            lowered = line.lower()
            if "am_anr" not in lowered:
                continue
            if package_name:
                if package_name not in line:
                    continue
            elif package_name_from_am_anr_line(line) is None:
                continue
            timestamp = parse_log_timestamp(line)
            if timestamp is not None:
                return timestamp
    return None


def _select_trace_entries_for_anchor(entries: list[dict[str, Any]], anchor_dt: datetime) -> list[dict[str, Any]]:
    """Select the trace file whose ANR timestamp is closest to *anchor_dt*."""

    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in entries:
        timestamp = parse_trace_filename_timestamp(entry.get("path", ""))
        if timestamp is None:
            timestamp = parse_trace_content_timestamp(entry.get("content", ""))
        if timestamp is None:
            continue
        scored.append((abs((timestamp - anchor_dt).total_seconds()), entry))
    if not scored:
        return entries
    scored.sort(key=lambda item: (item[0], item[1].get("path", "")))
    return [scored[0][1]]
