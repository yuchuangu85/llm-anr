"""Independent trace filtering and preprocessing entrypoint."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from ...log_filter import parse_log_timestamp, timestamp_to_raw
from ...trace_preprocessor import preprocess_trace_content
from ..shared import SourceFilterContext, SourceFilterOptions, SourceFilterResult, build_evidence


def parse_trace_filename_timestamp(path: str) -> datetime | None:
    """Parse ANR timestamp from common trace filenames.

    Expected format: ``anr_YYYY-MM-DD-HH-MM-SS-mmm``.  The millisecond
    component is optional because some vendors omit it.
    """

    m = re.search(r"anr_(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})(?:-(\d{3}))?", path)
    if not m:
        return None
    try:
        microsecond = int(m[7]) * 1000 if m[7] is not None else 0
        return datetime(
            int(m[1]),
            int(m[2]),
            int(m[3]),
            int(m[4]),
            int(m[5]),
            int(m[6]),
            microsecond,
        )
    except ValueError:
        return None


def parse_trace_content_timestamp(content: str) -> datetime | None:
    """Parse the first useful ANR timestamp from trace content."""

    full_match = re.search(
        r"\b(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?",
        content,
    )
    if full_match:
        try:
            fraction = (full_match[7] or "0")[:6].ljust(6, "0")
            return datetime(
                int(full_match[1]),
                int(full_match[2]),
                int(full_match[3]),
                int(full_match[4]),
                int(full_match[5]),
                int(full_match[6]),
                int(fraction),
            )
        except ValueError:
            pass
    for line in content.splitlines():
        lowered = line.lower()
        if "anr" not in lowered and "input dispatching" not in lowered and "focused window" not in lowered:
            continue
        timestamp = parse_log_timestamp(line)
        if timestamp is not None:
            return timestamp
    return None


def trace_anr_timestamp_from_entries(entries: list[dict[str, Any]]) -> datetime | None:
    """Return the trace ANR time used to align sharded log files."""

    for entry in entries:
        timestamp = parse_trace_filename_timestamp(entry.get("path", ""))
        if timestamp is not None:
            return timestamp
        timestamp = parse_trace_content_timestamp(entry.get("content", ""))
        if timestamp is not None:
            return timestamp
    return None


def filter_trace_source(
    source: dict[str, Any],
    context: SourceFilterContext | None = None,
    options: SourceFilterOptions | None = None,
) -> SourceFilterResult:
    """Build baseline trace evidence from one trace source."""

    context = context or SourceFilterContext()
    options = options or SourceFilterOptions()
    content = source.get("content", "")
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return SourceFilterResult(
            source_kind="trace",
            warnings=[{"code": "empty-trace", "message": "Trace source exists but no non-empty lines were retained."}],
        )

    preprocessed = preprocess_trace_content(
        content,
        anchor_timestamp=timestamp_to_raw(context.anchor_dt) if context.anchor_dt else None,
        process_name=options.package_name or context.package_name,
    )
    selected = preprocessed["compactedLines"]
    evidence = [build_evidence(
        evidence_id="trace_core",
        source_kind="trace",
        tier="P0",
        extraction_mode="baseline",
        rule_name="trace-baseline",
        anchor=context.primary_anchor or {"sourceKind": "trace", "timestamp": None, "line": None},
        source_path=source.get("path", "trace"),
        content="\n".join(selected),
        time_window="full-trace-context",
        label="trace-baseline-context",
    )]
    return SourceFilterResult(
        source_kind="trace",
        evidence=evidence,
        lines=selected,
        metadata={"traceAnrTimestamp": parse_trace_filename_timestamp(source.get("path", "")) or parse_trace_content_timestamp(content)},
    )
