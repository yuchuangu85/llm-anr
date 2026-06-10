"""Shared helpers for Phase 1 extraction."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from typing import Any, Iterable
from uuid import uuid4

from ..log_filter import parse_log_timestamp, timestamp_to_raw
from ..sources.shared.evidence import build_evidence, window_summary

__all__ = [
    "build_evidence",
    "window_summary",
    "normalize_package",
    "matching_lines",
    "parse_timestamp",
    "timestamp_to_raw_value",
    "dedupe_dicts",
]


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
