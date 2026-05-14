"""Shared evidence rendering helpers for source filters."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ...constants import DEFAULT_WINDOWS


def window_summary(anchor_dt: datetime | None, source_kind: str) -> str:
    if anchor_dt is None:
        return "fallback-full-source"
    if source_kind == "event_log":
        return f"{DEFAULT_WINDOWS['event_log_before_seconds']}s-before/0s-after"
    before_seconds = DEFAULT_WINDOWS["logcat_before_seconds"] if source_kind == "logcat" else DEFAULT_WINDOWS["kernel_before_seconds"]
    after_seconds = DEFAULT_WINDOWS["logcat_after_seconds"] if source_kind == "logcat" else DEFAULT_WINDOWS["kernel_after_seconds"]
    return f"{before_seconds}s-before/{after_seconds}s-after"


def build_evidence(
    *,
    evidence_id: str,
    source_kind: str,
    tier: str,
    extraction_mode: str,
    rule_name: str,
    anchor: dict[str, Any] | None,
    source_path: str,
    content: str,
    time_window: str,
    label: str,
    warning_flags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": evidence_id,
        "label": label,
        "sourceKind": source_kind,
        "tier": tier,
        "extractionMode": extraction_mode,
        "content": content,
        "provenance": {
            "sourceKind": source_kind,
            "sourcePath": source_path,
            "extractionRule": rule_name,
            "timeWindow": time_window,
            "anchorUsed": anchor,
            "tier": tier,
            "extractionMode": extraction_mode,
            "warningFlags": warning_flags or [],
        },
    }
