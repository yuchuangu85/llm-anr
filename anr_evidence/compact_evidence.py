"""Hard-bounded compact evidence views for LLM-facing artifacts.

This module intentionally does not replace the baseline filtered artifacts or
line-oriented context flooding policy.  It serializes a deterministic subset of
ESS rows, never splitting a UTF-8 record, so the emitted compact view itself
has an independently enforceable byte limit.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Sequence

from .evidence_slice import EvidenceSlice


@dataclass(frozen=True)
class CompactEvidenceResult:
    """Exact compact-view bytes plus auditable selection metadata."""

    content: bytes
    metadata: dict[str, Any]


def _importance_rank(slice_: EvidenceSlice) -> int:
    return {"critical": 0, "warning": 1, "contextual": 2}.get(slice_.importance, 3)


def _sort_key(slice_: EvidenceSlice) -> tuple[Any, ...]:
    return (
        _importance_rank(slice_),
        abs(slice_.delta_t_seconds) if slice_.delta_t_seconds is not None else float("inf"),
        slice_.source,
        slice_.line_index if slice_.line_index is not None else -1,
        slice_.content,
    )


def _record(slice_: EvidenceSlice) -> bytes:
    """Return a complete JSONL record; callers must not split this boundary."""

    value = {
        "source": slice_.source,
        "timestampIso": slice_.timestamp_iso,
        "deltaTSeconds": slice_.delta_t_seconds,
        "tag": slice_.tag,
        "content": slice_.content,
        "importance": slice_.importance,
        "groupId": slice_.group_id,
        "lineIndex": slice_.line_index,
        **({"evidenceRef": slice_.evidence_ref} if slice_.evidence_ref is not None else {}),
        **({"evidenceRefError": slice_.evidence_ref_error} if slice_.evidence_ref_error is not None else {}),
    }
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def build_compact_evidence_view(
    slices: Sequence[EvidenceSlice], *, max_bytes: int,
) -> CompactEvidenceResult:
    """Serialize a deterministic ESS subset under a strict UTF-8 byte cap.

    The returned bytes are the LLM-facing compact view and are always at most
    ``max_bytes``.  Whole JSONL records are selected by ESS importance and
    anchor proximity.  Oversize records are omitted with explicit metadata;
    no original evidence line is partially sliced or falsely represented as a
    complete source span.
    """

    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("compact LLM view max_bytes must be a positive integer")

    source_stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"candidateCount": 0, "retainedCount": 0, "droppedCount": 0}
    )
    for item in slices:
        source_stats[item.source]["candidateCount"] += 1

    selected: list[bytes] = []
    selected_count = 0
    dropped_count = 0
    protected_dropped_count = 0
    used = 0
    for item in sorted(slices, key=_sort_key):
        record = _record(item)
        if used + len(record) <= max_bytes:
            selected.append(record)
            used += len(record)
            selected_count += 1
            source_stats[item.source]["retainedCount"] += 1
            continue
        dropped_count += 1
        source_stats[item.source]["droppedCount"] += 1
        if item.importance == "critical" or item.delta_t_seconds == 0:
            protected_dropped_count += 1

    content = b"".join(selected)
    metadata = {
        "format": "anr-compact-evidence-jsonl-v1",
        "encoding": "utf-8",
        "budgetBytes": max_bytes,
        "retainedBytes": len(content),
        "candidateCount": len(slices),
        "retainedCount": selected_count,
        "droppedCount": dropped_count,
        "truncatedCount": dropped_count,
        "truncated": bool(dropped_count),
        "overflow": False,
        "failClosed": False,
        "protectedDroppedCount": protected_dropped_count,
        "recordBoundaryOnly": True,
        "sha256": hashlib.sha256(content).hexdigest(),
        "sourceStats": dict(sorted(source_stats.items())),
    }
    if dropped_count:
        metadata["truncationReason"] = "hard_utf8_byte_budget"
    return CompactEvidenceResult(content=content, metadata=metadata)
