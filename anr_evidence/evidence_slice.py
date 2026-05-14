"""Evidence Slice Schema (ESS) — structured JSONL representation of ANR evidence.

Each evidence line is enriched with delta-t, importance, source attribution, and
tag matching so downstream consumers (AI agents, entity linker, context flooding
prevention) operate on a common schema rather than raw strings.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .log_filter import TimestampParser, parse_log_timestamp
from .weighting import EVENT_LOG_TAG_WEIGHTS, ImportanceLevel, resolve_tag


@dataclass(frozen=True)
class EvidenceSlice:
    """A single timestamped piece of evidence with importance and delta-t."""

    source: str                      # "trace", "event_log", "logcat", "kernel_log"
    timestamp_iso: str | None        # ISO-8601, or None if unparseable
    delta_t_seconds: float | None    # negative = before anchor, positive = after
    tag: str | None                  # matched pattern tag, e.g. "am_anr", "binder"
    content: str                     # the raw log line
    importance: str                  # "critical", "warning", "contextual"
    group_id: str | None             # the ANR group this belongs to
    line_index: int | None           # position in original source (0-based)


def build_evidence_slices(
    groups: list[dict[str, Any]],
    *,
    timestamp_parser: TimestampParser = parse_log_timestamp,
) -> list[EvidenceSlice]:
    """Build a flat list of EvidenceSlice objects from ai_context groups."""
    slices: list[EvidenceSlice] = []
    for group in groups:
        group_id = group.get("id", "unknown")
        anchor = group.get("anchor")
        anchor_dt = None
        if anchor and anchor.get("timestamp"):
            anchor_dt = timestamp_parser(anchor["timestamp"])

        for source_key, source_kind in [
            ("trace", "trace"),
            ("eventLog", "event_log"),
            ("logcat", "logcat"),
            ("anrManager", "anr_manager"),
        ]:
            source_data = group.get(source_key, {})
            lines = source_data.get("lines", [])
            for idx, line in enumerate(lines):
                ts = timestamp_parser(line)
                ts_iso = None
                delta_t = None
                if ts is not None and anchor_dt is not None:
                    ts_iso = ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}"
                    delta_t = round((ts - anchor_dt).total_seconds(), 3)

                slices.append(EvidenceSlice(
                    source=source_kind,
                    timestamp_iso=ts_iso,
                    delta_t_seconds=delta_t,
                    tag=None,
                    content=line,
                    importance=ImportanceLevel.CONTEXTUAL.value,
                    group_id=group_id,
                    line_index=idx,
                ))

    return slices


def annotate_slices_with_tags(
    slices: list[EvidenceSlice],
    *,
    event_tags: frozenset[str] | None = None,
    logcat_patterns: frozenset[str] | None = None,
) -> list[EvidenceSlice]:
    """Annotate slices by matching content against known tag sets.

    Returns new EvidenceSlice objects with .tag and .importance populated.
    """
    from .log_filter import DEFAULT_EVENT_LOG_TAGS, LOGCAT_SIGNAL_PATTERNS

    event_tags = event_tags or DEFAULT_EVENT_LOG_TAGS
    logcat_patterns = logcat_patterns or LOGCAT_SIGNAL_PATTERNS

    annotated: list[EvidenceSlice] = []
    for s in slices:
        tag = None
        importance = ImportanceLevel.CONTEXTUAL.value
        if s.source == "event_log":
            tag = resolve_tag(s.content, event_tags)
            if tag:
                importance = EVENT_LOG_TAG_WEIGHTS.get(tag.lower(), ImportanceLevel.CONTEXTUAL).value
        elif s.source in ("logcat", "anr_manager"):
            tag = resolve_tag(s.content, logcat_patterns)
            if tag:
                from .weighting import LOGCAT_SIGNAL_WEIGHTS
                importance = LOGCAT_SIGNAL_WEIGHTS.get(tag.lower(), ImportanceLevel.CONTEXTUAL).value
            # AnrManager lines are inherently CRITICAL diagnostic evidence
            if s.source == "anr_manager":
                importance = ImportanceLevel.CRITICAL.value
        elif s.source == "kernel_log":
            from .log_filter import KERNEL_SIGNAL_PATTERNS
            tag = resolve_tag(s.content, KERNEL_SIGNAL_PATTERNS)
            if tag:
                from .weighting import KERNEL_SIGNAL_WEIGHTS
                importance = KERNEL_SIGNAL_WEIGHTS.get(tag.lower(), ImportanceLevel.CONTEXTUAL).value
        annotated.append(EvidenceSlice(
            source=s.source,
            timestamp_iso=s.timestamp_iso,
            delta_t_seconds=s.delta_t_seconds,
            tag=tag,
            content=s.content,
            importance=importance,
            group_id=s.group_id,
            line_index=s.line_index,
        ))
    return annotated


def build_ess_from_ai_context_result(
    groups: list[dict[str, Any]],
    *,
    event_tags: frozenset[str] | None = None,
    logcat_patterns: frozenset[str] | None = None,
) -> list[EvidenceSlice]:
    """Build and annotate ESS from ai_context.py group dicts."""
    slices = build_evidence_slices(groups)
    return annotate_slices_with_tags(slices, event_tags=event_tags, logcat_patterns=logcat_patterns)


# ── JSONL I/O ──────────────────────────────────────────────────────────────


def _slice_to_dict(s: EvidenceSlice) -> dict[str, Any]:
    return {
        "source": s.source,
        "timestamp_iso": s.timestamp_iso,
        "delta_t_seconds": s.delta_t_seconds,
        "tag": s.tag,
        "content": s.content,
        "importance": s.importance,
        "group_id": s.group_id,
        "line_index": s.line_index,
    }


def _dict_to_slice(d: dict[str, Any]) -> EvidenceSlice:
    return EvidenceSlice(
        source=d["source"],
        timestamp_iso=d.get("timestamp_iso"),
        delta_t_seconds=d.get("delta_t_seconds"),
        tag=d.get("tag"),
        content=d["content"],
        importance=d.get("importance", "contextual"),
        group_id=d.get("group_id"),
        line_index=d.get("line_index"),
    )


def write_ess_jsonl(slices: list[EvidenceSlice], path: Path) -> None:
    """Write ESS as newline-delimited JSON to a file."""
    with path.open("w", encoding="utf-8") as fh:
        for s in slices:
            fh.write(json.dumps(_slice_to_dict(s), ensure_ascii=False) + "\n")


def read_ess_jsonl(path: Path) -> list[EvidenceSlice]:
    """Read ESS from a JSONL file."""
    slices: list[EvidenceSlice] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            slices.append(_dict_to_slice(json.loads(line)))
    return slices


# ── Filter helpers ─────────────────────────────────────────────────────────


def group_slices_by_source(
    slices: list[EvidenceSlice],
) -> dict[str, list[EvidenceSlice]]:
    """Group slices by source_kind for sub-agent dispatch."""
    result: dict[str, list[EvidenceSlice]] = {}
    for s in slices:
        result.setdefault(s.source, []).append(s)
    return result


def filter_slices_by_importance(
    slices: list[EvidenceSlice],
    *,
    min_importance: str = "warning",
) -> list[EvidenceSlice]:
    """Filter slices to those at or above a given importance level."""
    order = {"critical": 0, "warning": 1, "contextual": 2}
    threshold = order.get(min_importance, 2)
    return [s for s in slices if order.get(s.importance, 2) <= threshold]


def filter_slices_by_delta_t(
    slices: list[EvidenceSlice],
    *,
    min_delta_t: float | None = None,
    max_delta_t: float | None = None,
) -> list[EvidenceSlice]:
    """Filter slices to a narrower delta-t window (for iterative re-probe)."""
    result: list[EvidenceSlice] = []
    for s in slices:
        if s.delta_t_seconds is None:
            if min_delta_t is None and max_delta_t is None:
                result.append(s)
            continue
        if min_delta_t is not None and s.delta_t_seconds < min_delta_t:
            continue
        if max_delta_t is not None and s.delta_t_seconds > max_delta_t:
            continue
        result.append(s)
    return result


def filter_slices_by_source(
    slices: list[EvidenceSlice],
    *,
    source_kinds: list[str] | None = None,
) -> list[EvidenceSlice]:
    """Filter slices to specific source kinds."""
    if source_kinds is None:
        return slices
    return [s for s in slices if s.source in source_kinds]
