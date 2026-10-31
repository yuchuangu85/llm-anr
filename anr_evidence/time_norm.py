"""Delta-t time normalization for evidence lines relative to ANR anchor timestamps.

Computes per-line ``delta_t_seconds`` so every evidence line can be expressed
as an offset from the ANR trigger, enabling temporal reasoning by both
deterministic rules and LLM agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

from .log_filter import TIMESTAMP_RE, TimestampParser, parse_log_timestamp


def parse_record_timestamp(text: str, *, year: int | None = None, trace: bool = False) -> tuple[datetime | None, str]:
    """Parse a record prefix or trace header, never a historical date in its payload."""
    for raw in text.splitlines() if trace else [text]:
        line = raw.lstrip()
        is_header = trace and re.match(r"(?:-----\s*pid\b|Dump time:|Dump timestamp:|Timestamp:|Time:)", line, re.IGNORECASE)
        if not TIMESTAMP_RE.match(line) and not is_header:
            continue
        timestamp = parse_log_timestamp(raw, **({"year": year} if year is not None else {}))
        if timestamp is not None:
            return timestamp, raw
    return None, text.splitlines()[0] if text.splitlines() else ""


def record_clock_domain(line: str, parsed: datetime | None) -> str:
    if parsed is not None:
        timestamp = TIMESTAMP_RE.search(line)
        offset = re.match(r"\s*(Z|[+-]\d{2}:?\d{2})\b", line[timestamp.end():]) if timestamp else None
        return f"wall_clock:{offset.group(1)}" if offset else "wall_clock:unspecified"
    if re.search(r"\b(?:uptime|boottime|elapsedRealtime)\b|^\s*\[\s*\d+\.\d+\]", line):
        return "monotonic:unmapped"
    return "unknown"


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
    anchor_clock_domain: str = "wall_clock:unspecified",
) -> list[TimeNormalizedLine]:
    """Parse timestamps from each line and compute seconds delta from anchor."""
    result: list[TimeNormalizedLine] = []
    for line in lines:
        ts = parse_record_timestamp(line, year=anchor_dt.year, trace=source_kind == "trace")[0] if timestamp_parser is parse_log_timestamp else timestamp_parser(line)
        if ts is None:
            result.append(TimeNormalizedLine(
                raw_line=line,
                timestamp_iso=None,
                delta_t_seconds=None,
                source_kind=source_kind,
            ))
            continue
        comparable = timestamp_parser is not parse_log_timestamp or record_clock_domain(line, ts) == anchor_clock_domain
        delta = (ts - anchor_dt).total_seconds() if comparable else None
        result.append(TimeNormalizedLine(
            raw_line=line,
            timestamp_iso=ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}",
            delta_t_seconds=round(delta, 3) if delta is not None else None,
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

    anchor_dt, anchor_line = parse_record_timestamp(anchor.get("line") or anchor["timestamp"])
    if anchor_dt is None:
        anchor_dt, anchor_line = parse_record_timestamp(anchor["timestamp"])
    if anchor_dt is None:
        return _normalize_without_anchor(group)

    result: dict[str, list[TimeNormalizedLine]] = {}
    for source_key, source_kind in [
        ("trace", "trace"),
        ("eventLog", "event_log"),
        ("logcat", "logcat"),
        ("anrManager", "anr_manager"),
        ("kernelLog", "kernel_log"),
    ]:
        source_data = group.get(source_key, {})
        lines = source_data.get("lines", [])
        if not lines:
            result[source_key] = []
        else:
            result[source_key] = compute_delta_t(
                lines, anchor_dt, source_kind=source_kind,
                anchor_clock_domain=record_clock_domain(anchor_line, anchor_dt),
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
        ("kernelLog", "kernel_log"),
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
