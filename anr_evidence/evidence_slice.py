"""Evidence Slice Schema (ESS) — structured JSONL representation of ANR evidence.

Each evidence line is enriched with delta-t, importance, source attribution, and
tag matching so downstream consumers (AI agents, entity linker, context flooding
prevention) operate on a common schema rather than raw strings.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any

from .evidence_store import EvidenceStore, EvidenceStoreError
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
    line_index: int | None           # position in the supplied group source view (0-based)
    evidence_ref: dict[str, Any] | None = None  # optional verified store span coordinates
    evidence_ref_error: str | None = None  # explicit failed/untrusted reference binding


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
            ("meminfo", "meminfo"),
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
        if s.source == "trace":
            # The trace is already compacted by trace_preprocessor and is the
            # primary direct-blocking evidence; truncation must not discard it
            # merely because stack lines do not have EventLog-style tags.
            importance = ImportanceLevel.CRITICAL.value
        elif s.source == "event_log":
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
        elif s.source == "meminfo":
            importance = ImportanceLevel.WARNING.value
        annotated.append(EvidenceSlice(
            source=s.source,
            timestamp_iso=s.timestamp_iso,
            delta_t_seconds=s.delta_t_seconds,
            tag=tag,
            content=s.content,
            importance=importance,
            group_id=s.group_id,
            line_index=s.line_index,
            evidence_ref=s.evidence_ref,
            evidence_ref_error=s.evidence_ref_error,
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
        **({"evidence_ref": s.evidence_ref} if s.evidence_ref is not None else {}),
        **({"evidence_ref_error": s.evidence_ref_error} if s.evidence_ref_error is not None else {}),
    }


def _dict_to_slice(d: dict[str, Any]) -> EvidenceSlice:
    error = d.get("evidence_ref_error")
    known_errors = {
        "serialized_reference_unverified", "serialized_reference_error",
        "malformed_sidecar", "ambiguous_sidecar_candidates", "reference_integrity_check_failed",
    }
    if error is not None and (not isinstance(error, str) or error not in known_errors):
        error = "serialized_reference_error"
    if d.get("evidence_ref") is not None:
        error = "serialized_reference_unverified"
    return EvidenceSlice(
        source=d["source"],
        timestamp_iso=d.get("timestamp_iso"),
        delta_t_seconds=d.get("delta_t_seconds"),
        tag=d.get("tag"),
        content=d["content"],
        importance=d.get("importance", "contextual"),
        group_id=d.get("group_id"),
        line_index=d.get("line_index"),
        # Serialized references are data, not proof. Rebind from a store/sidecar.
        evidence_ref=None,
        evidence_ref_error=error,
    )


def write_ess_jsonl(slices: list[EvidenceSlice], path: Path) -> None:
    """Write ESS as newline-delimited JSON to a file."""
    with path.open("w", encoding="utf-8") as fh:
        for s in slices:
            fh.write(json.dumps(_slice_to_dict(s), ensure_ascii=False) + "\n")


def read_ess_jsonl(path: Path, *, store=None,
                   references: dict[str, list[dict[str, Any]]] | None = None) -> list[EvidenceSlice]:
    """Read ESS, discarding serialized refs unless freshly rebound to a store."""
    if (store is None) != (references is None):
        raise ValueError("store and references must be supplied together")
    slices: list[EvidenceSlice] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            slices.append(_dict_to_slice(json.loads(line)))
    return attach_store_references(slices, store, references) if store is not None else slices


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


def attach_store_references(slices: list[EvidenceSlice], store,
                            references: dict[str, list[dict[str, Any]]]) -> list[EvidenceSlice]:
    """Bind a sidecar reference only after scoped byte-for-byte store readback.

    JSONL-supplied references are not trusted: this function replaces them.
    Failed bindings retain their full slice content with evidence_ref=None.
    """
    sections = {"trace": "trace", "event_log": "eventLog", "logcat": "logcat",
                "anr_manager": "anrManager", "meminfo": "meminfo"}
    candidates: dict[tuple, list[dict[str, Any]]] = {}
    invalid_groups: set[str] = set()
    for group_id, rows in references.items():
        if not isinstance(group_id, str):
            continue
        if not isinstance(rows, list):
            invalid_groups.add(group_id)
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("status") == "unresolved":
                continue
            if (not isinstance(row, dict) or not isinstance(row.get("section"), str)
                    or type(row.get("viewLineIndex")) is not int
                    or not isinstance(row.get("spanId"), str) or row.get("status") != "resolved"):
                invalid_groups.add(group_id)
                continue
            if row.get("status") == "resolved":
                key = (group_id, row.get("section"), row.get("viewLineIndex"))
                candidates.setdefault(key, []).append(row)
    output = []
    for item in slices:
        ref = None
        error = "malformed_sidecar" if item.group_id in invalid_groups else None
        rows = candidates.get((item.group_id, sections.get(item.source), item.line_index), [])
        if len(rows) > 1:
            error = "ambiguous_sidecar_candidates"
        if len(rows) == 1 and error is None:
            row = rows[0]
            source_kind = "logcat" if item.source == "anr_manager" else item.source
            try:
                if row.get("sourceKind") != source_kind:
                    raise EvidenceStoreError("Reference source mismatch")
                read_kwargs = {}
                if "artifactCommitment" in row:
                    read_kwargs["expected_artifact_commitment"] = row["artifactCommitment"]
                value = store.read_span(
                    row["spanId"], group_id=item.group_id, source_kind=source_kind,
                    max_bytes=EvidenceStore.DEFAULT_MAX_REFERENCE_READ_BYTES,
                    **read_kwargs,
                )
                if value["content"].decode("utf-8").rstrip("\r\n") != item.content:
                    raise EvidenceStoreError("Reference content differs from slice")
                keys = [
                    "spanId", "groupId", "artifactId", "sourceKind", "sha256",
                    "startByte", "endByte", "coordinateSpace",
                ]
                if "artifactCommitment" in value:
                    keys.append("artifactCommitment")
                ref = {key: value[key] for key in keys}
            except (EvidenceStoreError, KeyError, UnicodeError):
                error = "reference_integrity_check_failed"
        output.append(replace(item, evidence_ref=ref, evidence_ref_error=error))
    return output
