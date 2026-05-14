"""Common source filtering data contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SourceFilterContext:
    """Context shared by independent source filters."""

    anchor_dt: datetime | None = None
    primary_anchor: dict[str, Any] | None = None
    trace_anr_dt: datetime | None = None
    package_name: str | None = None


@dataclass(frozen=True)
class SourceFilterOptions:
    """Optional knobs for source filters."""

    package_name: str | None = None
    before_seconds: int | None = None
    after_seconds: int | None = None


@dataclass(frozen=True)
class SourceFilterResult:
    """Result returned by one source-specific filtering entrypoint."""

    source_kind: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
