"""Anchor candidate collection and resolution for Phase 1 extraction."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from ..constants import TIME_ANCHOR_PRECEDENCE
from ..log_filter import parse_log_timestamp
from .common import dedupe_dicts, timestamp_to_raw_value


@dataclass(frozen=True)
class AnchorCandidate:
    source_kind: str
    timestamp: datetime
    raw_timestamp: str
    line: str


def collect_anchor_candidates(package: dict[str, Any]) -> list[AnchorCandidate]:
    candidates: list[AnchorCandidate] = []
    source_extractors = {
        "event_log": _collect_event_log_candidates,
        "trace": _collect_trace_candidates,
        "logcat": _collect_logcat_candidates,
        "kernel_log": _collect_kernel_candidates,
    }
    for source_kind, extractor in source_extractors.items():
        source = package["sources"].get(source_kind)
        if not source:
            continue
        candidates.extend(extractor(source.get("content", ""), source_kind))
    return candidates


def resolve_anchor(candidates: list[AnchorCandidate]) -> dict[str, Any]:
    warnings: list[dict[str, str]] = []
    primary = None
    for source_kind in TIME_ANCHOR_PRECEDENCE:
        source_candidates = [candidate for candidate in candidates if candidate.source_kind == source_kind]
        if source_candidates:
            primary = source_candidates[0]
            break
    secondary = []
    if primary:
        for candidate in candidates:
            if candidate == primary:
                continue
            secondary.append({
                "sourceKind": candidate.source_kind,
                "timestamp": candidate.raw_timestamp,
                "line": candidate.line,
            })
            if abs(candidate.timestamp - primary.timestamp) > timedelta(seconds=1):
                warnings.append({
                    "code": "anchor-mismatch",
                    "message": f"Observed anchor mismatch between {primary.source_kind} ({primary.raw_timestamp}) and {candidate.source_kind} ({candidate.raw_timestamp}).",
                })
    primary_anchor = None
    if primary:
        primary_anchor = {
            "sourceKind": primary.source_kind,
            "timestamp": primary.raw_timestamp,
            "line": primary.line,
        }
    return {
        "primary_anchor": primary_anchor,
        "secondary_anchors": secondary,
        "warnings": dedupe_dicts(warnings),
    }


def _collect_event_log_candidates(content: str, source_kind: str) -> list[AnchorCandidate]:
    return _candidate_lines(content, source_kind, lambda lower: "am_anr" in lower)


def _collect_trace_candidates(content: str, source_kind: str) -> list[AnchorCandidate]:
    return _candidate_lines(content, source_kind, lambda lower: "anr" in lower or "input dispatching" in lower or "focused window" in lower)


def _collect_logcat_candidates(content: str, source_kind: str) -> list[AnchorCandidate]:
    return _candidate_lines(content, source_kind, lambda lower: "input dispatching" in lower or "focused window" in lower or "am_anr" in lower)


def _collect_kernel_candidates(content: str, source_kind: str) -> list[AnchorCandidate]:
    return _candidate_lines(content, source_kind, lambda lower: "binder" in lower or "sched" in lower or "input" in lower)


def _candidate_lines(content: str, source_kind: str, predicate: Callable[[str], bool]) -> list[AnchorCandidate]:
    candidates: list[AnchorCandidate] = []
    for line in content.splitlines():
        ts = parse_log_timestamp(line)
        if ts is None:
            continue
        if predicate(line.lower()):
            candidates.append(AnchorCandidate(source_kind=source_kind, timestamp=ts, raw_timestamp=timestamp_to_raw_value(ts), line=line.strip()))
    return candidates[:1]
