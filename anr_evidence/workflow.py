"""Workflow orchestration for source-specific ANR filtering."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .constants import DEFAULT_WINDOWS
from .log_filter import LogFilterSpec, default_patterns_for_source, filter_timestamp_window
from .sources import (
    MeminfoFilterOptions,
    SourceFilterContext,
    SourceFilterOptions,
    SourceFilterResult,
    filter_event_log_source,
    filter_logcat_source,
    filter_meminfo_source,
    filter_trace_source,
)
from .sources.shared import build_evidence, parse_raw_timestamp, window_summary


@dataclass(frozen=True)
class FilterWorkflowOptions:
    """Workflow-level options for the source filtering pipeline."""

    package_name: str | None = None
    include_kernel: bool = True
    high_load_processes: tuple[str, ...] = ()
    high_load_pids: tuple[int, ...] = ()


@dataclass(frozen=True)
class FilterWorkflowResult:
    """Aggregated result from the source filtering workflow."""

    evidence: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    source_results: dict[str, SourceFilterResult] = field(default_factory=dict)
    context: SourceFilterContext = field(default_factory=SourceFilterContext)


def run_filter_workflow(
    package: dict[str, Any],
    anchor_summary: dict[str, Any],
    options: FilterWorkflowOptions | None = None,
) -> FilterWorkflowResult:
    """Run the trace -> EventLog -> logcat filtering workflow.

    The workflow keeps each source filter independent while preserving the
    legacy Phase 1 evidence order and warning behavior.
    """

    options = options or FilterWorkflowOptions()
    evidence: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    source_results: dict[str, SourceFilterResult] = {}
    primary_anchor = anchor_summary.get("primary_anchor")
    anchor_dt = parse_raw_timestamp(primary_anchor.get("timestamp") if primary_anchor else None)
    context = SourceFilterContext(
        anchor_dt=anchor_dt,
        primary_anchor=primary_anchor,
        package_name=options.package_name,
    )
    source_filters = {
        "trace": filter_trace_source,
        "event_log": filter_event_log_source,
        "logcat": filter_logcat_source,
        "kernel_log": _filter_kernel_log_source,
    }

    # meminfo is optional evidence and intentionally runs after logcat so
    # AnrManager Top CPU/IO findings can drive memory follow-up.
    source_order = ("trace", "event_log", "logcat", "meminfo", "kernel_log")
    for source_kind in source_order:
        source = package.get("sources", {}).get(source_kind)
        if not source:
            if source_kind == "meminfo":
                continue
            warnings.append({"code": "missing-source", "message": f"Source {source_kind} is missing from the package."})
            continue
        if not source.get("readable", True):
            warnings.append({"code": "source-read-warning", "message": f"Source {source_kind} was decoded with replacement characters."})
        if source_kind == "kernel_log" and not options.include_kernel:
            continue

        if source_kind == "meminfo":
            result = filter_meminfo_source(
                source,
                context,
                MeminfoFilterOptions(
                    package_name=options.package_name,
                    high_processes=options.high_load_processes,
                    high_pids=options.high_load_pids,
                ),
            )
        else:
            result = source_filters[source_kind](
                source,
                context,
                SourceFilterOptions(package_name=options.package_name),
            )
        source_results[source_kind] = result
        evidence.extend(result.evidence)
        warnings.extend(result.warnings)

    return FilterWorkflowResult(
        evidence=evidence,
        warnings=warnings,
        source_results=source_results,
        context=context,
    )


def _filter_kernel_log_source(
    source: dict[str, Any],
    context: SourceFilterContext,
    options: SourceFilterOptions | None = None,
) -> SourceFilterResult:
    del options
    spec = LogFilterSpec(
        source_kind="kernel_log",
        before_seconds=DEFAULT_WINDOWS["kernel_before_seconds"],
        after_seconds=DEFAULT_WINDOWS["kernel_after_seconds"],
        include_patterns=default_patterns_for_source("kernel_log"),
    )
    result = filter_timestamp_window(source.get("content", ""), context.anchor_dt, spec, fallback_label="kernel-full-fallback")
    selection, warnings = result.lines, result.warnings
    if not selection:
        return SourceFilterResult(source_kind="kernel_log", warnings=warnings)
    return SourceFilterResult(
        source_kind="kernel_log",
        evidence=[build_evidence(
            evidence_id="kernel_anchor_window",
            source_kind="kernel_log",
            tier="P0",
            extraction_mode="baseline" if context.anchor_dt else "fallback",
            rule_name="kernel-anchor-window",
            anchor=context.primary_anchor,
            source_path=source.get("path", "kernel_log"),
            content="\n".join(selection),
            time_window=window_summary(context.anchor_dt, "kernel_log"),
            label="kernel-anchor-window",
            warning_flags=[warning["code"] for warning in warnings],
        )],
        warnings=warnings,
        lines=selection,
    )
