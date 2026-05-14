"""Delta-t time normalization for evidence lines relative to ANR anchor timestamps.

Computes per-line ``delta_t_seconds`` so every evidence line can be expressed
as an offset from the ANR trigger, enabling temporal reasoning by both
deterministic rules and LLM agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .log_filter import TimestampParser, parse_log_timestamp


@dataclass(frozen=True)
class TimeNormalizedLine:
    """A single log line with its computed delta-t from an anchor timestamp."""

    raw_line: str
    timestamp_iso: str | None      # ISO-8601 formatted, or None if unparseable
    delta_t_seconds: float | None   # negative = before anchor, positive = after
    source_kind: str                # "trace", "event_log", "logcat", "kernel_log"


def compute_delta_t(
    lines: list[str],
    anchor_dt: datetime,
    *,
    source_kind: str,
    timestamp_parser: TimestampParser = parse_log_timestamp,
) -> list[TimeNormalizedLine]:
    """Parse timestamps from each line and compute seconds delta from anchor."""
    result: list[TimeNormalizedLine] = []
    for line in lines:
        ts = timestamp_parser(line)
        if ts is None:
            result.append(TimeNormalizedLine(
                raw_line=line,
                timestamp_iso=None,
                delta_t_seconds=None,
                source_kind=source_kind,
            ))
            continue
        delta = (ts - anchor_dt).total_seconds()
        result.append(TimeNormalizedLine(
            raw_line=line,
            timestamp_iso=ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}",
            delta_t_seconds=round(delta, 3),
            source_kind=source_kind,
        ))
    return result


def compute_delta_t_for_group(
    group: dict[str, Any],
) -> dict[str, list[TimeNormalizedLine]]:
    """Apply delta-t normalization to all sources in an ANR group dict.

    The group dict has the shape built by ai_context._build_groups:
      {id, anchor: {timestamp: "MM-DD HH:MM:SS.mmm", ...},
       trace: {lines: [...], ...},
       eventLog: {lines: [...], ...},
       logcat: {lines: [...], ...}}

    Returns a dict keyed by source kind: {"trace": [...], "eventLog": [...], "logcat": [...]}.
    """
    anchor = group.get("anchor")
    if anchor is None or not anchor.get("timestamp"):
        return _normalize_without_anchor(group)

    anchor_dt = parse_log_timestamp(anchor["timestamp"])
    if anchor_dt is None:
        return _normalize_without_anchor(group)

    result: dict[str, list[TimeNormalizedLine]] = {}
    for source_key, source_kind in [
        ("trace", "trace"),
        ("eventLog", "event_log"),
        ("logcat", "logcat"),
        ("anrManager", "anr_manager"),
    ]:
        source_data = group.get(source_key, {})
        lines = source_data.get("lines", [])
        if not lines:
            result[source_key] = []
        else:
            result[source_key] = compute_delta_t(
                lines, anchor_dt, source_kind=source_kind,
            )
    return result


def _normalize_without_anchor(
    group: dict[str, Any],
) -> dict[str, list[TimeNormalizedLine]]:
    """Return normalized lines with None delta_t when no anchor exists."""
    result: dict[str, list[TimeNormalizedLine]] = {}
    for source_key, source_kind in [
        ("trace", "trace"),
        ("eventLog", "event_log"),
        ("logcat", "logcat"),
        ("anrManager", "anr_manager"),
    ]:
        source_data = group.get(source_key, {})
        lines = source_data.get("lines", [])
        result[source_key] = [
            TimeNormalizedLine(
                raw_line=line,
                timestamp_iso=None,
                delta_t_seconds=None,
                source_kind=source_kind,
            )
            for line in lines
        ]
    return result
