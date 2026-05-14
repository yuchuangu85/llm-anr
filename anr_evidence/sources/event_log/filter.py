"""Independent EventLog filtering entrypoint."""

from __future__ import annotations

from typing import Any

from ...constants import DEFAULT_WINDOWS
from ...log_filter import LogFilterSpec, default_patterns_for_source, filter_preceding_anchor_window
from ..shared import SourceFilterContext, SourceFilterOptions, SourceFilterResult, build_evidence, window_summary


def filter_event_log_source(
    source: dict[str, Any],
    context: SourceFilterContext | None = None,
    options: SourceFilterOptions | None = None,
) -> SourceFilterResult:
    """Build baseline EventLog evidence from one event log source."""

    context = context or SourceFilterContext()
    options = options or SourceFilterOptions()
    content = source.get("content", "")
    spec = LogFilterSpec(
        source_kind="event_log",
        before_seconds=options.before_seconds if options.before_seconds is not None else DEFAULT_WINDOWS["event_log_before_seconds"],
        after_seconds=0,
        include_patterns=default_patterns_for_source("event_log"),
        package_name=options.package_name or context.package_name,
        package_filter_scope="anchor",
    )
    result = filter_preceding_anchor_window(content, "am_anr", spec)
    evidence: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = list(result.warnings)
    if not result.matched_anchor:
        selected = [line for line in content.splitlines() if line.strip()][: DEFAULT_WINDOWS["event_log_pre_lines"] + 1]
        if selected:
            evidence.append(build_evidence(
                evidence_id="event_log_leading_context",
                source_kind="event_log",
                tier="P0",
                extraction_mode="fallback",
                rule_name="event-log-leading-context",
                anchor=context.primary_anchor,
                source_path=source.get("path", "event_log"),
                content="\n".join(selected),
                time_window="leading-context",
                label="event-log-leading-context",
                warning_flags=[warning["code"] for warning in warnings],
            ))
        return SourceFilterResult(source_kind="event_log", evidence=evidence, warnings=warnings, lines=selected)

    evidence.append(build_evidence(
        evidence_id="event_am_anr",
        source_kind="event_log",
        tier="P0",
        extraction_mode="baseline",
        rule_name="event-log-am-anr",
        anchor=context.primary_anchor,
        source_path=source.get("path", "event_log"),
        content=result.matched_anchor,
        time_window="am_anr-line",
        label="event-log-am-anr",
    ))
    evidence.append(build_evidence(
        evidence_id="event_pre_window",
        source_kind="event_log",
        tier="P0",
        extraction_mode="baseline",
        rule_name="event-log-filtered-pre-window",
        anchor=context.primary_anchor,
        source_path=source.get("path", "event_log"),
        content="\n".join(result.lines),
        time_window=window_summary(context.anchor_dt, "event_log"),
        label="event-log-filtered-pre-window",
        warning_flags=[warning["code"] for warning in warnings],
    ))
    return SourceFilterResult(source_kind="event_log", evidence=evidence, warnings=warnings, lines=result.lines)
