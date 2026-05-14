"""Independent logcat filtering entrypoint."""

from __future__ import annotations

from typing import Any

from ...constants import DEFAULT_WINDOWS
from ...log_filter import LogFilterSpec, default_patterns_for_source, extract_anrmanager_block, filter_timestamp_window
from ..shared import SourceFilterContext, SourceFilterOptions, SourceFilterResult, build_evidence, window_summary


def filter_logcat_source(
    source: dict[str, Any],
    context: SourceFilterContext | None = None,
    options: SourceFilterOptions | None = None,
) -> SourceFilterResult:
    """Build baseline logcat evidence from one logcat source."""

    context = context or SourceFilterContext()
    options = options or SourceFilterOptions()
    spec = LogFilterSpec(
        source_kind="logcat",
        before_seconds=options.before_seconds if options.before_seconds is not None else DEFAULT_WINDOWS["logcat_before_seconds"],
        after_seconds=options.after_seconds if options.after_seconds is not None else DEFAULT_WINDOWS["logcat_after_seconds"],
        include_patterns=default_patterns_for_source("logcat"),
        package_name=options.package_name or context.package_name,
    )
    result = filter_timestamp_window(source.get("content", ""), context.anchor_dt, spec, fallback_label="logcat-full-fallback")
    selection, warnings = result.lines, result.warnings
    if not selection:
        return SourceFilterResult(source_kind="logcat", warnings=warnings)
    evidence = [build_evidence(
        evidence_id="logcat_anchor_window",
        source_kind="logcat",
        tier="P0",
        extraction_mode="baseline" if context.anchor_dt else "fallback",
        rule_name="logcat-anchor-window",
        anchor=context.primary_anchor,
        source_path=source.get("path", "logcat"),
        content="\n".join(selection),
        time_window=window_summary(context.anchor_dt, "logcat"),
        label="logcat-anchor-window",
        warning_flags=[warning["code"] for warning in warnings],
    )]
    return SourceFilterResult(source_kind="logcat", evidence=evidence, warnings=warnings, lines=selection)


def filter_logcat_anrmanager_block(
    source: dict[str, Any],
    context: SourceFilterContext | None = None,
    options: SourceFilterOptions | None = None,
) -> SourceFilterResult:
    """Extract AnrManager diagnostic block through the logcat source entrypoint."""

    context = context or SourceFilterContext()
    options = options or SourceFilterOptions()
    result = extract_anrmanager_block(
        source.get("content", ""),
        package_name=options.package_name or context.package_name,
        anchor_dt=context.anchor_dt,
    )
    return SourceFilterResult(
        source_kind="logcat",
        warnings=result.warnings,
        lines=result.lines,
        metadata={"matchedAnchor": result.matched_anchor, **result.metadata},
    )
