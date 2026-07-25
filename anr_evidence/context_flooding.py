"""Context flooding prevention for ANR evidence.

Provides importance-based truncation strategies that ensure evidence sent to
LLMs stays within token budget while preserving the most diagnostically
valuable content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .evidence_slice import EvidenceSlice

# Rough chars-per-token estimate for ANR log text, used together with an
# assumed ~80 chars per line to convert a token budget into a line budget
# (see truncate_evidence): max_lines = max_tokens * 4 / 80.
_EST_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class TruncationConfig:
    """Configuration for context flooding prevention."""

    max_total_lines: int = 200
    max_per_source: dict[str, int] = field(default_factory=lambda: {
        "trace": 60,
        "event_log": 60,
        "logcat": 80,
        "kernel_log": 40,
    })
    min_importance: str = "warning"       # drop "contextual" first
    preserve_critical: bool = True
    preserve_anchor_lines: bool = True    # always keep lines closest to anchor
    max_tokens: int | None = None         # if set, overrides max_total_lines


@dataclass(frozen=True)
class TruncationResult:
    """Result of applying context flooding prevention."""

    retained_slices: list[EvidenceSlice]
    dropped_slices: list[EvidenceSlice]
    stats: dict[str, Any]        # per-source: {retained: int, dropped: int}


def truncate_evidence(
    slices: list[EvidenceSlice],
    config: TruncationConfig = TruncationConfig(),
) -> TruncationResult:
    """Apply importance-based truncation to evidence slices.

    Strategy (in order):
    1. Always retain CRITICAL slices (up to per-source cap)
    2. Fill remaining budget with WARNING slices (closest to anchor first)
    3. Fill any remaining budget with CONTEXTUAL slices (closest to anchor first)
    4. If still over budget, drop slices farthest from anchor (largest |delta_t|)
    """
    if config.max_tokens is not None:
        est_max_lines = config.max_tokens * _EST_CHARS_PER_TOKEN // 80
        max_total = min(config.max_total_lines, est_max_lines)
    else:
        max_total = config.max_total_lines

    # Group by source
    by_source: dict[str, list[EvidenceSlice]] = {}
    for s in slices:
        by_source.setdefault(s.source, []).append(s)

    retained: list[EvidenceSlice] = []
    dropped: list[EvidenceSlice] = []
    stats: dict[str, dict[str, int]] = {}

    total_retained = 0

    for source, source_slices in by_source.items():
        source_cap = config.max_per_source.get(source, 60)
        source_retained: list[EvidenceSlice] = []
        source_dropped: list[EvidenceSlice] = []

        # Sort by importance desc, then by |delta_t| asc (closer to anchor first)
        importance_order = {"critical": 0, "warning": 1, "contextual": 2}
        sorted_slices = sorted(
            source_slices,
            key=lambda s: (
                importance_order.get(s.importance, 2),
                abs(s.delta_t_seconds) if s.delta_t_seconds is not None else 999999,
            ),
        )

        min_importance_idx = importance_order.get(config.min_importance, 2)

        for s in sorted_slices:
            idx = importance_order.get(s.importance, 2)
            is_anchor = config.preserve_anchor_lines and s.delta_t_seconds == 0
            if (config.preserve_critical and idx == 0) or is_anchor:
                source_retained.append(s)
                continue
            if idx > min_importance_idx:
                source_dropped.append(s)
                continue
            if len(source_retained) < source_cap and total_retained < max_total:
                source_retained.append(s)
            else:
                source_dropped.append(s)

        retained.extend(source_retained)
        dropped.extend(source_dropped)
        stats[source] = {
            "retained": len(source_retained),
            "dropped": len(source_dropped),
        }
        total_retained = len(retained)

    # Global overflow check: if total retained still exceeds max_total,
    # drop those farthest from anchor
    if len(retained) > max_total:
        overflow = len(retained) - max_total
        # Sort retained by |delta_t| desc, keep non-critical first for dropping
        non_critical_retained = [s for s in retained if s.importance != "critical"]
        critical_retained = [s for s in retained if s.importance == "critical"]
        non_critical_retained.sort(
            key=lambda s: abs(s.delta_t_seconds) if s.delta_t_seconds is not None else 0,
            reverse=True,
        )
        drop_from = non_critical_retained[:overflow]
        keep_from = non_critical_retained[overflow:]
        retained = critical_retained + keep_from
        dropped = dropped + drop_from

    retained_ids = {id(item) for item in retained}
    for source, source_slices in by_source.items():
        stats[source] = {
            "retained": sum(id(item) in retained_ids for item in source_slices),
            "dropped": sum(id(item) not in retained_ids for item in source_slices),
        }
    stats["_global"] = {
        "retained": len(retained),
        "dropped": len(dropped),
        "budget": max_total,
        "budgetOverflow": max(0, len(retained) - max_total),
    }

    return TruncationResult(
        retained_slices=retained,
        dropped_slices=dropped,
        stats=stats,
    )


def truncation_stats_text(result: TruncationResult) -> str:
    """Render a human-readable summary of what was dropped."""
    lines = ["### Context Truncation Summary"]
    for source, counts in sorted(result.stats.items()):
        if source == "_global":
            continue
        lines.append(f"- `{source}`: {counts['retained']} retained, {counts['dropped']} dropped")
    total = len(result.retained_slices) + len(result.dropped_slices)
    lines.append(f"- **Total**: {len(result.retained_slices)}/{total} lines retained "
                  f"({100 * len(result.retained_slices) // max(total, 1)}%)")
    if result.dropped_slices:
        by_source: dict[str, int] = {}
        for s in result.dropped_slices:
            by_source[s.source] = by_source.get(s.source, 0) + 1
        lines.append("- Dropped breakdown: " + ", ".join(
            f"`{k}`: {v}" for k, v in sorted(by_source.items())
        ))
    global_stats = result.stats.get("_global", {})
    if global_stats.get("budgetOverflow"):
        lines.append(
            f"- **Protected evidence overflow**: {global_stats['budgetOverflow']} lines exceed the configured "
            "budget because critical/anchor evidence is preserved."
        )
    return "\n".join(lines)
