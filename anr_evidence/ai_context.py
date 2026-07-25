"""Build grouped ANR markdown context and AI prompt artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import copy
import json
from pathlib import Path
import re
from typing import Any

from .am_anr import package_name_from_am_anr_line
from .anr_strategy import AnrTypeStrategy, strategy_for_package
from .anrmanager_parser import parse_anrmanager_block
from .constants import TIME_ANCHOR_PRECEDENCE
from .cross_source_fusion import fuse_cross_source_evidence
from .root_cause_hints import (
    infer_root_cause_pattern_hints_from_ids,
    infer_root_cause_pattern_hints_from_texts,
    merge_root_cause_pattern_hints,
    root_cause_hint_details,
)
from .log_filter import (
    LogFilterSpec,
    default_patterns_for_source,
    filter_known_anchor_window,
    filter_timestamp_windows,
    iter_text_lines,
    timestamped_context_before_windows,
    parse_log_timestamp,
    timestamp_to_raw,
)
from .sources import MeminfoFilterOptions, SourceFilterContext, SourceFilterOptions, filter_logcat_anrmanager_block, filter_meminfo_source
from .trace_preprocessor import preprocess_trace_content

ANRMANAGER_PRE_CONTEXT_SECONDS = 12


@dataclass(frozen=True)
class AiContextOptions:
    out_dir: str | Path | None = None
    event_before_seconds: int | None = None
    logcat_before_seconds: int | None = None
    logcat_after_seconds: int | None = None
    meminfo_before_seconds: int | None = None
    meminfo_after_seconds: int | None = None
    group_tolerance_seconds: int | None = None
    package_name: str | None = None
    anr_type: str | None = None
    build_ess: bool = False
    delta_t_normalized: bool = False


@dataclass(frozen=True)
class AiContextResult:
    """Side-effect-free AI context result shared by CLI and Web adapters."""

    package_id: str | None
    options: AiContextOptions
    strategy: dict[str, Any]
    groups: list[dict[str, Any]]
    events: list[dict[str, Any]]
    cache_markdown: str
    ai_prompt_markdown: str

    def summary(self, artifact_paths: dict[str, str] | None = None) -> dict[str, Any]:
        return {
            "packageId": self.package_id,
            "strategy": self.strategy,
            "artifactPaths": artifact_paths or {},
            "groupCount": len(self.groups),
            "events": self.events,
            "groups": [
                {
                    "id": group["id"],
                    "anchor": group.get("anchor"),
                    "fallbackUsed": group.get("fallbackUsed", False),
                    "strategy": group.get("strategy"),
                    "rootCausePatternHints": group.get("rootCausePatternHints", []),
                    "completeness": group["completeness"],
                }
                for group in self.groups
            ],
        }


def build_ai_context(package: dict[str, Any], options: AiContextOptions) -> AiContextResult:
    """Build AI context without writing files. Suitable for CLI and Web adapters."""

    strategy = strategy_for_package(package, options.anr_type)
    resolved = _resolve_options(options, strategy)
    events = [_event("source_loaded", "loaded", packageId=package.get("package_id"), sourceKinds=sorted(package.get("sources", {}).keys()))]
    events.append(_event("anr_type_selected", "completed", anrType=strategy.anr_type, label=strategy.label))
    groups, group_events = _build_groups(package, resolved, strategy)
    events.extend(group_events)
    cache_md = _render_cache_markdown(package, groups, resolved, strategy)
    events.append(_event("cache_rendered", "completed", groupCount=len(groups)))
    prompt_md = _render_ai_prompt(cache_md, groups, strategy)
    events.append(_event("prompt_generated", "completed", groupCount=len(groups)))
    return AiContextResult(
        package_id=package.get("package_id"),
        options=resolved,
        strategy=_strategy_summary(strategy),
        groups=groups,
        events=events,
        cache_markdown=cache_md,
        ai_prompt_markdown=prompt_md,
    )


def build_ai_context_artifacts(package: dict[str, Any], options: AiContextOptions) -> dict[str, Any]:
    """Create anr_analysis.md per ANR group and index.json for AI-assisted ANR analysis.

    Artifact generation intentionally avoids rendering a monolithic cache/prompt
    for every group before writing files.  Large multi-ANR inputs can retain
    megabytes of rendered markdown otherwise; rendering each group directly
    keeps peak memory proportional to one group plus the shared source package.
    """

    out_dir = Path(options.out_dir or "anr_ai_context")
    out_dir.mkdir(parents=True, exist_ok=True)

    strategy = strategy_for_package(package, options.anr_type)
    resolved = _resolve_options(AiContextOptions(
        out_dir=out_dir,
        event_before_seconds=options.event_before_seconds,
        logcat_before_seconds=options.logcat_before_seconds,
        logcat_after_seconds=options.logcat_after_seconds,
        meminfo_before_seconds=options.meminfo_before_seconds,
        meminfo_after_seconds=options.meminfo_after_seconds,
        group_tolerance_seconds=options.group_tolerance_seconds,
        package_name=options.package_name,
        anr_type=options.anr_type,
    ), strategy)
    events = [_event("source_loaded", "loaded", packageId=package.get("package_id"), sourceKinds=sorted(package.get("sources", {}).keys()))]
    events.append(_event("anr_type_selected", "completed", anrType=strategy.anr_type, label=strategy.label))
    groups, group_events = _build_groups(package, resolved, strategy)
    events.extend(group_events)

    # Clean stale legacy files from the top-level output directory.
    for stale in ("cache.md", "ai_prompt.md", "summary.json", "analysis.md"):
        stale_path = out_dir / stale
        if stale_path.exists():
            stale_path.unlink()
    # Clean stale legacy files from group subdirectories (upgrade migration).
    for child in out_dir.iterdir():
        if child.is_dir():
            for stale_name in ("cache.md", "ai_prompt.md", "analysis.md", "summary.json"):
                stale_child = child / stale_name
                if stale_child.exists():
                    stale_child.unlink()

    index_groups: list[dict[str, Any]] = []
    for group in groups:
        group_dir = out_dir / group["id"]
        group_dir.mkdir(parents=True, exist_ok=True)
        analysis_path = group_dir / "anr_analysis.md"
        logcat_path = group_dir / "logcat.txt"
        logcat_lines = group.get("logcat", {}).get("lines", [])
        logcat_path.write_text(("\n".join(logcat_lines) + "\n") if logcat_lines else "", encoding="utf-8")
        group_for_render = _with_logcat_artifact_reference(group, logcat_path.name)
        artifact_paths = {
            "analysis": str(analysis_path),
            "logcat": str(logcat_path),
        }
        existing = _read_existing_analyses(analysis_path) if analysis_path.exists() else {}
        evidence_md = _render_cache_markdown(package, [group_for_render], resolved, strategy, include_analysis_slots=True)
        anr_analysis_md = _render_ai_prompt(evidence_md, [group], strategy, evidence_analysis_md=evidence_md)
        if existing:
            anr_analysis_md = _merge_analyses(anr_analysis_md, existing)
        analysis_slots = _analysis_slot_statuses_from_text(anr_analysis_md)
        analysis_path.write_text(anr_analysis_md + "\n", encoding="utf-8")
        index_groups.append({
            "id": group["id"],
            "anchor": group.get("anchor"),
            "fallbackUsed": group.get("fallbackUsed", False),
            "completeness": group["completeness"],
            "rootCausePatternHints": group.get("rootCausePatternHints", []),
            "rootCausePatternHintDetails": group.get("rootCausePatternHintDetails", []),
            "analysisSlots": analysis_slots,
            "analysisComplete": all(status == "filled" for status in analysis_slots.values()),
            "warningCount": _group_warning_count(group),
            "artifactPaths": artifact_paths,
            "anrManager": {
                "anchor": group.get("anrManager", {}).get("anchor"),
                "metadata": group.get("anrManager", {}).get("metadata", {}),
            },
        })

    events.append(_event("cache_rendered", "completed", groupCount=len(groups)))
    events.append(_event("prompt_generated", "completed", groupCount=len(groups)))
    index_path = out_dir / "index.json"
    index = {
        "packageId": package.get("package_id"),
        "strategy": _strategy_summary(strategy),
        "artifactPaths": {"index": str(index_path)},
        "groupCount": len(groups),
        "events": events,
        "groups": index_groups,
    }
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return index

def _single_group_summary(result: AiContextResult, group: dict[str, Any], artifact_paths: dict[str, str]) -> dict[str, Any]:
    return {
        "packageId": result.package_id,
        "strategy": result.strategy,
        "artifactPaths": artifact_paths,
        "groupCount": 1,
        "events": [
            event
            for event in result.events
            if event.get("details", {}).get("groupId") in {None, group["id"]}
        ],
        "groups": [
            {
                "id": group["id"],
                "anchor": group.get("anchor"),
                "fallbackUsed": group.get("fallbackUsed", False),
                "strategy": group.get("strategy"),
                "rootCausePatternHints": group.get("rootCausePatternHints", []),
                "completeness": group["completeness"],
            }
        ],
    }


def _group_warning_count(group: dict[str, Any]) -> int:
    return sum(
        len(group.get(section, {}).get("warnings", []))
        for section in ("trace", "eventLog", "logcat", "anrManager", "meminfo")
    )


def _with_logcat_artifact_reference(group: dict[str, Any], filename: str) -> dict[str, Any]:
    """Return a shallow render copy that points Markdown to external logcat evidence."""

    rendered = dict(group)
    logcat = dict(group.get("logcat", {}))
    logcat["artifactFilename"] = filename
    rendered["logcat"] = logcat
    return rendered


def _resolve_options(options: AiContextOptions, strategy: AnrTypeStrategy) -> AiContextOptions:
    return AiContextOptions(
        out_dir=options.out_dir,
        event_before_seconds=options.event_before_seconds if options.event_before_seconds is not None else strategy.event_before_seconds,
        logcat_before_seconds=options.logcat_before_seconds if options.logcat_before_seconds is not None else strategy.logcat_before_seconds,
        logcat_after_seconds=options.logcat_after_seconds if options.logcat_after_seconds is not None else strategy.logcat_after_seconds,
        meminfo_before_seconds=options.meminfo_before_seconds,
        meminfo_after_seconds=options.meminfo_after_seconds,
        group_tolerance_seconds=options.group_tolerance_seconds if options.group_tolerance_seconds is not None else strategy.group_tolerance_seconds,
        package_name=options.package_name,
        anr_type=strategy.anr_type,
    )


def _build_groups(package: dict[str, Any], options: AiContextOptions, strategy: AnrTypeStrategy) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources = package.get("sources", {})
    events: list[dict[str, Any]] = []
    anchors = _event_anchors(sources.get("event_log", {}).get("content", ""), options.package_name)
    fallback_used = False
    if not anchors and not options.package_name:
        fallback_used = True
        anchors = _fallback_anchors(sources, options.package_name, strategy)
    if not anchors:
        trace = _trace_context(sources.get("trace"), None, package_name=options.package_name)
        inferred_anr_dt = _trace_selected_timestamp(trace)
        warning = (
            {"code": "target-am-anr-not-found", "message": f"No EventLog am_anr line matched package `{options.package_name}`."}
            if options.package_name
            else {"code": "missing-anchor", "message": "No ANR anchor was found."}
        )
        root_cause_hints = _root_cause_pattern_hints_for_group(
            trace,
            [],
            [],
            [],
            None,
            [],
        )
        group = {
            "id": _unanchored_group_id(inferred_anr_dt),
            "anchor": None,
            "inferredAnrTime": timestamp_to_raw(inferred_anr_dt) if inferred_anr_dt else None,
            "inferredAnrTimeSource": "trace" if inferred_anr_dt else None,
            "fallbackUsed": fallback_used,
            "strategy": _strategy_summary(strategy),
            "rootCausePatternHints": root_cause_hints,
            "rootCausePatternHintDetails": root_cause_hint_details(root_cause_hints),
            "trace": trace,
            "eventLog": {"lines": [], "warnings": [warning]},
            "logcat": {"lines": [], "warnings": [warning]},
            "completeness": _completeness(sources, trace.get("lines", []), [], None),
        }
        events.extend(_group_step_events(group))
        return [group], events

    anchors = _collapse_nearby_anchors(anchors, options.group_tolerance_seconds)
    events.append(_event("grouped", "completed", groupCount=len(anchors), fallbackUsed=fallback_used))
    groups = []
    # Anchor-independent work hoisted out of the per-anchor loop.  Trace
    # preprocessing is shared via cache, and logcat timestamp-window filtering
    # is done as a single streaming pass over the source text instead of
    # materializing a large (timestamp, line) tuple table.
    trace_preprocess_cache: dict[Any, Any] = {}
    kernel_log_content = sources.get("kernel_log", {}).get("content", "")
    kernel_log_lines = list(iter_text_lines(kernel_log_content)) if kernel_log_content else []
    logcat_content = sources.get("logcat", {}).get("content", "")
    effective_package_names = [options.package_name or anchor.get("packageName") for anchor in anchors]
    logcat_results = filter_timestamp_windows(
        logcat_content,
        [
            (
                anchor["timestamp"],
                LogFilterSpec(
                    source_kind="logcat",
                    before_seconds=options.logcat_before_seconds,
                    after_seconds=options.logcat_after_seconds,
                    include_patterns=strategy.logcat_patterns,
                    package_name=effective_package_names[index],
                    package_filter_scope="system_or_package",
                ),
                "logcat-ai-context",
            )
            for index, anchor in enumerate(anchors)
        ],
    )
    anrmanager_results = [
        filter_logcat_anrmanager_block(
            sources.get("logcat", {}),
            SourceFilterContext(anchor_dt=anchor["timestamp"], package_name=effective_package_names[index]),
            SourceFilterOptions(package_name=effective_package_names[index]),
        )
        for index, anchor in enumerate(anchors)
    ]
    anrmanager_pre_context_anchors = [
        _anrmanager_pre_context_anchor(result.metadata)
        for result in anrmanager_results
    ]
    anrmanager_pre_contexts = timestamped_context_before_windows(
        logcat_content,
        anrmanager_pre_context_anchors,
        ANRMANAGER_PRE_CONTEXT_SECONDS,
    )

    for index, anchor in enumerate(anchors):
        anchor_dt = anchor["timestamp"]
        effective_package_name = effective_package_names[index]
        event_lines, event_warnings = _event_window(sources.get("event_log", {}).get("content", ""), anchor, options, strategy)
        trace = _trace_context(sources.get("trace"), anchor_dt, package_name=effective_package_name, cache=trace_preprocess_cache)
        logcat_result = logcat_results[index]

        # Extract the AnrManager diagnostic block (memory pressure, CPU usage,
        # AnrDumpRecord, addErrorToDropBox) for the target or inferred package.
        # Merge raw logcat context immediately preceding the selected
        # AnrManager dump, then the AnrManager block, then the generic
        # signal-filtered anchor window.  The generic window is intentionally
        # pattern/package-filtered, so without this explicit pre-context slice
        # ordinary framework/vendor lines in the 12s before AnrManager can be
        # lost even though they explain the dump trigger.
        anrmanager_result = anrmanager_results[index]
        anrmanager_lines = anrmanager_result.lines
        anrmanager_pre_context_anchor = anrmanager_pre_context_anchors[index]
        anrmanager_pre_context_lines = anrmanager_pre_contexts[index]
        anrmanager_summary = parse_anrmanager_block(anrmanager_lines) if anrmanager_lines else None
        meminfo_result = filter_meminfo_source(
            sources.get("meminfo", {}),
            SourceFilterContext(anchor_dt=anchor_dt, package_name=effective_package_name),
            MeminfoFilterOptions(
                package_name=effective_package_name,
                high_processes=_top_process_names_from_anrmanager(anrmanager_summary),
                high_pids=_top_pids_from_anrmanager(anrmanager_summary),
                top_n=5,
                include_all_snapshots=False,
                window_before_seconds=options.meminfo_before_seconds if options.meminfo_before_seconds is not None else 5,
                window_after_seconds=options.meminfo_after_seconds if options.meminfo_after_seconds is not None else 5,
            ),
        ) if sources.get("meminfo", {}).get("content") else None
        merged_logcat_lines = _dedupe_lines(
            anrmanager_pre_context_lines,
            anrmanager_lines,
            logcat_result.lines,
        )
        logcat_metadata = {
            "anrManagerPreContextSeconds": ANRMANAGER_PRE_CONTEXT_SECONDS,
            "anrManagerPreContextAnchor": timestamp_to_raw(anrmanager_pre_context_anchor) if anrmanager_pre_context_anchor else None,
            "anrManagerPreContextRetainedLineCount": len(anrmanager_pre_context_lines),
        }

        # Cross-source confidence fusion: promote trace/AnrManager hints
        # whose findings are corroborated by logcat / event_log / kernel
        # log signals (e.g. MAIN_BINDER_WAIT_REPLY + "Slow binder
        # transaction" -> critical).
        if trace.get("traceHints"):
            trace["traceHints"] = fuse_cross_source_evidence(
                trace["traceHints"],
                logcat_text="\n".join(merged_logcat_lines),
                event_log_text="\n".join(event_lines),
                kernel_log_text=kernel_log_content,
            )

        root_cause_hints = _root_cause_pattern_hints_for_group(
            trace,
            event_lines,
            merged_logcat_lines,
            kernel_log_lines,
            anrmanager_summary,
            meminfo_result.lines if meminfo_result else [],
        )

        group = {
            "id": _group_id(anchor_dt),
            "anchor": {
                "sourceKind": anchor["sourceKind"],
                "timestamp": timestamp_to_raw(anchor_dt),
                "line": anchor["line"],
                "packageName": effective_package_name,
            },
            "fallbackUsed": fallback_used or anchor.get("fallback", False),
            "strategy": _strategy_summary(strategy),
            "rootCausePatternHints": root_cause_hints,
            "rootCausePatternHintDetails": root_cause_hint_details(root_cause_hints),
            "trace": trace,
            "eventLog": {"lines": event_lines, "warnings": event_warnings},
            "logcat": {"lines": merged_logcat_lines, "warnings": logcat_result.warnings, "metadata": logcat_metadata},
            "anrManager": {
                "lines": anrmanager_lines,
                "warnings": anrmanager_result.warnings,
                "anchor": anrmanager_result.metadata.get("matchedAnchor"),
                "metadata": anrmanager_result.metadata,
                "summary": anrmanager_summary,
            },
            "meminfo": {
                "lines": meminfo_result.lines if meminfo_result else [],
                "warnings": meminfo_result.warnings if meminfo_result else [],
                "metadata": meminfo_result.metadata if meminfo_result else {},
            },
            "completeness": _completeness(sources, trace.get("lines", []), merged_logcat_lines, event_lines, meminfo_result.lines if meminfo_result else []),
        }
        groups.append(group)
        events.extend(_group_step_events(group))
    return groups, events


def _root_cause_pattern_hints_for_group(
    trace: dict[str, Any],
    event_lines: list[str],
    logcat_lines: list[str],
    kernel_lines: list[str],
    anrmanager_summary: dict[str, Any] | None,
    meminfo_lines: list[str],
) -> list[str]:
    structured_hints = list(trace.get("deadlockHints", []) or [])
    structured_hints.extend(trace.get("traceHints", []) or [])
    if anrmanager_summary:
        structured_hints.extend(anrmanager_summary.get("derivedHints", []) or [])
    return merge_root_cause_pattern_hints(
        infer_root_cause_pattern_hints_from_ids(structured_hints),
        infer_root_cause_pattern_hints_from_texts(
            [
                "\n".join(trace.get("lines", []) or []),
                "\n".join(event_lines),
                "\n".join(logcat_lines),
                "\n".join(kernel_lines),
                "\n".join(meminfo_lines),
            ]
        ),
    )


def _group_step_events(group: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _event("trace_filtered", "completed", groupId=group["id"], retainedLineCount=len(group["trace"].get("lines", [])), warningCount=len(group["trace"].get("warnings", []))),
        _event("event_log_filtered", "completed", groupId=group["id"], retainedLineCount=len(group["eventLog"].get("lines", [])), warningCount=len(group["eventLog"].get("warnings", []))),
        _event("logcat_filtered", "completed", groupId=group["id"], retainedLineCount=len(group["logcat"].get("lines", [])), warningCount=len(group["logcat"].get("warnings", []))),
        _event("meminfo_filtered", "completed", groupId=group["id"], retainedLineCount=len(group.get("meminfo", {}).get("lines", [])), warningCount=len(group.get("meminfo", {}).get("warnings", []))),
        _event("completeness_checked", "completed", groupId=group["id"], complete=group["completeness"].get("complete")),
    ]


def _anrmanager_pre_context_anchor(metadata: dict[str, Any]) -> datetime | None:
    """Prefer the AnrManager block start as the raw-log context anchor."""

    for key in ("blockStartTimestamp", "anchorTimestamp"):
        value = metadata.get(key)
        if isinstance(value, str):
            parsed = parse_log_timestamp(value)
            if parsed is not None:
                return parsed
    return None


def _dedupe_lines(*line_groups: list[str]) -> list[str]:
    """Merge line groups while preserving first occurrence order."""

    merged: list[str] = []
    seen: set[str] = set()
    for group in line_groups:
        for line in group:
            normalized = line.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
    return merged


def _top_process_names_from_anrmanager(summary: dict[str, Any] | None, limit: int = 5) -> tuple[str, ...]:
    if not summary:
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for proc in summary.get("highCpuProcessesOver90") or []:
        name = proc.get("processName")
        if name and str(name) not in seen:
            names.append(str(name))
            seen.add(str(name))
    for proc in (summary.get("cpuTopProcesses") or [])[:limit]:
        name = proc.get("processName")
        if name and str(name) not in seen:
            names.append(str(name))
            seen.add(str(name))
    return tuple(names)


def _top_pids_from_anrmanager(summary: dict[str, Any] | None, limit: int = 5) -> tuple[int, ...]:
    if not summary:
        return ()
    pids: list[int] = []
    seen: set[int] = set()
    for proc in summary.get("highCpuProcessesOver90") or []:
        pid = proc.get("pid")
        if pid is not None and int(pid) not in seen:
            pids.append(int(pid))
            seen.add(int(pid))
    for proc in (summary.get("cpuTopProcesses") or [])[:limit]:
        pid = proc.get("pid")
        if pid is not None and int(pid) not in seen:
            pids.append(int(pid))
            seen.add(int(pid))
    return tuple(pids)


def _event(step: str, status: str, **details: Any) -> dict[str, Any]:
    return {"step": step, "status": status, "details": details}


def _collapse_nearby_anchors(anchors: list[dict[str, Any]], tolerance_seconds: int) -> list[dict[str, Any]]:
    if not anchors:
        return []
    ordered = sorted(anchors, key=lambda anchor: anchor["timestamp"])
    collapsed = [ordered[0]]
    tolerance = timedelta(seconds=tolerance_seconds)
    for anchor in ordered[1:]:
        previous = collapsed[-1]
        if (
            anchor["timestamp"] - previous["timestamp"] <= tolerance
            and anchor.get("dedupeKey")
            and anchor.get("dedupeKey") == previous.get("dedupeKey")
        ):
            continue
        collapsed.append(anchor)
    return collapsed


def _event_anchors(content: str, package_name: str | None) -> list[dict[str, Any]]:
    anchors = []
    for index, line in enumerate(iter_text_lines(content)):
        if "am_anr" not in line.lower():
            continue
        if package_name and package_name not in line:
            continue
        ts = parse_log_timestamp(line)
        if ts is None:
            continue
        inferred_package_name = package_name_from_am_anr_line(line)
        anchors.append({
            "sourceKind": "event_log",
            "timestamp": ts,
            "line": line.strip(),
            "lineIndex": index,
            "dedupeKey": _anchor_dedupe_key(line),
            "packageName": package_name or inferred_package_name,
        })
    return anchors


def _fallback_anchors(sources: dict[str, Any], package_name: str | None, strategy: AnrTypeStrategy) -> list[dict[str, Any]]:
    predicates = strategy.fallback_anchor_patterns
    for source_kind in TIME_ANCHOR_PRECEDENCE:
        if source_kind == "event_log":
            continue
        source = sources.get(source_kind)
        if not source:
            continue
        anchors = []
        for index, line in enumerate(iter_text_lines(source.get("content", ""))):
            if package_name and package_name not in line:
                continue
            ts = parse_log_timestamp(line)
            if ts is None:
                continue
            lower = line.lower()
            if any(pattern in lower for pattern in predicates.get(source_kind, ())):
                anchors.append({
                    "sourceKind": source_kind,
                    "timestamp": ts,
                    "line": line.strip(),
                    "lineIndex": index,
                    "fallback": True,
                    "dedupeKey": _anchor_dedupe_key(line),
                })
        if anchors:
            return anchors
    return []


def _anchor_dedupe_key(line: str) -> str:
    without_ts = re.sub(r"\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d{3}", "", line)
    return re.sub(r"\s+", " ", without_ts).strip().lower()


def _event_window(content: str, anchor: dict[str, Any], options: AiContextOptions, strategy: AnrTypeStrategy) -> tuple[list[str], list[dict[str, str]]]:
    if not content:
        return [], [{"code": "missing-event-log", "message": "EventLog source is missing."}]
    # EventLog filtering follows docs/hermes-gemma-algorithm-design.md: anchor on the target
    # ``am_anr`` line, then retain every documented EventLog tag in the
    # preceding 12s window.  The package filter applies to anchor discovery,
    # not to contextual pre-window lines, because lifecycle/focus evidence may
    # belong to system_server, the next app, or other processes.
    result = filter_known_anchor_window(
        content,
        anchor_line=anchor["line"],
        anchor_dt=anchor["timestamp"],
        anchor_line_index=anchor.get("lineIndex"),
        spec=LogFilterSpec(
            source_kind="event_log",
            before_seconds=options.event_before_seconds,
            include_patterns=default_patterns_for_source("event_log"),
            package_filter_scope="anchor",
        ),
    )
    return result.lines, result.warnings


def _trace_context(
    source: dict[str, Any] | None,
    anchor_dt: datetime | None,
    *,
    package_name: str | None = None,
    cache: dict[Any, Any] | None = None,
) -> dict[str, Any]:
    if not source or not source.get("content"):
        return {
            "lines": [],
            "warnings": [{"code": "missing-trace", "message": "Trace source is missing."}],
            "metadata": {},
            "lockGraph": None,
            "deadlockHints": [],
        }
    preprocessed = preprocess_trace_content(
        source.get("content", ""),
        anchor_timestamp=timestamp_to_raw(anchor_dt) if anchor_dt else None,
        process_name=package_name,
        cache=cache,
    )
    deadlock_hints = copy.deepcopy(preprocessed.get("deadlockHints", []) or [])
    trace_hints = copy.deepcopy(preprocessed.get("traceHints", []) or [])
    lock_graph = copy.deepcopy(preprocessed.get("lockGraph")) if preprocessed.get("lockGraph") else None
    compacted_lines = list(preprocessed.get("compactedLines", []))
    return {
        "lines": compacted_lines,
        "warnings": [],
        "metadata": _trace_metadata(source, preprocessed, lock_graph, deadlock_hints, trace_hints, compacted_lines, anchor_dt),
        "lockGraph": lock_graph,
        "deadlockHints": deadlock_hints,
        "traceHints": trace_hints,
    }


def _trace_metadata(
    source: dict[str, Any],
    preprocessed: dict[str, Any],
    lock_graph: dict[str, Any] | None,
    deadlock_hints: list[dict[str, Any]],
    trace_hints: list[dict[str, Any]],
    compacted_lines: list[str],
    anchor_dt: datetime | None,
) -> dict[str, Any]:
    """Build AI-visible structured trace metadata.

    The raw trace block remains the baseline evidence.  This metadata is a
    compact field-by-field reading aid modelled after ``ANR-trace文件分析.md``:
    it exposes thread header fields, scheduling context, stack summaries and
    owner/peer hints so the analysis can be as detailed as a manual trace read
    without forcing the model to rediscover every value from free text.
    """

    selected_dt = _first_timestamp(compacted_lines)
    delta_ms = None
    if anchor_dt and selected_dt:
        delta_ms = int((selected_dt - anchor_dt).total_seconds() * 1000)
    thread_summary = preprocessed.get("threadSummary") or {}
    return {
        "sourcePath": source.get("path", "trace"),
        "sectionCount": preprocessed.get("sectionCount"),
        "selectedSectionIndex": preprocessed.get("selectedSectionIndex"),
        "selectedSectionTimestamp": timestamp_to_raw(selected_dt) if selected_dt else None,
        "anchorTimestamp": timestamp_to_raw(anchor_dt) if anchor_dt else None,
        "selectedSectionDeltaFromAnchorMs": delta_ms,
        "processName": preprocessed.get("processName"),
        "pid": preprocessed.get("pid"),
        "mainThread": _summarize_thread(preprocessed.get("primaryThread")),
        "ownerThread": _summarize_thread(preprocessed.get("ownerThread")),
        "threadSummary": {
            "threadCount": thread_summary.get("threadCount"),
            "suspiciousThreadCount": thread_summary.get("suspiciousThreadCount"),
            "mainThreadBlocked": thread_summary.get("mainThreadBlocked"),
            "lockContentionDetected": thread_summary.get("lockContentionDetected"),
            "ownerThreadTid": thread_summary.get("ownerThreadTid"),
            "ownerThreadName": thread_summary.get("ownerThreadName"),
            "dominantBlockHint": thread_summary.get("dominantBlockHint"),
            "threadStateCounts": thread_summary.get("threadStateCounts", {}),
            "blockHintCounts": thread_summary.get("blockHintCounts", {}),
        },
        "cpuSummary": preprocessed.get("cpuSummary"),
        "binderSummary": preprocessed.get("binderSummary"),
        "renderSummary": preprocessed.get("renderSummary"),
        "suspendSummary": preprocessed.get("suspendSummary"),
        "suspiciousThreads": [_summarize_thread(thread) for thread in (preprocessed.get("suspiciousThreads", []) or [])[:5]],
        "deadlockHintCount": len(deadlock_hints),
        "deadlockCycleCount": len((lock_graph or {}).get("cycles", []) or []),
        "traceHintCount": len(trace_hints),
    }


def _first_timestamp(lines: list[str]) -> datetime | None:
    for line in lines:
        ts = parse_log_timestamp(line)
        if ts:
            return ts
    return None


def _summarize_thread(thread: dict[str, Any] | None) -> dict[str, Any] | None:
    if not thread:
        return None
    return {
        "threadName": thread.get("threadName"),
        "tid": thread.get("tid"),
        "prio": thread.get("prio"),
        "daemon": thread.get("daemon"),
        "artThreadState": thread.get("artThreadState"),
        "javaThreadState": thread.get("javaThreadState"),
        "sysTid": thread.get("sysTid"),
        "group": thread.get("group"),
        "sCount": thread.get("sCount"),
        "dsCount": thread.get("dsCount"),
        "ucsCount": thread.get("ucsCount"),
        "flags": thread.get("flags"),
        "obj": thread.get("obj"),
        "self": thread.get("self"),
        "nice": thread.get("nice"),
        "cgrp": thread.get("cgrp"),
        "sched": thread.get("sched"),
        "handle": thread.get("handle"),
        "linuxState": thread.get("linuxState"),
        "schedstat": thread.get("schedstat"),
        "schedstatParsed": thread.get("schedstatParsed"),
        "utm": thread.get("utm"),
        "stm": thread.get("stm"),
        "core": thread.get("core"),
        "hz": thread.get("hz"),
        "heldMutexes": thread.get("heldMutexes"),
        "threadState": thread.get("threadState"),
        "blockHint": thread.get("blockHint"),
        "binderCallKind": thread.get("binderCallKind"),
        "binderDriverFrame": thread.get("binderDriverFrame"),
        "renderCallKind": thread.get("renderCallKind"),
        "renderDriverFrame": thread.get("renderDriverFrame"),
        "waitObject": thread.get("waitObject"),
        "lockOwnerTid": thread.get("lockOwnerTid"),
        "heldLocks": thread.get("heldLocks"),
        "waitingLocks": thread.get("waitingLocks"),
        "nativeTopFrame": thread.get("nativeTopFrame"),
        "javaTopFrame": thread.get("javaTopFrame"),
        "looperFrame": thread.get("looperFrame"),
        "suspicionScore": thread.get("suspicionScore"),
        "suspicionReasons": thread.get("suspicionReasons"),
    }


def _completeness(
    sources: dict[str, Any],
    trace_lines: list[str],
    logcat_lines: list[str],
    event_lines: list[str] | None,
    meminfo_lines: list[str] | None = None,
) -> dict[str, Any]:
    required = {
        "trace": bool(sources.get("trace", {}).get("content")),
        "event_log": bool(sources.get("event_log", {}).get("content")),
        "logcat": bool(sources.get("logcat", {}).get("content")),
    }
    retained = {
        "trace": bool(trace_lines),
        "event_log": bool(event_lines),
        "logcat": bool(logcat_lines),
    }
    missing_sources = [kind for kind, available in required.items() if not available]
    empty_filtered = [kind for kind, has_lines in retained.items() if required.get(kind) and not has_lines]
    complete = not missing_sources and not empty_filtered
    return {
        "complete": complete,
        "missingSources": missing_sources,
        "emptyFilteredSources": empty_filtered,
        "retainedLineCounts": {
            kind: len(lines)
            for kind, lines in {
                "trace": trace_lines,
                "event_log": event_lines or [],
                "logcat": logcat_lines,
                "meminfo": meminfo_lines or [],
            }.items()
        },
    }


_ANALYSIS_SLOT_BOUNDARIES = {
    "trace": "\n### EventLog 事件日志",
    "eventlog": "\n### Logcat 系统日志",
    "logcat": "\n### 完整性检查",
    "logcat-anrmanager": "\n### 完整性检查",
    "final-anr": None,
}
_ANALYSIS_SLOT_IDS = ("trace", "eventlog", "logcat-anrmanager", "final-anr")


def _analysis_slot_body_span(text: str, slot_id: str) -> tuple[int, int] | None:
    marker = f"<!-- AI_ANALYSIS_SLOT:{slot_id} -->"
    marker_index = text.find(marker)
    if marker_index < 0:
        return None
    start = marker_index + len(marker)
    if start < len(text) and text[start] == "\n":
        start += 1
    boundary = _ANALYSIS_SLOT_BOUNDARIES.get(slot_id)
    if boundary is None:
        end = len(text)
    else:
        boundary_index = text.find(boundary, start)
        end = boundary_index if boundary_index >= 0 else len(text)
    return start, end


def _read_existing_analyses_from_text(text: str) -> dict[str, str]:
    """Extract filled AI analysis slot bodies from rendered Markdown.

    The final analysis slot is expected to contain Markdown headings such as
    ``## Timeline``.  Parsing by "next heading" would therefore drop the most
    important conclusion during regeneration.  Instead, use the known staged
    workflow boundaries between Trace/EventLog/Logcat/Final slots.
    """

    results: dict[str, str] = {}
    for slot_id in (*_ANALYSIS_SLOT_IDS, "logcat"):
        span = _analysis_slot_body_span(text, slot_id)
        if not span:
            continue
        start, end = span
        body = text[start:end].strip()
        # Check if the body is still a template (unfilled) by looking for template markers
        is_template = (
            "_Pending AI analysis" in body
            or "待 AI 分析填写" in body
            or "_请用" in body  # New template format markers
            or "_列出" in body
            or "_评估" in body
        )
        if body and not is_template:
            results[slot_id] = body
    return results


def _analysis_slot_statuses_from_text(text: str) -> dict[str, str]:
    filled = _read_existing_analyses_from_text(text)
    return {
        slot_id: "filled" if slot_id in filled else "pending"
        for slot_id in _ANALYSIS_SLOT_IDS
    }


def _read_existing_analyses(filepath: Path) -> dict[str, str]:
    """Extract existing AI analysis content from a previously generated file.

    Returns a dict mapping slot_id (e.g. ``trace``) to the analysis text
    below the ``<!-- AI_ANALYSIS_SLOT:... -->`` marker.  Slots still showing
    the pending placeholder are treated as empty.
    """
    if not filepath.exists():
        return {}
    text = filepath.read_text(encoding="utf-8")
    return _read_existing_analyses_from_text(text)


def _merge_analyses(new_content: str, existing: dict[str, str]) -> str:
    """Replace pending placeholders in *new_content* with preserved analysis text."""
    # Preserve user-filled analysis from the previous per-source layout when
    # regenerating into the fixed four-stage workflow.
    if "logcat-anrmanager" not in existing and "logcat" in existing:
        existing["logcat-anrmanager"] = existing["logcat"]
    for slot_id, body in existing.items():
        span = _analysis_slot_body_span(new_content, slot_id)
        if not span:
            continue
        start, end = span
        new_content = new_content[:start] + body.rstrip() + "\n\n" + new_content[end:].lstrip("\n")
    return new_content


def _render_cache_markdown(
    package: dict[str, Any],
    groups: list[dict[str, Any]],
    options: AiContextOptions,
    strategy: AnrTypeStrategy,
    *,
    include_analysis_slots: bool = False,
    document_title: str = "ANR AI Context Cache",
) -> str:
    lines = [
        f"# {document_title}",
        "",
        "## 运行元数据",
        f"- Package: `{package.get('package_id', 'unknown')}`",
        f"- ANR type strategy: `{strategy.anr_type}` ({strategy.label})",
        f"- Event window: `{options.event_before_seconds}s-before/0s-after`",
        f"- Logcat window: `{options.logcat_before_seconds}s-before/{options.logcat_after_seconds}s-after`",
        f"- Group tolerance: `{options.group_tolerance_seconds}s`",
        f"- Package filter: `{options.package_name or 'none'}`",
        "",
    ]
    for group in groups:
        anchor = group.get("anchor")
        title = group["id"]
        lines.extend([f"## {title}", ""])
        if anchor:
            lines.extend([
                "### 锚点",
                f"- Source: `{anchor['sourceKind']}`",
                f"- Timestamp: `{anchor['timestamp']}`",
                f"- Line: `{anchor['line']}`",
                f"- Fallback anchor: `{group.get('fallbackUsed', False)}`",
                f"- Strategy: `{group.get('strategy', {}).get('anrType', strategy.anr_type)}`",
            ])
            _append_root_cause_pattern_hint_section(lines, group.get("rootCausePatternHintDetails", []))
            lines.append("")
        else:
            lines.extend(["### 锚点", "- 未找到匹配的 EventLog `am_anr` 锚点。"])
            if group.get("inferredAnrTime"):
                lines.extend([
                    f"- Inferred ANR time: `{group['inferredAnrTime']}` (source: `{group.get('inferredAnrTimeSource', 'unknown')}`)",
                    "- Folder id uses this inferred ANR time to avoid a timeless `anr-unanchored` directory.",
                ])
            _append_root_cause_pattern_hint_section(lines, group.get("rootCausePatternHintDetails", []))
            lines.append("")
        _append_deadlock_section(lines, group["trace"].get("lockGraph"), group["trace"].get("deadlockHints", []))
        _append_trace_hints_section(lines, group["trace"].get("traceHints", []), group["trace"].get("deadlockHints", []))
        annotated_trace_lines = _inject_hint_markers(
            group["trace"].get("lines", []),
            group["trace"].get("deadlockHints", []),
        )
        _append_section(lines, "Trace 堆栈", annotated_trace_lines, group["trace"].get("warnings", []))
        if include_analysis_slots:
            _append_analysis_slot(
                lines,
                "Trace 堆栈",
                "trace",
                "anr-trace-analysis",
                [
                    "只分析 Trace / 死锁检测 / Trace 线索。",
                    "输出 main thread、直接 trace blocker、相关线程/hints、Trace-only conclusion、gaps、confidence。",
                ],
            )
        _append_section(lines, "EventLog 事件日志", group["eventLog"].get("lines", []), group["eventLog"].get("warnings", []))
        if include_analysis_slots:
            _append_analysis_slot(
                lines,
                "EventLog 事件日志",
                "eventlog",
                "anr-eventlog-analysis",
                [
                    "只分析 EventLog 与 Anchor；按 am_anr 基准计算 ΔT 并解释 pre-ANR tag 序列。",
                    "输出 Anchor/am_anr、pre-ANR sequence、state-machine interpretation、EventLog-only conclusion、gaps、confidence。",
                ],
            )
        logcat_artifact = group["logcat"].get("artifactFilename")
        if include_analysis_slots and logcat_artifact:
            _append_logcat_artifact_reference(
                lines,
                logcat_artifact,
                group["logcat"].get("lines", []),
                group["logcat"].get("warnings", []),
                group["logcat"].get("metadata", {}),
            )
        else:
            _append_section(
                lines,
                "Logcat 系统日志",
                group["logcat"].get("lines", []),
                group["logcat"].get("warnings", []),
            )
        anrmanager = group.get("anrManager")
        if anrmanager and anrmanager.get("lines"):
            _append_anrmanager_summary(lines, anrmanager.get("summary"))
        meminfo = group.get("meminfo")
        if meminfo and meminfo.get("lines"):
            _append_section(lines, "Meminfo 目标/高负载跟进", meminfo["lines"], meminfo.get("warnings", []))
        if include_analysis_slots:
            _append_analysis_slot(
                lines,
                "Logcat / AnrManager",
                "logcat-anrmanager",
                "anr-logcat-analysis",
                [
                    "先读取同目录 `logcat.txt` 中的过滤后 Logcat，再结合 AnrManager 摘要 与 Meminfo 目标/高负载跟进。",
                    "输出 trigger line、dump lifecycle、window/focus/surface sequence、load/PSI/meminfo、Logcat-only conclusion、gaps、confidence。",
                ],
            )
        completeness = group["completeness"]
        lines.extend([
            "### 完整性检查",
            f"- 完整: `{completeness['complete']}`",
            f"- 缺失来源: `{', '.join(completeness['missingSources']) or 'none'}`",
            f"- 空过滤来源: `{', '.join(completeness['emptyFilteredSources']) or 'none'}`",
            f"- 保留行数: `{completeness['retainedLineCounts']}`",
            "",
        ])
        if include_analysis_slots:
            _append_analysis_slot(
                lines,
                "最终 ANR 综合分析",
                "final-anr",
                "anr-analysis",
                [
                    "仅在 Trace、EventLog、Logcat/AnrManager 三段专项分析完成后填写。",
                    "综合分析必须写回本槽位；按固定步骤输出 综合分析结论、Timeline、Direct blocking point、Candidate root-cause chains、Evidence quality、Remediation suggestions 和 fenced JSON tail。",
                ],
            )
    return "\n".join(lines).rstrip()


def _append_logcat_artifact_reference(
    lines: list[str],
    filename: str,
    content_lines: list[str],
    warnings: list[dict[str, str]],
    metadata: dict[str, Any] | None = None,
) -> None:
    """Render a compact pointer to the external filtered logcat artifact."""

    lines.extend(["### Logcat 系统日志"])
    if warnings:
        lines.append(f"- Warnings: `{'; '.join(w['code'] for w in warnings)}`")
    lines.append(f"- 过滤后的 Logcat 已单独保存为：`{filename}`")
    lines.append(f"- Retained lines: `{len(content_lines)}`")
    if metadata:
        pre_count = metadata.get("anrManagerPreContextRetainedLineCount")
        pre_anchor = metadata.get("anrManagerPreContextAnchor")
        if pre_count is not None:
            lines.append(f"- AnrManager pre-context retained lines: `{pre_count}`")
        if pre_anchor:
            lines.append(f"- AnrManager pre-context anchor: `{pre_anchor}`")
    lines.extend([
        "- 分析本段时请读取同目录下该文件；`anr_analysis.md` 不再内联大段 logcat 文本。",
        "",
    ])


def _render_inline_analysis_markdown(package: dict[str, Any], groups: list[dict[str, Any]], options: AiContextOptions, strategy: AnrTypeStrategy) -> str:
    """Render a reviewer-friendly file where AI conclusions sit beside evidence."""

    body = _render_cache_markdown(
        package,
        groups,
        options,
        strategy,
        include_analysis_slots=True,
        document_title="ANR Inline Analysis Workspace",
    )
    instructions = "\n".join([
        "> 使用方式：按 Trace → EventLog → Logcat/AnrManager → Final ANR 顺序填写四个 `#### AI Analysis — ...` 小节。",
        "> 保留上方对应 evidence 代码块，方便人工在同一位置核对原始过滤日志和结论。",
        "> 前三段只做专项分析；Final ANR 段负责跨源汇总、候选根因链、证据质量、修复建议和 JSON tail。",
        "> 若某来源证据不足，请在对应专项分析位写明 `_本来源无有效结论_` 以及缺口。",
        "",
    ])
    return body.replace("## 运行元数据", instructions + "## 运行元数据", 1)


def _append_section(
    lines: list[str],
    title: str,
    content_lines: list[str],
    warnings: list[dict[str, str]],
    *,
    include_analysis_slot: bool = False,
) -> None:
    lines.extend([f"### {title}"])
    if warnings:
        lines.append(f"- Warnings: `{'; '.join(w['code'] for w in warnings)}`")
    if content_lines:
        lines.append("```text")
        lines.extend(content_lines)
        lines.append("```")
    else:
        lines.append("_无保留行。")
    lines.append("")
    if include_analysis_slot:
        _append_analysis_slot(lines, title)


def _append_analysis_slot(
    lines: list[str],
    title: str,
    slot_id: str | None = None,
    skill_name: str | None = None,
    instructions: list[str] | None = None,
) -> None:
    slot_id = slot_id or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    lines.extend([
        f"#### AI Analysis — {title}",
        f"<!-- AI_ANALYSIS_SLOT:{slot_id} -->",
    ])
    if skill_name:
        lines.append(f"> Skill: `{skill_name}`")
    for instruction in instructions or []:
        lines.append(f"> - {instruction}")

    # Provide slot-specific output templates
    template = _get_analysis_slot_template(slot_id)
    lines.extend([
        "",
        template,
        "",
    ])


def _get_analysis_slot_template(slot_id: str) -> str:
    """Return a detailed output template for each analysis slot."""

    templates = {
        "trace": """##### 本段专项结论
_请用 2-3 句话总结 Trace 专项结论：主线程状态、直接阻塞点、相关线程/hints。_

##### 关键证据
_列出支持上述结论的关键 Trace 证据：_
- _主线程：tid/state/top frames_
- _死锁检测结果（如有）_
- _Trace 线索（如有）_
- _相关 owner/peer 线程（如有）_

##### 缺口
_列出 Trace 分析中的证据缺口：_
- _例如：缺少 owner 线程栈、schedstat 不足以判断负载等_

##### 置信度
_评估本段分析的置信度：strong / medium / weak，并说明原因。_""",

        "eventlog": """##### 本段专项结论
_请用 2-3 句话总结 EventLog 专项结论：ANR anchor、pre-ANR 时间线、关键状态转换。_

##### 关键证据
_按时间顺序列出关键 EventLog 证据：_
- _am_anr 行及其 ΔT=0 基准_
- _pre-ANR 窗口内的关键 tag（生命周期/焦点/输入等）_
- _状态机转换序列_

##### 缺口
_列出 EventLog 分析中的证据缺口：_
- _例如：缺少 input 事件、窗口焦点变化不明确等_

##### 置信度
_评估本段分析的置信度：strong / medium / weak，并说明原因。_""",

        "logcat-anrmanager": """##### 本段专项结论
_请用 2-3 句话总结 Logcat/AnrManager 专项结论：触发点、dump 生命周期、负载/PSI 状态。_

##### 关键证据
_列出关键 Logcat/AnrManager 证据：_
- _触发点（InputDispatcher / WindowManager / etc.）_
- _AnrManager reason / CPU / PSI / 高负载进程_
- _窗口/焦点/surface 序列（如有）_
- _Meminfo 跟进（如有）_

##### 缺口
_列出 Logcat/AnrManager 分析中的证据缺口：_
- _例如：缺少完整 dump 流程、CPU/PSI 数据不全等_

##### 置信度
_评估本段分析的置信度：strong / medium / weak，并说明原因。_""",

        "final-anr": """## 综合分析结论
_用 3-5 条 bullet 写明：ANR 类型、直接阻塞点、最可信根因链、降级/不支持方向。_

## 时间线
_按时间顺序整合 trace、EventLog、logcat 的关键事件。_

## Trace 证据分析
_按 Trace 分析要求展开主线程/相关线程/Trace 线索。_

## EventLog 证据分析
_按 EventLog 分析要求展开 12 秒 pre-ANR tag 证据。_

## Logcat 与 AnrManager 证据分析
_按 Logcat 与 AnrManager 分析要求展开 WMS/Input/AM/AnrManager。_

## 直接阻塞点
_判断直接阻塞点，并说明对应证据；如有死锁检测 hint 命中，请优先采纳并引用 hint id。_

## 候选根因链
_给出候选根因链路，按置信度排序。每条链路包含：触发类型 → 直接阻塞点 → 上游诱因 → 责任边界 → 证据强度。_

## 证据质量
_标注证据强弱、矛盾点、缺失信息、fallback/过滤问题。_

## 修复建议
_输出详细结论和下一步排查建议。_

## 结构化 JSON 尾部
_追加 fenced JSON 代码块，包含 anrType、primaryRootCauseHintId、candidateChains 等字段。_
```json
{
  "anrType": "input_dispatching_timeout | no_focus_window | unknown",
  "primaryRootCauseHintId": "DEADLOCK_CYCLE | MAIN_BINDER_WAIT_REPLY | null",
  "primaryRootCauseDescription": "<≤80字概述>",
  "supportingHintIds": [],
  "blockingThread": {"tid": "1", "name": "main", "frame": "..."},
  "ownerThread": {"tid": null, "name": null, "frame": null},
  "candidateChains": [
    {"rank": 1, "confidence": "strong", "summary": "...", "evidence": []}
  ],
  "remediationSuggestions": [],
  "evidenceGaps": [],
  "finalJudgment": false,
  "notRootCauseYet": true,
  "requiresHumanConfirmation": true
}
```"""
    }

    return templates.get(slot_id, "_待 AI 分析填写。请替换为本段专项结论、关键证据、缺口和置信度。_")


def _append_deadlock_section(
    lines: list[str],
    lock_graph: dict[str, Any] | None,
    deadlock_hints: list[dict[str, Any]],
) -> None:
    """Render a structured Deadlock Detection block above the raw Trace.

    The goal is to surface high-confidence machine-derived conclusions
    (cycles, chains, owner-sleeping contention) before the AI starts
    parsing raw thread dumps, so the LLM can anchor its analysis on
    structured evidence rather than re-deriving it from text.
    """

    has_graph = bool(lock_graph and (lock_graph.get("edges") or lock_graph.get("cycles")))
    if not deadlock_hints and not has_graph:
        return

    lines.append("### 死锁检测")
    if not has_graph:
        lines.append("- 未检测到锁等待边（无 `- waiting to lock ... held by thread N` 行）。")
    else:
        cycles = lock_graph.get("cycles", []) or []
        edges = lock_graph.get("edges", []) or []
        lines.append(f"- Lock graph: `{len(lock_graph.get('nodes', []))}` nodes, `{len(edges)}` edges, `{len(cycles)}` cycle(s).")
        for index, cycle in enumerate(cycles, start=1):
            kind = "self-loop" if cycle.get("selfLoop") else f"size {cycle.get('size', len(cycle.get('tids', []))) }"
            lines.append(f"  - Cycle #{index}: tids `{cycle.get('tids')}` ({kind}).")
        if edges:
            lines.append("- Edges (waiter → owner @ lock):")
            for edge in edges[:8]:
                lines.append(
                    f"  - `tid={edge['waiterTid']}` → `tid={edge['ownerTid']}` @ `{edge.get('lockObject')}`"
                )
            if len(edges) > 8:
                lines.append(f"  - …+{len(edges) - 8} more edges (truncated)")
    if deadlock_hints:
        lines.append("- Hints:")
        for hint in deadlock_hints:
            lines.append(
                f"  - `[{hint.get('id')} / {hint.get('confidence')} / {hint.get('severity')}]` {hint.get('message')}"
            )
            for action in hint.get("nextActions", [])[:2]:
                lines.append(f"    - next: {action}")
    else:
        lines.append("- Hints: none (graph present but no cycle / chain / owner-sleeping pattern matched).")
    lines.append("")


def _append_root_cause_pattern_hint_section(lines: list[str], details: list[dict[str, str]]) -> None:
    if not details:
        lines.append("- Root-cause pattern hints: `none`")
        return
    lines.append("- Root-cause pattern hints (candidate only):")
    for detail in details:
        lines.append(f"  - `{detail.get('id')}` — {detail.get('label')} (非最终根因，只作为证据提示)")


def _append_anrmanager_summary(lines: list[str], summary: dict[str, Any] | None) -> None:
    """Render structured AnrManager fields above the raw block.

    Surface the gold-standard AOSP-side numbers (CPU window totals, top
    processes, PSI memory pressure, ANR reason) and any system-level
    derived hints so the AI doesn't have to re-parse them from text.
    """

    if not summary:
        return
    interesting = (
        summary.get("anrReason")
        or summary.get("load")
        or summary.get("cpuTotal")
        or any(
            (section.get("some") or section.get("full"))
            for section in (summary.get("pressure") or {}).values()
            if isinstance(section, dict)
        )
        or summary.get("cpuTopProcesses")
        or summary.get("highCpuProcessesOver90")
        or summary.get("derivedHints")
    )
    if not interesting:
        return

    lines.append("### AnrManager 摘要")
    if summary.get("anrReason"):
        lines.append(f"- Reason: `{summary['anrReason']}`")
        if summary.get("anrPackage"):
            lines.append(f"- Process: `pid={summary.get('anrPid')} {summary.get('anrPackage')}`")
    load = summary.get("load")
    if load:
        lines.append(f"- Load: `{load.get('load1')} / {load.get('load5')} / {load.get('load15')}`")
    cpu_total = summary.get("cpuTotal")
    if cpu_total:
        iowait = cpu_total.get("iowaitPct")
        iowait_str = f" / iowait `{iowait}%`" if iowait is not None else ""
        lines.append(
            f"- CPU TOTAL: `{cpu_total.get('totalPct')}%` "
            f"(user `{cpu_total.get('userPct')}%` / kernel `{cpu_total.get('kernelPct')}%`{iowait_str})"
        )
    cpu_window = summary.get("cpuWindow")
    if cpu_window:
        lines.append(
            f"- CPU window: from `{cpu_window.get('fromMsAgo')}ms` to `{cpu_window.get('toMsAgo')}ms` ago"
        )
    top = summary.get("cpuTopProcesses") or []
    if top:
        lines.append("- Top CPU processes:")
        for proc in top[:5]:
            lines.append(
                f"  - `{proc['totalPct']}%` pid=`{proc['pid']}` `{proc['processName']}` "
                f"(user `{proc['userPct']}%` / kernel `{proc['kernelPct']}%`)"
            )
    high_cpu = summary.get("highCpuProcessesOver90") or []
    if high_cpu:
        lines.append("- CPU >90% processes:")
        for proc in high_cpu:
            lines.append(
                f"  - `{proc['totalPct']}%` pid=`{proc['pid']}` `{proc['processName']}` "
                f"(user `{proc['userPct']}%` / kernel `{proc['kernelPct']}%`)"
            )
    for pressure_name, pressure in (summary.get("pressure") or {}).items():
        if not isinstance(pressure, dict):
            continue
        psi_some = pressure.get("some")
        psi_full = pressure.get("full")
        if psi_some:
            lines.append(
                f"- PSI {pressure_name}.some: avg10=`{psi_some.get('avg10')}` "
                f"avg60=`{psi_some.get('avg60')}` avg300=`{psi_some.get('avg300')}`"
            )
        if psi_full:
            lines.append(
                f"- PSI {pressure_name}.full: avg10=`{psi_full.get('avg10')}` "
                f"avg60=`{psi_full.get('avg60')}` avg300=`{psi_full.get('avg300')}`"
            )
    if summary.get("tracesFilePath"):
        lines.append(f"- DropBox tracesFile: `{summary['tracesFilePath']}`")
    derived = summary.get("derivedHints") or []
    if derived:
        lines.append("- Derived hints:")
        for hint in derived:
            lines.append(
                f"  - `[{hint.get('id')} / {hint.get('confidence')} / {hint.get('severity')}]` {hint.get('message')}"
            )
    lines.append("")


def _append_trace_hints_section(
    lines: list[str],
    trace_hints: list[dict[str, Any]],
    deadlock_hints: list[dict[str, Any]],
) -> None:
    """Render every trace_hint that is NOT already covered by Deadlock Detection.

    Deadlock hints are surfaced separately (with cycles + edges); this section
    handles the rest (NativePollOnce truth/false, future SP/IO/Binder/Render
    hints, etc.) so they share a uniform AI-visible schema.
    """

    deadlock_ids = {id(h) for h in deadlock_hints or []}
    others = [h for h in (trace_hints or []) if id(h) not in deadlock_ids]
    if not others:
        return
    lines.append("### Trace 线索")
    for hint in others:
        promoted_from = hint.get("confidencePromotedFrom")
        promo_str = f" (↑ from `{promoted_from}` via cross-source corroboration)" if promoted_from else ""
        lines.append(
            f"- `[{hint.get('id')} / {hint.get('confidence')} / {hint.get('severity')}]`{promo_str} {hint.get('message')}"
        )
        sched = hint.get("schedstat")
        if sched:
            lines.append(f"  - schedstat: runNs=`{sched.get('runNs')}` waitNs=`{sched.get('waitNs')}`")
        for ev in hint.get("corroboratingEvidence") or []:
            lines.append(f"  - corroborated by `{ev.get('source')}`: {ev.get('label')} (regex: `{ev.get('regex')}`)")
        for action in (hint.get("nextActions") or [])[:2]:
            lines.append(f"  - next: {action}")
    lines.append("")


def _inject_hint_markers(
    trace_lines: list[str],
    deadlock_hints: list[dict[str, Any]],
) -> list[str]:
    """Append ``▸ HINT[id, confidence]: ...`` annotation lines below matching trace lines.

    Original lines are NEVER modified; the annotation is added as a separate
    line directly below the matched anchor so the AI (and humans) can still
    grep for the original ``- waiting to lock`` text. Anchors are identified
    by the lock object id appearing in any ``- waiting to lock <obj> held by
    thread N`` line that maps to a hint edge.
    """

    if not trace_lines or not deadlock_hints:
        return trace_lines

    markers_by_index: dict[int, list[str]] = {}
    for hint in deadlock_hints:
        edges = hint.get("edges", []) or []
        if not edges:
            continue
        for edge in edges:
            lock_obj = edge.get("lockObject")
            waiter_tid = edge.get("waiterTid")
            owner_tid = edge.get("ownerTid")
            if not lock_obj:
                continue
            for index, line in enumerate(trace_lines):
                if f"waiting to lock <{lock_obj}>" not in line:
                    continue
                indent = line[: len(line) - len(line.lstrip())]
                marker = (
                    f"{indent}  ▸ HINT[{hint.get('id')}, {hint.get('confidence')}]: "
                    f"tid={waiter_tid} → tid={owner_tid} (lock {lock_obj})"
                )
                bucket = markers_by_index.setdefault(index, [])
                if marker not in bucket:
                    bucket.append(marker)

    if not markers_by_index:
        return trace_lines

    out: list[str] = []
    for index, line in enumerate(trace_lines):
        out.append(line)
        for marker in markers_by_index.get(index, []):
            out.append(marker)
    return out


def _render_ai_prompt(cache_md: str, groups: list[dict[str, Any]], strategy: AnrTypeStrategy, *, evidence_analysis_md: str | None = None) -> str:
    completeness_summary = [f"- `{group['id']}` complete=`{group['completeness']['complete']}`" for group in groups]
    unified = evidence_analysis_md is not None
    return "\n".join([
        "# AI Prompt: Android ANR Root Cause Analysis",
        "",
        "你是 Android ANR 根因分析专家。请只基于下面的证据进行分析，明确区分证据、推断和缺失信息。",
        f"当前分析分支: `{strategy.anr_type}` ({strategy.label})。不同 ANR 类型的过滤窗口和证据重点可能不同，不要把 input timeout 结论套用到其他类型。",
        "",
        "## 类型特化关注点",
        *[f"- {item}" for item in strategy.analysis_focus],
        "",
        "## 根因模式提示约束",
        "- `rootCausePatternHints[]` 只表示候选根因模式提示，不等于最终根因。",
        "- `deadlock` / `memory_leak_oom_pressure` / `high_load_anr` 可以与任意 trigger type 并存，也可以出现在 unknown trigger 下。",
        "- 输出最终结论时必须用 Trace/EventLog/Logcat/AnrManager/Meminfo 证据交叉验证，不能只凭 hint 下结论。",
        "",
        "## 死锁检测速查表",
        "- 每个 `## anr-*` 分组下若存在 `### 死锁检测` 小节，则其内容为程序化锁图分析（基于 Tarjan SCC）的结论，可信度高于对栈帧的自由解读。",
        "- `[DEADLOCK_CYCLE / strong / critical]` = 等锁环成立且环上节点全部 Blocked，符合 Coffman 四要件，可直接作为根因。",
        "- `[DEADLOCK_LIKELY / medium / warning]` = 环存在但部分节点不在 Blocked，建议跨 trace 复核再下结论。",
        "- `[DEADLOCK_SELF / medium / warning]` = 同一线程等待自己已持有的锁，多见于 reentrant / unmatched unlock。",
        "- `[LOCK_OWNER_BLOCKED / strong / warning]` = 链式阻塞（≥2 跳但未成环），先解链末端 owner，链上其它锁会依次释放。",
        "- `[LOCK_OWNER_SLEEPING / strong / warning]` = owner 持锁后 sleep/timed_wait（典型：SP/IO 同步落盘），属于设计/性能问题，不是死锁。",
        "- `[LOCK_CONTENTION_BLOCKED / strong / warning]` = 普通锁竞争（owner 仍在执行）。",
        "- 若你给出的死锁结论与 `### 死锁检测` 不一致，请显式说明依据并解释冲突原因。",
        "- Trace 块内形如 `▸ HINT[id, confidence]: tid=X → tid=Y (lock 0x...)` 的行是程序化注解（不是原始日志），用于把对应 hint 锚定到具体栈帧；可直接引用，但不要把它当作 AOSP 输出。",
        "- `### Trace 线索` 小节包含非死锁类的程序化 hint，遵循同样的 [id / confidence / severity] 约定。",
        "- 特别注意 NativePollOnce 类 hint：",
        "  - `[NATIVE_POLL_BUT_BUSY / strong / warning]` = 主线程**有显著 CPU 消耗**，看似空闲实则在执行历史消息；**禁止**将主线程判为空闲。",
        "  - `[NATIVE_POLL_IDLE_LIKELY / weak / info]` = 主线程 schedstat 极小，**很可能消息队列空闲，trace 是替罪羊**；真实根因通常在系统压力 / 跨进程等待 / 渲染 GPU / 焦点窗口未就绪等其它源。",
        "  - `[NATIVE_POLL_AMBIGUOUS / weak / info]` = schedstat 处于不明区间；建议结合旁证综合判断。",
        "- 如果有以 `MAIN_*` 开头的 hint，说明主线程栈直接命中了已知模式：",
        "  - `MAIN_BINDER_WAIT_REPLY` = 主线程在 BinderProxy.transact / waitForResponse → 跨进程等回包；务必一并查 server 进程栈。",
        "  - `MAIN_SP_APPLY_WAIT` = SharedPreferences commit/apply / QueuedWork.waitToFinish → SP fsync 卡。",
        "  - `MAIN_IO_BLOCKED` / `MAIN_DB_BLOCKED` / `MAIN_NETWORK_BLOCKED` = 主线程同步 IO / SQLite / Socket。",
        "  - `MAIN_GC_PAUSED` = 主线程被 GC 暂停（WaitForGcToComplete / Runtime.gc）。",
        "  - `MAIN_RENDER_WAIT_FENCE` = 主线程在 ThreadedRenderer 同步 GPU 帧 / 等 fence。",
        "  - 一条主线程可同时命中多个 MAIN_* hint，没有互斥要求；按 confidence 优先级综合。",
        "- 若分组下有 `### AnrManager Summary` 小节，里面的 `Reason` / `Load` / `PSI memory|cpu|io` / `CPU TOTAL` / `CPU >90% processes` / `Top CPU processes` 是 AOSP 端权威字段，比从 trace 自由推断更可靠：",
        "  - AnrManager 负载归因顺序必须是：先看 `Load` 与 PSI 判断系统 CPU/IO/内存压力；再看 `CPU TOTAL`/`iowait`，若 `CPU TOTAL >=90%` 必须标记**整机/任务负载重**；再列出所有 `CPU >90% processes`。若这些进程包含目标包，必须明确标记为**目标应用自身极高负载**并联动目标包 meminfo/ANR metadata/PSI/GC/LMK/OOM 证据；若目标包 CPU `>85%`（例如 114%），即使 TOTAL 未到 90%，也要明确标记为**应用自身负载过高**。若 PSS/RSS/Anon RSS 偏高或 trace 出现 GC 等待，应把它归为应用负载问题，**大概率为内存泄漏或内存膨胀导致的 GC/分配抖动**（仍需 heap/GC 证据最终确认）。若其它进程 `>90%` 或高负载，则同样检查该进程内存/IO 证据，并把它归为外部系统压力或跨进程影响候选。",
        "  - 没有目标包/其它高负载进程的内存证据时，不能直接下“内存泄漏”或“OOM”结论，只能标记为待确认缺口。",
        "  - `[ANR_REASON_CLASSIFIED]` 的 `anrType` 字段是 AOSP 给出的 ANR 类型，应作为分析分支判定的首选依据。",
        "  - `[SYSTEM_CPU_SATURATED / strong / warning]` 出现时，主线程「卡」更可能是被调度饿死，而非应用代码问题。",
        "  - `[HIGH_CPU_PROCESS_OVER_90 / strong / warning]` 出现时，必须逐个检查 `CPU >90%` 进程；若命中目标包，目标包 meminfo 是必查项。",
        "  - `[ANR_PROCESS_CPU_CRITICAL / strong / critical]` 出现时，目标进程 CPU `>90%`，应优先按应用自身极高负载排查。",
        "  - `[SYSTEM_IO_PRESSURE / strong / warning]` 出现时，任何主线程同步 IO 都会被显著放大。",
        "  - `[SYSTEM_MEMORY_PRESSURE / strong / warning]` 出现时，主线程 STW / GC 暴涨需作为候选根因。",
        "",
        "## 四阶段强制分析流程",
        "必须按固定顺序填写四个分析位：Trace → EventLog → Logcat/AnrManager → Final ANR。Final ANR 只能在前三段专项分析完成后整合，不能跳过 source-specific analysis 直接给综合结论。",
        "综合分析必须写回当前 `anr_analysis.md` 的 `#### AI Analysis — 最终 ANR 综合分析` 分析位；不得只在聊天回复中输出。建议先写 `## 综合分析结论`，再写时间线、阻塞点、候选根因链、证据质量、修复建议和 JSON tail。",
        "",
        "1. `anr-trace-analysis`：只分析 Trace / 死锁检测 / Trace 线索，输出 Trace-only conclusion。",
        "2. `anr-eventlog-analysis`：只分析 EventLog 与 `am_anr` anchor，输出 EventLog-only timeline/state-machine conclusion。",
        "3. `anr-logcat-analysis`：分析 Logcat + AnrManager + Meminfo follow-up，输出 trigger/dump/load conclusion。",
        "4. `anr-analysis`：按固定步骤交叉验证前三段，输出最终 Markdown 报告与 JSON tail。",
        "",
        "## 内联分析位置",
        "- 下文 `## 证据与分析` 区域中固定包含四个分析位：`#### AI Analysis — Trace 堆栈`、`#### AI Analysis — EventLog 事件日志`、`#### AI Analysis — Logcat / AnrManager`、`#### AI Analysis — 最终 ANR 综合分析`。" if unified else "- 当前 ANR 目录会生成 `anr_analysis.md`：它把过滤后的日志证据和四个固定分析位放在同一位置。",
        "- 你的 Markdown 分析结果必须写入对应分析位；前三段只做专项分析，Final ANR 段才放跨源汇总、候选根因链、Evidence quality、remediation 和 JSON tail。",
        "- Final ANR 综合结论必须落盘在同一文件的 `#### AI Analysis — 最终 ANR 综合分析` 下；回复用户前先确认该槽位不再是 pending。",
        "- 如果某一来源没有足够证据，在对应专项分析位写 `_本来源无有效结论_` 并列出缺口；Final ANR 必须引用这些缺口。",
        "",
        "### Trace 分析要求",
        "- 写明 trace 文件/分组、pid/process、选中的 trace section、dump 时间与 ANR anchor 的关系。",
        "- 必须以原始 Trace 代码块、死锁检测 与 Trace 线索 为证据；工具内部 metadata 仅供程序使用，不在 Markdown 中单独输出。",
        "- 展开主线程：thread name/tid/sysTid/prio、ART/Java/Linux state、group/sCount/dsCount/obj/self、nice/cgrp/sched/core、top native/java/looper frames、schedstat/utm/stm/HZ、held mutexes、waitObject/lockOwnerTid、是否命中 Deadlock / Trace 线索。",
        "- 判断主线程当前直接阻塞类型：lock/binder/io/db/network/render/nativePoll/idle-or-ambiguous；说明“能证明什么”和“不能证明什么”。",
        "- 若存在锁/Binder/owner 线程，列出 owner/peer 线程证据；没有则明确写缺口。",
        "- 若 `ownerThread`、`binderSummary`、`renderSummary`、`suspendSummary`、`cpuSummary` 存在，必须分别说明其与主线程阻塞的关系；若只能作为旁证或放大因素，也要明确降级。",
        "",
        "### Trace 字段解读检查表",
        "- 线程头：解释 `\"main\" prio=... tid=... <state>` 中 name/prio/tid/ART state；`tid` 是 ART 线程标识，`sysTid` 才是 Linux 线程号，主线程 sysTid 通常等于 pid。",
        "- 线程对象/组：解释 `group/sCount/dsCount/ucsCount/flags/obj/self`；`dsCount` 只可提示调试器挂起历史，不能单独定责。",
        "- 调度上下文：解释 `sysTid/nice/cgrp/sched/handle` 与 `state/schedstat/utm/stm/core/HZ`；`schedstat` 三元组按 runNs/waitNs/timeSlices 解读，`waitNs >> runNs` 支持调度等待但需要 CPU/Load 旁证。",
        "- 状态映射：把 ART state 映射为 Java 语义（RUNNABLE/NATIVE、BLOCKED/MONITOR、WAITING/WAIT/VMWAIT、TIMED_WAITING/TIMED_WAIT、SUSPENDED、ZOMBIE/TERMINATED、UNKNOWN），并结合 Linux state R/S/D 判断是执行、睡眠、不可中断等待还是采样不明。",
        "- 栈帧：分别列 top native frame、top Java frame、looper frame；`nativePollOnce` 只能说明该快照在等消息/epoll，必须结合 schedstat、ANR 类型和 logcat 判断是否“替罪羊”。",
        "- 锁/等待：对 `waiting to lock <obj> held by thread N`、`waiting on <obj>`、`sleeping on <obj>`、`locked <obj>` 写清 waiter → owner → lockObject；若 owner 线程缺失或 owner 未阻塞，不能升级为死锁。",
        "- 自动分类规则要保守落地：CPU 执行超时需要 main RUNNABLE/R + 持续时间/CPU 旁证；锁竞争需要 main BLOCKED/MONITOR + owner 线程；Binder 需要 waitForResponse/talkWithDriver + 对端；Input 需要 InputDispatcher/Slow dispatch；GC/STW 需要多线程 SUSPENDED/GC 旁证；Render/GPU 需要 main/RenderThread/SF 或 fence 证据。",
        "",
        "### EventLog 分析要求",
        "- 以 `am_anr` 行作为基准，写明 timestamp、pid、process、reason。",
        "- 对 12 秒 pre-ANR 窗口内保留的 `am_*` / `wm_*` / `input_*` / power/battery/ssm tag 按时间顺序解释。",
        "- 对每条关键 EventLog 写明相对 ANR 的 ΔT、所属类别（进程/Activity 生命周期/窗口/焦点/输入/内存等）和它对根因链的意义。",
        "- 不要求上下文行都包含目标包名；要解释 next app、system_server 或其它进程事件如何影响 ANR。",
        "",
        "### Logcat 与 AnrManager 分析要求",
        "- 若 `### Logcat 系统日志` 只给出 `logcat.txt` 文件名，必须先读取同目录下该文件，再分析 InputDispatcher、WindowManager、ActivityManager、AnrManager 的关键行。",
        "- 分析 InputDispatcher、WindowManager、ActivityManager、AnrManager 的关键行，尤其真实触发点和 dump/kill/restart 流程。",
        "- 对窗口/focus/surface/transition 事件写清顺序：focus from/to、relayout、surface show/hide、finishDrawing/reportDrawFinished、window death。",
        "- 分析 AnrManager CPU/PSI/Load/trace dump 字段；必须按“Load/PSI → CPU TOTAL/iowait → CPU >90% processes → 目标包 Top 负载 → 高负载进程内存证据（meminfo/PSI/GC/LMK/OOM）→ 外部进程压力”顺序归因；若 `CPU TOTAL >=90%`，必须写成整机/任务负载重；若目标包 CPU `>90%`，必须写成目标应用自身极高负载并查目标包 meminfo；若目标包 CPU `>85%`，必须写成应用自身负载过高，并结合目标包内存提示判断是否大概率内存泄漏/内存膨胀；若缺失或只有部分 block，也要写明缺口。",
        "- 若 cache 中存在 `### Meminfo 目标/高负载跟进`，必须在 AnrManager 负载分析之后引用该节，验证目标包和 AnrManager `CPU >90%` / Top 高负载进程的 PSS/RSS/系统内存状态。",
        "- 区分 ANR 触发前证据、dump 期间证据、ANR 后恢复/重启证据，避免把后置日志当根因。",
        "",
        "### 跨源综合要求",
        "- 明确列出 Trace ↔ EventLog ↔ Logcat 是否相互印证、是否矛盾，以及哪个来源是 primary evidence。",
        "- 若某来源缺失、过滤为空、时间不一致或 fallback anchor 被使用，必须在 Evidence quality 中标红说明。",
        "",
        "### Final ANR 固定步骤要求",
        "Final ANR 段必须按以下顺序整合：",
        "1. 确认 ANR 类型与 anchor（优先 EventLog `am_anr` 与 AnrManager reason/hint）。",
        "2. 汇总 Trace 直接阻塞点：线程、状态、栈、等待对象、owner/peer、Trace 线索。",
        "3. 汇总 EventLog 时间线：ANR 前窗口、生命周期、焦点/输入/进程变化。",
        "4. 汇总 Logcat/AnrManager：真实触发点、dump 生命周期、负载/PSI/meminfo、恢复/kill。",
        "5. 交叉验证三源：一致、矛盾、缺失、fallback anchor 或时间偏差。",
        "6. 输出 Direct blocking point：只写证据能直接支持的阻塞点。",
        "7. 输出 Candidate root-cause chains：触发类型 → 直接阻塞点 → 上游诱因 → 责任边界 → 证据强度。",
        "8. 输出 Evidence quality、Remediation suggestions，并追加保守 JSON tail。",
        "",
        "## 必需输出",
        "在 `#### AI Analysis — 最终 ANR 综合分析` 中必须使用以下 Markdown 标题，且每节至少包含可引用的原始证据/专项分析结论或明确写 `_无保留证据_`：",
        "1. `## 综合分析结论`：先用 3-5 条 bullet 写 ANR 类型、直接阻塞点、最可信根因链、降级/不支持方向。",
        "2. `## 时间线`：按时间线描述 trace、EventLog、logcat 的关键事件。",
        "3. `## Trace 证据分析`：按 Trace 分析要求 展开主线程/相关线程/Trace 线索。",
        "4. `## EventLog 证据分析`：按 EventLog 分析要求 展开 12 秒 pre-ANR tag 证据。",
        "5. `## Logcat 与 AnrManager 证据分析`：按 Logcat 与 AnrManager 分析要求 展开 WMS/Input/AM/AnrManager。",
        "6. `## 直接阻塞点`：判断直接阻塞点，并说明对应证据；如有 死锁检测 hint 命中，请优先采纳并引用 hint id。",
        "7. `## 候选根因链`：给出候选根因链路，按置信度排序。",
        "8. `## 证据质量`：标注证据强弱、矛盾点、缺失信息、fallback/过滤问题。",
        "9. `## 修复建议`：输出详细结论和下一步排查建议。",
        "",
        "## 必需输出 — 结构化 JSON 尾部",
        "在自由文本结论之后，**追加一段独立的 fenced JSON 代码块**（语言标 `json`），便于自动评分。结构如下：",
        "```json",
        "{",
        '  "anrType": "input_dispatching_timeout | no_focus_window | broadcast_timeout | service_timeout | provider_timeout | unknown",',
        '  "primaryRootCauseHintId": "DEADLOCK_CYCLE | MAIN_BINDER_WAIT_REPLY | ... | null",',
        '  "primaryRootCauseDescription": "<≤80字概述>",',
        '  "supportingHintIds": ["..."],',
        '  "blockingThread": {"tid": "1", "name": "main", "frame": "<top frame概述>"},',
        '  "ownerThread": {"tid": null, "name": null, "frame": null},',
        '  "sourceAnalyses": {',
        '    "trace": {"summary": "...", "keyEvidence": ["..."], "gaps": ["..."]},',
        '    "eventLog": {"summary": "...", "keyEvidence": ["..."], "gaps": ["..."]},',
        '    "logcat": {"summary": "...", "keyEvidence": ["..."], "gaps": ["..."]}',
        '  },',
        '  "candidateChains": [',
        '    {"rank": 1, "confidence": "critical|strong|weak", "summary": "...", "evidence": ["hint:DEADLOCK_CYCLE", "logcat:Slow binder transaction", "..."]}',
        '  ],',
        '  "remediationSuggestions": ["..."],',
        '  "evidenceGaps": ["..."],',
        '  "finalJudgment": false,',
        '  "notRootCauseYet": true,',
        '  "requiresHumanConfirmation": true',
        "}",
        "```",
        "约束：",
        "- `anrType` 必须与 `[ANR_REASON_CLASSIFIED]` hint 的 `anrType` 字段对齐；若没有该 hint，可基于 trace/logcat 推断后填入。",
        "- `primaryRootCauseHintId` 必须引用 `### Deadlock Detection` 或 `### Trace Hints` 或 `### AnrManager 摘要` 中真实出现过的 hint id；若证据不足请置为 `null`。",
        "- `supportingHintIds` 列表中的元素必须出现在本上下文中。",
        "- `candidateChains[*].confidence` 必须按照本上下文标注（critical/strong/weak），如果你下调了某 hint 的可信度请显式说明。",
        "- `finalJudgment / notRootCauseYet / requiresHumanConfirmation` 三个字段保持上述默认值（保守策略），除非证据极其充分。",
        "",
        "## 完整性摘要",
        *completeness_summary,
        "",
        *((
            "## 证据与分析",
            "",
            "> 使用方式：AI 分析结果应直接填写到每个 `#### AI Analysis — ...` 小节下。",
            "> 保留上方对应 evidence 代码块，方便人工在同一位置核对原始过滤日志和结论。",
            "> 若某来源证据不足，请在对应小节写明 `_本来源无有效结论_` 以及缺口。",
            "",
            evidence_analysis_md,
        ) if unified else (
            "## 证据缓存",
            cache_md,
        )),
    ]).rstrip()


def _strategy_summary(strategy: AnrTypeStrategy) -> dict[str, Any]:
    return {
        "anrType": strategy.anr_type,
        "label": strategy.label,
        "eventBeforeSeconds": strategy.event_before_seconds,
        "logcatBeforeSeconds": strategy.logcat_before_seconds,
        "logcatAfterSeconds": strategy.logcat_after_seconds,
        "groupToleranceSeconds": strategy.group_tolerance_seconds,
        "analysisFocus": list(strategy.analysis_focus),
    }


def _group_id(timestamp: datetime) -> str:
    return f"anr-{timestamp.strftime('%Y%m%d-%H%M%S-%f')[:-3]}"


def _unanchored_group_id(timestamp: datetime | None) -> str:
    if timestamp is None:
        return "anr-unanchored"
    return f"anr-unanchored-{timestamp.strftime('%Y%m%d-%H%M%S-%f')[:-3]}"


def _trace_selected_timestamp(trace: dict[str, Any]) -> datetime | None:
    metadata = trace.get("metadata") or {}
    selected = metadata.get("selectedSectionTimestamp")
    if not selected:
        return None
    return parse_log_timestamp(selected)
