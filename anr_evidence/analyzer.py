"""Phase 3 minimal assisted analysis on top of normalized evidence packages."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha1
from typing import Any

REQUIRED_FIELDS = ("metadata", "classification", "anchors", "sourceSummaries", "normalizedRecords", "warnings")


class AnalysisError(ValueError):
    """Raised when the input is not a valid Phase 2 normalized package."""


def analyze_normalized_package(package: dict[str, Any]) -> dict[str, Any]:
    normalized = _validate_phase2_package(package)
    timeline = _build_timeline(normalized)
    signal_summary = _build_signal_summary(normalized)
    findings = _build_findings(normalized, signal_summary, timeline)
    return {
        "metadata": {
            "packageId": normalized["metadata"]["packageId"],
            "phase": "phase3-assisted-analysis",
            "schemaVersion": "phase3-analysis-v1",
            "upstreamPhase": normalized["metadata"].get("phase", "phase2-evidence-normalization"),
            "status": normalized["metadata"]["status"],
        },
        "classification": deepcopy(normalized["classification"]),
        "anchors": deepcopy(normalized["anchors"]),
        "signalSummary": signal_summary,
        "timeline": timeline,
        "findings": findings,
        "warnings": deepcopy(normalized.get("warnings", [])),
    }


def _validate_phase2_package(package: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(package, dict):
        raise AnalysisError("Phase 3 input must be a dict-like Phase 2 normalized package.")
    missing = [field for field in REQUIRED_FIELDS if field not in package]
    if missing:
        raise AnalysisError(f"Phase 2 normalized package is missing required fields: {', '.join(missing)}")
    metadata = package.get("metadata", {})
    if metadata.get("phase") != "phase2-evidence-normalization":
        raise AnalysisError("Phase 3 analyzer expects a Phase 2 normalized package.")
    return package


def _build_timeline(package: dict[str, Any]) -> list[dict[str, Any]]:
    entries = []
    for record in package["normalizedRecords"]:
        fields = record.get("normalizedFields", {})
        timestamp = _record_timestamp(record)
        entries.append(
            {
                "entryId": _stable_id("timeline", record["recordId"]),
                "timestamp": timestamp,
                "sourceKind": record["sourceKind"],
                "recordType": record["recordType"],
                "tier": record["tier"],
                "signalCategory": _record_signal_category(record),
                "dominantBlockHint": fields.get("dominantBlockHint"),
                "suspiciousThreadCount": fields.get("suspiciousThreadCount"),
                "summary": _record_summary(record),
                "recordRef": record["recordId"],
            }
        )
    return sorted(entries, key=lambda item: ((item["timestamp"] or "99-99 99:99:99.999"), item["sourceKind"], item["recordType"], item["recordRef"]))


def _build_signal_summary(package: dict[str, Any]) -> dict[str, Any]:
    source_counts: dict[str, int] = {}
    record_type_counts: dict[str, int] = {}
    signal_category_counts: dict[str, int] = {}
    tier_counts: dict[str, int] = {}
    dominant_trace_block_hints: dict[str, int] = {}
    suspicious_trace_records: list[dict[str, Any]] = []
    for record in package["normalizedRecords"]:
        source_counts[record["sourceKind"]] = source_counts.get(record["sourceKind"], 0) + 1
        record_type_counts[record["recordType"]] = record_type_counts.get(record["recordType"], 0) + 1
        tier_counts[record["tier"]] = tier_counts.get(record["tier"], 0) + 1
        fields = record.get("normalizedFields", {})
        category = _record_signal_category(record)
        if category:
            signal_category_counts[category] = signal_category_counts.get(category, 0) + 1
        if record["sourceKind"] == "trace":
            dominant_block_hint = fields.get("dominantBlockHint")
            if dominant_block_hint:
                dominant_trace_block_hints[dominant_block_hint] = dominant_trace_block_hints.get(dominant_block_hint, 0) + 1
            suspicious_count = fields.get("suspiciousThreadCount") or 0
            if suspicious_count:
                suspicious_trace_records.append(
                    {
                        "recordRef": record["recordId"],
                        "threadName": fields.get("threadName"),
                        "blockHint": fields.get("blockHint"),
                        "dominantBlockHint": dominant_block_hint,
                        "suspiciousThreadCount": suspicious_count,
                    }
                )
    missing_sources = sorted(
        source_kind for source_kind, summary in package["sourceSummaries"].items() if not summary.get("available")
    )
    unreadable_sources = sorted(
        source_kind for source_kind, summary in package["sourceSummaries"].items() if summary.get("available") and not summary.get("readable", True)
    )
    return {
        "recordCount": len(package["normalizedRecords"]),
        "sourceCounts": source_counts,
        "recordTypeCounts": record_type_counts,
        "signalCategoryCounts": signal_category_counts,
        "tierCounts": tier_counts,
        "missingSources": missing_sources,
        "unreadableSources": unreadable_sources,
        "traceInsights": {
            "suspiciousRecordCount": len(suspicious_trace_records),
            "suspiciousThreadTotal": sum(item["suspiciousThreadCount"] for item in suspicious_trace_records),
            "dominantBlockHintCounts": dominant_trace_block_hints,
            "mainThread": _main_thread_trace_summary(package["normalizedRecords"]),
            "binderSummary": _binder_trace_summary(package["normalizedRecords"]),
            "renderSummary": _render_trace_summary(package["normalizedRecords"]),
            "suspendSummary": _suspend_trace_summary(package["normalizedRecords"]),
            "cpuSummary": _cpu_trace_summary(package["normalizedRecords"]),
            "topSuspiciousRecords": sorted(
                suspicious_trace_records,
                key=lambda item: (-item["suspiciousThreadCount"], item.get("threadName") or "", item["recordRef"]),
            )[:5],
        },
        "inputInsights": _input_insights_summary(package["normalizedRecords"]),
    }


def _build_findings(package: dict[str, Any], signal_summary: dict[str, Any], timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    classification = package["classification"]
    findings.append(
        _finding(
            "analysis_scope",
            "scope_status",
            (
                f"This output is an assisted analysis summary for `{classification.get('detectedType')}`. "
                "It highlights evidence structure and suspicious signals, not a root-cause judgment."
            )
            if classification.get("supported") and classification.get("detectedType")
            else (
                "This output remains in assisted-analysis mode under fallback semantics; "
                "it highlights evidence structure and suspicious signals without asserting a root cause."
            ),
            severity="info",
        )
    )
    if classification.get("fallbackMode") and classification.get("fallbackMode") != "none":
        findings.append(
            _finding(
                "fallback",
                classification["fallbackMode"],
                f"Normalization and analysis preserved fallback mode `{classification['fallbackMode']}`; review the package with extra caution.",
                severity="warning",
            )
        )
    if signal_summary["missingSources"] or signal_summary["unreadableSources"]:
        details = []
        if signal_summary["missingSources"]:
            details.append(f"missing: {', '.join(signal_summary['missingSources'])}")
        if signal_summary["unreadableSources"]:
            details.append(f"unreadable: {', '.join(signal_summary['unreadableSources'])}")
        findings.append(
            _finding(
                "coverage",
                "source_integrity",
                "Source availability is incomplete; " + "; ".join(details) + ".",
                severity="warning",
            )
        )
    top_categories = sorted(signal_summary["signalCategoryCounts"].items(), key=lambda item: (-item[1], item[0]))
    for category, count in top_categories[:3]:
        findings.append(
            _finding(
                "signal",
                category,
                f"Observed {count} normalized record(s) tagged as `{category}` across the retained evidence.",
                severity="info",
            )
        )
    trace_insights = signal_summary.get("traceInsights", {})
    main_thread = trace_insights.get("mainThread")
    if main_thread and main_thread.get("captured"):
        findings.append(
            _finding(
                "trace",
                "main_thread",
                (
                    f"Trace main thread `{main_thread.get('threadName')}`/tid=`{main_thread.get('tid')}` "
                    f"sysTid=`{main_thread.get('sysTid')}` is retained with block hint `{main_thread.get('blockHint')}`; "
                    f"key looper frame: `{main_thread.get('looperFrame')}`."
                ),
                severity="info",
            )
        )
        if main_thread.get("lockContentionDetected"):
            findings.append(
                _finding(
                    "trace",
                    "lock_owner",
                    (
                        f"Main thread waits on `{main_thread.get('waitObject')}` and the trace resolves owner thread "
                        f"`{main_thread.get('lockOwnerThreadName')}`/tid=`{main_thread.get('lockOwnerTid')}` "
                        f"(sysTid=`{main_thread.get('lockOwnerThreadSysTid')}`), indicating likely lock contention."
                    ),
                    severity="warning",
                )
            )
    binder_summary = trace_insights.get("binderSummary", {})
    if binder_summary.get("binderWaitChainDetected"):
        findings.append(
            _finding(
                "trace",
                "binder_chain",
                (
                    f"Trace shows binder wait-chain signals: mainThreadBinderBlocked=`{binder_summary.get('mainThreadBinderBlocked')}`, "
                    f"binderThreadCount=`{binder_summary.get('binderThreadCount')}`, "
                    f"replyWait=`{binder_summary.get('binderReplyWaitCount')}`, "
                    f"threadPool=`{binder_summary.get('binderThreadPoolCount')}`."
                ),
                severity="warning",
            )
        )
    render_summary = trace_insights.get("renderSummary", {})
    if render_summary.get("renderWaitChainDetected"):
        findings.append(
            _finding(
                "trace",
                "render_chain",
                (
                    f"Trace shows render/gpu wait-chain signals: mainThreadRenderBlocked=`{render_summary.get('mainThreadRenderBlocked')}`, "
                    f"renderThreadCount=`{render_summary.get('renderThreadCount')}`, "
                    f"gpuWait=`{render_summary.get('renderGpuWaitCount')}`, "
                    f"doFrame=`{render_summary.get('renderDoFrameCount')}`."
                ),
                severity="warning",
            )
        )
    suspend_summary = trace_insights.get("suspendSummary", {})
    if suspend_summary.get("stwPauseDetected"):
        findings.append(
            _finding(
                "trace",
                "gc_stw",
                (
                    f"Trace shows STW-like suspension signals: suspendedThreadCount=`{suspend_summary.get('suspendedThreadCount')}` "
                    f"with debuggerSuspicion=`{suspend_summary.get('debuggerSuspicion')}`."
                ),
                severity="warning",
            )
        )
    if suspend_summary.get("vmWaitClusterDetected"):
        findings.append(
            _finding(
                "trace",
                "vm_wait_cluster",
                f"Trace shows clustered VMWAIT threads: vmWaitThreadCount=`{suspend_summary.get('vmWaitThreadCount')}`.",
                severity="info",
            )
        )
    cpu_summary = trace_insights.get("cpuSummary", {})
    if cpu_summary.get("schedulerPressureDetected"):
        findings.append(
            _finding(
                "trace",
                "scheduler_pressure",
                (
                    f"Trace suggests scheduler pressure: mainThreadRunnableLike=`{cpu_summary.get('mainThreadRunnableLike')}`, "
                    f"waitNs=`{cpu_summary.get('mainThreadWaitNs')}`, runNs=`{cpu_summary.get('mainThreadRunNs')}`."
                ),
                severity="warning",
            )
        )
    if cpu_summary.get("cpuBusyExecutionDetected"):
        findings.append(
            _finding(
                "trace",
                "cpu_busy_execution",
                (
                    f"Trace suggests main-thread CPU busy execution: runNs=`{cpu_summary.get('mainThreadRunNs')}`, "
                    f"waitNs=`{cpu_summary.get('mainThreadWaitNs')}`."
                ),
                severity="info",
            )
        )
    input_insights = signal_summary.get("inputInsights", {})
    if input_insights.get("noFocusedWindowDetected"):
        findings.append(
            _finding(
                "input",
                "no_focused_window",
                (
                    f"Cross-source input signals suggest a no-focused-window condition; "
                    f"agreementCount=`{input_insights.get('sourceAgreementCount')}`."
                ),
                severity="warning",
            )
        )
    if input_insights.get("inputWaitDetected"):
        findings.append(
            _finding(
                "input",
                "dispatcher_wait_finish",
                (
                    f"InputDispatcher wait/finish signals are present across sources; "
                    f"logcatInputDispatcherDetected=`{input_insights.get('logcatInputDispatcherDetected')}`, "
                    f"traceInputDispatcherDetected=`{input_insights.get('traceInputDispatcherDetected')}`."
                ),
                severity="warning",
            )
        )
    if input_insights.get("crossSourceInputConsistency"):
        findings.append(
            _finding(
                "input",
                "cross_source_confirmed",
                (
                    f"Input-related evidence is cross-source consistent for `{input_insights.get('detectedFamily')}` "
                    f"with agreementCount=`{input_insights.get('sourceAgreementCount')}`."
                ),
                severity="info",
            )
        )
    top_trace_records = trace_insights.get("topSuspiciousRecords", [])
    if top_trace_records:
        top_record = top_trace_records[0]
        findings.append(
            _finding(
                "trace",
                "suspicious_threads",
                (
                    f"Trace preprocessing flagged {trace_insights.get('suspiciousThreadTotal', 0)} suspicious thread signal(s); "
                    f"top record `{top_record['recordRef']}` centers on thread `{top_record.get('threadName')}` "
                    f"with dominant block hint `{top_record.get('dominantBlockHint') or top_record.get('blockHint')}`."
                ),
                severity="warning",
            )
        )
    dominant_trace_hints = trace_insights.get("dominantBlockHintCounts", {})
    if dominant_trace_hints:
        dominant_hint = sorted(dominant_trace_hints.items(), key=lambda item: (-item[1], item[0]))[0][0]
        findings.append(
            _finding(
                "trace",
                dominant_hint,
                f"Trace-derived blocking context is currently dominated by `{dominant_hint}` signals.",
                severity="info",
            )
        )
    if timeline:
        earliest = timeline[0]
        latest = timeline[-1]
        findings.append(
            _finding(
                "timeline",
                "observed_window",
                f"Timeline spans from `{earliest['timestamp']}` to `{latest['timestamp']}` across {len(timeline)} normalized event(s).",
                severity="info",
            )
        )
    return findings


def _record_timestamp(record: dict[str, Any]) -> str | None:
    anchor = record.get("anchorRef")
    if anchor and anchor.get("timestamp"):
        return anchor["timestamp"]
    fields = record.get("normalizedFields", {})
    for key in ("eventTimestamp", "timestamp"):
        if fields.get(key):
            return fields[key]
    return None


def _record_signal_category(record: dict[str, Any]) -> str | None:
    fields = record.get("normalizedFields", {})
    return fields.get("matchedSymptomCategory") or fields.get("reasonCategory")


def _record_summary(record: dict[str, Any]) -> str:
    fields = record.get("normalizedFields", {})
    parts = [record["recordType"]]
    if fields.get("packageHint"):
        parts.append(fields["packageHint"])
    if fields.get("threadName"):
        parts.append(fields["threadName"])
    if fields.get("dominantBlockHint"):
        parts.append(fields["dominantBlockHint"])
    if fields.get("matchedSymptomCategory"):
        parts.append(fields["matchedSymptomCategory"])
    elif fields.get("reasonCategory"):
        parts.append(fields["reasonCategory"])
    return " | ".join(parts)


def _main_thread_trace_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in records:
        if record.get("sourceKind") != "trace":
            continue
        fields = record.get("normalizedFields", {})
        if fields.get("mainThreadCaptured"):
            return {
                "captured": True,
                "recordRef": record.get("recordId"),
                "threadName": fields.get("threadName"),
                "tid": fields.get("tid"),
                "threadRole": fields.get("threadRole"),
                "prio": fields.get("prio"),
                "daemon": fields.get("daemon"),
                "artThreadState": fields.get("artThreadState"),
                "javaThreadState": fields.get("javaThreadState"),
                "sysTid": fields.get("mainThreadSysTid"),
                "group": fields.get("group"),
                "nice": fields.get("nice"),
                "cgrp": fields.get("cgrp"),
                "sched": fields.get("sched"),
                "linuxState": fields.get("linuxState"),
                "schedstat": fields.get("schedstat"),
                "core": fields.get("core"),
                "blockHint": fields.get("blockHint"),
                "lockOwnerTid": fields.get("lockOwnerTid"),
                "lockOwnerThreadName": fields.get("lockOwnerThreadName"),
                "lockOwnerThreadSysTid": fields.get("lockOwnerThreadSysTid"),
                "lockOwnerThreadRole": fields.get("lockOwnerThreadRole"),
                "lockOwnerThreadState": fields.get("lockOwnerThreadState"),
                "lockOwnerThreadBlockHint": fields.get("lockOwnerThreadBlockHint"),
                "lockContentionDetected": fields.get("lockContentionDetected"),
                "waitObject": fields.get("waitObject"),
                "heldMutexes": fields.get("heldMutexes"),
                "nativeTopFrame": fields.get("mainThreadNativeTopFrame"),
                "javaTopFrame": fields.get("mainThreadJavaTopFrame"),
                "looperFrame": fields.get("mainThreadLooperFrame"),
            }
    return {"captured": False}


def _binder_trace_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in records:
        if record.get("sourceKind") != "trace":
            continue
        fields = record.get("normalizedFields", {})
        return {
            "binderWaitChainDetected": fields.get("binderWaitChainDetected"),
            "mainThreadBinderBlocked": fields.get("mainThreadBinderBlocked"),
            "mainThreadBinderCallKind": fields.get("mainThreadBinderCallKind"),
            "mainThreadBinderDriverFrame": fields.get("mainThreadBinderDriverFrame"),
            "binderThreadCount": fields.get("binderThreadCount"),
            "binderThreadPoolCount": fields.get("binderThreadPoolCount"),
            "binderReplyWaitCount": fields.get("binderReplyWaitCount"),
            "binderBacklogCount": fields.get("binderBacklogCount"),
            "binderDriverIoCount": fields.get("binderDriverIoCount"),
            "topBinderThreads": fields.get("topBinderThreads"),
        }
    return {"binderWaitChainDetected": False}


def _render_trace_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in records:
        if record.get("sourceKind") != "trace":
            continue
        fields = record.get("normalizedFields", {})
        return {
            "renderWaitChainDetected": fields.get("renderWaitChainDetected"),
            "mainThreadRenderBlocked": fields.get("mainThreadRenderBlocked"),
            "mainThreadRenderCallKind": fields.get("mainThreadRenderCallKind"),
            "mainThreadRenderDriverFrame": fields.get("mainThreadRenderDriverFrame"),
            "renderThreadCount": fields.get("renderThreadCount"),
            "renderGpuWaitCount": fields.get("renderGpuWaitCount"),
            "renderDoFrameCount": fields.get("renderDoFrameCount"),
            "topRenderThreads": fields.get("topRenderThreads"),
        }
    return {"renderWaitChainDetected": False}


def _suspend_trace_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in records:
        if record.get("sourceKind") != "trace":
            continue
        fields = record.get("normalizedFields", {})
        return {
            "stwPauseDetected": fields.get("stwPauseDetected"),
            "vmWaitClusterDetected": fields.get("vmWaitClusterDetected"),
            "debuggerSuspicion": fields.get("debuggerSuspicion"),
            "suspendedThreadCount": fields.get("suspendedThreadCount"),
            "vmWaitThreadCount": fields.get("vmWaitThreadCount"),
            "debuggerTouchedThreadCount": fields.get("debuggerTouchedThreadCount"),
            "topSuspendedThreads": fields.get("topSuspendedThreads"),
            "topVmWaitThreads": fields.get("topVmWaitThreads"),
        }
    return {"stwPauseDetected": False, "vmWaitClusterDetected": False}


def _cpu_trace_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in records:
        if record.get("sourceKind") != "trace":
            continue
        fields = record.get("normalizedFields", {})
        return {
            "schedulerPressureDetected": fields.get("schedulerPressureDetected"),
            "cpuBusyExecutionDetected": fields.get("cpuBusyExecutionDetected"),
            "mainThreadRunnableLike": fields.get("mainThreadRunnableLike"),
            "mainThreadRunNs": fields.get("mainThreadRunNs"),
            "mainThreadWaitNs": fields.get("mainThreadWaitNs"),
            "mainThreadWaitRunRatio": fields.get("mainThreadWaitRunRatio"),
            "runnableThreadCount": fields.get("runnableThreadCount"),
            "topRunnableThreads": fields.get("topRunnableThreads"),
        }
    return {"schedulerPressureDetected": False, "cpuBusyExecutionDetected": False}


def _input_insights_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    source_flags = {
        "trace": {"input": False, "no_focus": False},
        "logcat": {"input": False, "no_focus": False},
        "event_log": {"input": False, "no_focus": False},
    }
    input_dispatcher_signal_count = 0
    focus_signal_count = 0
    event_am_anr_input_detected = False
    logcat_input_dispatcher_detected = False
    trace_input_dispatcher_detected = False

    for record in records:
        source_kind = record.get("sourceKind")
        fields = record.get("normalizedFields", {})
        raw = (record.get("rawSnippet") or "").lower()
        joined = " ".join(str(v).lower() for v in fields.values() if isinstance(v, (str, int, float, bool)))

        no_focus = any(token in raw or token in joined for token in ("no focused window", "no focus window", "no window has focus", "focus_window_signal"))
        input_wait = any(
            token in raw or token in joined
            for token in (
                "input dispatching",
                "input dispatching timed out",
                "input_dispatch_signal",
                "input_dispatch_timeout",
                "touched window has not finished processing",
                "inputdispatcher",
            )
        )

        if source_kind in source_flags:
            if no_focus:
                source_flags[source_kind]["no_focus"] = True
            if input_wait:
                source_flags[source_kind]["input"] = True

        if source_kind == "event_log":
            if fields.get("eventTag") in {"wm_focus", "input_focus"} or "wm_focus" in raw or "input_focus" in raw:
                focus_signal_count += 1
            if "am_anr" in raw and (no_focus or input_wait):
                event_am_anr_input_detected = True
        if source_kind == "logcat" and ("inputdispatcher" in raw or input_wait or no_focus):
            logcat_input_dispatcher_detected = True
        if source_kind == "trace" and (input_wait or no_focus):
            trace_input_dispatcher_detected = True

        if input_wait:
            input_dispatcher_signal_count += 1

    no_focus_sources = sum(1 for flags in source_flags.values() if flags["no_focus"])
    input_sources = sum(1 for flags in source_flags.values() if flags["input"])
    detected_family = None
    if no_focus_sources >= input_sources and no_focus_sources > 0:
        detected_family = "no_focused_window"
    elif input_sources > 0:
        detected_family = "input_dispatch_wait"

    source_agreement_count = max(no_focus_sources, input_sources)
    return {
        "inputWaitDetected": input_sources > 0,
        "noFocusedWindowDetected": no_focus_sources > 0,
        "inputDispatcherSignalCount": input_dispatcher_signal_count,
        "focusSignalCount": focus_signal_count,
        "crossSourceInputConsistency": source_agreement_count >= 2,
        "sourceAgreementCount": source_agreement_count,
        "detectedFamily": detected_family,
        "eventAmAnrInputDetected": event_am_anr_input_detected,
        "logcatInputDispatcherDetected": logcat_input_dispatcher_detected,
        "traceInputDispatcherDetected": trace_input_dispatcher_detected,
    }


def _finding(finding_type: str, finding_key: str, message: str, *, severity: str) -> dict[str, Any]:
    return {
        "findingId": _stable_id(finding_type, finding_key, message),
        "type": finding_type,
        "key": finding_key,
        "severity": severity,
        "message": message,
    }


def _stable_id(*parts: str) -> str:
    seed = "|".join(parts)
    return sha1(seed.encode("utf-8")).hexdigest()[:12]
