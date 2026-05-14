"""Shared helpers for Phase 1 extraction."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from typing import Any, Iterable
from uuid import uuid4

from ..constants import DEFAULT_WINDOWS
from ..log_filter import parse_log_timestamp, timestamp_to_raw


def normalize_package(package: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(package)
    normalized.setdefault("package_id", f"pkg-{uuid4().hex[:8]}")
    normalized.setdefault("provided_type", None)
    normalized.setdefault("sources", {})
    for source_kind, source in list(normalized["sources"].items()):
        source.setdefault("path", source_kind)
        source.setdefault("content", "")
        source.setdefault("readable", True)
    return normalized


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


def matching_lines(content: str, patterns: Iterable[str]) -> list[str]:
    matched = []
    for line in content.splitlines():
        lower = line.lower()
        if any(pattern in lower for pattern in patterns):
            matched.append(line)
    return matched


def parse_timestamp(raw_timestamp: str | None) -> datetime | None:
    if not raw_timestamp:
        return None
    return parse_log_timestamp(raw_timestamp)


def timestamp_to_raw_value(timestamp: datetime) -> str:
    return timestamp_to_raw(timestamp)


def dedupe_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    deduped = []
    for item in items:
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped
