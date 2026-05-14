"""Phase 2 normalization for Phase 1 evidence packages."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha1
import re
from typing import Any

from .trace_preprocessor import preprocess_trace_content

PRIMARY_FIELDS = ("metadata", "classification", "anchors", "sources", "evidence", "warnings")
TIMESTAMP_RE = re.compile(r"(?P<ts>\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})")
PACKAGE_HINT_RE = re.compile(r"ANR in\s+(?P<pkg>[^:\s]+)")
LOGCAT_RE = re.compile(r"\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\s+(?P<priority>[VDIWEF])\s+(?P<tag>[^\s:]+)")


class NormalizationError(ValueError):
    """Raised when the input is not a valid Phase 1 evidence package."""


SOURCE_RECORD_TYPES = {
    "trace": "trace_thread_context",
    "event_log": "event_marker",
    "logcat": "log_line_window",
    "kernel_log": "kernel_context",
}


def normalize_evidence_package(package: dict[str, Any]) -> dict[str, Any]:
    upstream = _validate_phase1_package(package)
    normalized_records = [_normalize_record(upstream, evidence) for evidence in _sorted_evidence(upstream["evidence"])]
    return {
        "metadata": {
            "packageId": upstream["metadata"]["packageId"],
            "phase": "phase2-evidence-normalization",
            "schemaVersion": "phase2-normalized-v1",
            "upstreamPhase": upstream["metadata"].get("phase", "phase1-evidence-extraction-mvp"),
            "status": upstream["metadata"]["status"],
        },
        "classification": deepcopy(upstream["classification"]),
        "anchors": deepcopy(upstream["anchors"]),
        "sourceSummaries": deepcopy(upstream["sources"]),
        "normalizedRecords": normalized_records,
        "warnings": deepcopy(upstream.get("warnings", [])),
    }


def _validate_phase1_package(package: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(package, dict):
        raise NormalizationError("Phase 2 input must be a dict-like Phase 1 evidence package.")
    missing = [field for field in PRIMARY_FIELDS if field not in package]
    if missing:
        raise NormalizationError(f"Phase 1 evidence package is missing required fields: {', '.join(missing)}")
    return package


def _sorted_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (item.get("sourceKind", ""), item.get("id", ""), item.get("label", "")))


def _normalize_record(package: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    source_kind = evidence["sourceKind"]
    record_type = _record_type_for(evidence)
    normalized_fields = _normalized_fields_for(source_kind, evidence)
    anchor_ref = _anchor_ref(package, evidence)
    raw_snippet = evidence.get("content", "")
    record_seed = "|".join([
        package["metadata"]["packageId"],
        source_kind,
        record_type,
        evidence.get("id", ""),
        evidence.get("content", ""),
    ])
    record_id = f"norm-{sha1(record_seed.encode('utf-8')).hexdigest()[:12]}"
    return {
        "recordId": record_id,
        "sourceKind": source_kind,
        "recordType": record_type,
        "tier": evidence["tier"],
        "anchorRef": anchor_ref,
        "label": evidence.get("label"),
        "sourcePath": evidence.get("provenance", {}).get("sourcePath"),
        "extractionMode": evidence.get("extractionMode"),
        "normalizedFields": normalized_fields,
        "contentLineCount": len([line for line in raw_snippet.splitlines() if line.strip()]),
        "rawSnippet": raw_snippet,
        "provenance": deepcopy(evidence.get("provenance", {})),
        "warnings": list(evidence.get("provenance", {}).get("warningFlags", [])),
    }


def _record_type_for(evidence: dict[str, Any]) -> str:
    evidence_id = evidence.get("id", "")
    if evidence_id == "trace_core":
        return "trace_thread_context"
    if evidence_id == "event_pre_window":
        return "event_pre_window"
    if evidence_id == "event_am_anr":
        return "event_marker"
    if evidence_id == "logcat_anchor_window":
        return "log_anchor_window"
    if evidence_id == "kernel_anchor_window":
        return "kernel_anchor_window"
    if evidence_id.endswith("_context") and evidence["sourceKind"] == "trace":
        return "trace_thread_context"
    if evidence_id.endswith("_context") and evidence["sourceKind"] == "logcat":
        return "log_symptom_context"
    if evidence_id.endswith("_context") and evidence["sourceKind"] == "event_log":
        return "event_context"
    return SOURCE_RECORD_TYPES.get(evidence["sourceKind"], "generic_record")


def _normalized_fields_for(source_kind: str, evidence: dict[str, Any]) -> dict[str, Any]:
    content = evidence.get("content", "")
    if source_kind == "trace":
        return _normalize_trace(content)
    if source_kind == "event_log":
        return _normalize_event_log(content, evidence)
    if source_kind == "logcat":
        return _normalize_logcat(content, evidence)
    if source_kind == "kernel_log":
        return _normalize_kernel_log(content)
    return {"summary": content[:120]}


def _normalize_trace(content: str) -> dict[str, Any]:
    preprocessed = preprocess_trace_content(content)
    primary_thread = preprocessed.get("primaryThread") or {}
    owner_thread = preprocessed.get("ownerThread") or {}
    binder_summary = preprocessed.get("binderSummary") or {}
    render_summary = preprocessed.get("renderSummary") or {}
    suspend_summary = preprocessed.get("suspendSummary") or {}
    cpu_summary = preprocessed.get("cpuSummary") or {}
    matched_pattern = None
    for line in preprocessed.get("compactedLines", []):
        lower = line.lower()
        if "focused window" in lower:
            matched_pattern = "focus_window_signal"
        elif "input dispatching" in lower:
            matched_pattern = "input_dispatch_signal"
    return {
        "processName": preprocessed.get("processName"),
        "pid": preprocessed.get("pid"),
        "threadName": primary_thread.get("threadName"),
        "tid": primary_thread.get("tid"),
        "threadRole": primary_thread.get("threadRole"),
        "prio": primary_thread.get("prio"),
        "daemon": primary_thread.get("daemon"),
        "artThreadState": primary_thread.get("artThreadState"),
        "javaThreadState": primary_thread.get("javaThreadState"),
        "mainThreadCaptured": bool(primary_thread.get("isMainThread")),
        "mainThreadSysTid": primary_thread.get("sysTid"),
        "group": primary_thread.get("group"),
        "sCount": primary_thread.get("sCount"),
        "dsCount": primary_thread.get("dsCount"),
        "ucsCount": primary_thread.get("ucsCount"),
        "flags": primary_thread.get("flags"),
        "obj": primary_thread.get("obj"),
        "self": primary_thread.get("self"),
        "nice": primary_thread.get("nice"),
        "cgrp": primary_thread.get("cgrp"),
        "sched": primary_thread.get("sched"),
        "handle": primary_thread.get("handle"),
        "linuxState": primary_thread.get("linuxState"),
        "schedstat": primary_thread.get("schedstat"),
        "utm": primary_thread.get("utm"),
        "stm": primary_thread.get("stm"),
        "core": primary_thread.get("core"),
        "hz": primary_thread.get("hz"),
        "threadState": primary_thread.get("threadState"),
        "blockHint": primary_thread.get("blockHint"),
        "lockOwnerTid": primary_thread.get("lockOwnerTid"),
        "waitObject": primary_thread.get("waitObject"),
        "heldMutexes": primary_thread.get("heldMutexes"),
        "lockOwnerThreadName": owner_thread.get("threadName"),
        "lockOwnerThreadSysTid": owner_thread.get("sysTid"),
        "lockOwnerThreadRole": owner_thread.get("threadRole"),
        "lockOwnerThreadState": owner_thread.get("threadState"),
        "lockOwnerThreadBlockHint": owner_thread.get("blockHint"),
        "lockContentionDetected": bool(preprocessed.get("threadSummary", {}).get("lockContentionDetected")),
        "binderWaitChainDetected": binder_summary.get("binderWaitChainDetected"),
        "mainThreadBinderBlocked": binder_summary.get("mainThreadBinderBlocked"),
        "mainThreadBinderCallKind": binder_summary.get("mainThreadBinderCallKind"),
        "mainThreadBinderDriverFrame": binder_summary.get("mainThreadBinderDriverFrame"),
        "binderThreadCount": binder_summary.get("binderThreadCount"),
        "binderThreadPoolCount": binder_summary.get("binderThreadPoolCount"),
        "binderReplyWaitCount": binder_summary.get("binderReplyWaitCount"),
        "binderBacklogCount": binder_summary.get("binderBacklogCount"),
        "binderDriverIoCount": binder_summary.get("binderDriverIoCount"),
        "topBinderThreads": binder_summary.get("topBinderThreads"),
        "renderWaitChainDetected": render_summary.get("renderWaitChainDetected"),
        "mainThreadRenderBlocked": render_summary.get("mainThreadRenderBlocked"),
        "mainThreadRenderCallKind": render_summary.get("mainThreadRenderCallKind"),
        "mainThreadRenderDriverFrame": render_summary.get("mainThreadRenderDriverFrame"),
        "renderThreadCount": render_summary.get("renderThreadCount"),
        "renderGpuWaitCount": render_summary.get("renderGpuWaitCount"),
        "renderDoFrameCount": render_summary.get("renderDoFrameCount"),
        "topRenderThreads": render_summary.get("topRenderThreads"),
        "stwPauseDetected": suspend_summary.get("stwPauseDetected"),
        "vmWaitClusterDetected": suspend_summary.get("vmWaitClusterDetected"),
        "debuggerSuspicion": suspend_summary.get("debuggerSuspicion"),
        "suspendedThreadCount": suspend_summary.get("suspendedThreadCount"),
        "vmWaitThreadCount": suspend_summary.get("vmWaitThreadCount"),
        "debuggerTouchedThreadCount": suspend_summary.get("debuggerTouchedThreadCount"),
        "topSuspendedThreads": suspend_summary.get("topSuspendedThreads"),
        "topVmWaitThreads": suspend_summary.get("topVmWaitThreads"),
        "schedulerPressureDetected": cpu_summary.get("schedulerPressureDetected"),
        "cpuBusyExecutionDetected": cpu_summary.get("cpuBusyExecutionDetected"),
        "mainThreadRunnableLike": cpu_summary.get("mainThreadRunnableLike"),
        "mainThreadRunNs": cpu_summary.get("mainThreadRunNs"),
        "mainThreadWaitNs": cpu_summary.get("mainThreadWaitNs"),
        "mainThreadWaitRunRatio": cpu_summary.get("mainThreadWaitRunRatio"),
        "runnableThreadCount": cpu_summary.get("runnableThreadCount"),
        "topRunnableThreads": cpu_summary.get("topRunnableThreads"),
        "mainThreadNativeTopFrame": primary_thread.get("nativeTopFrame"),
        "mainThreadJavaTopFrame": primary_thread.get("javaTopFrame"),
        "mainThreadLooperFrame": primary_thread.get("looperFrame"),
        "retainedThreadCount": len(preprocessed.get("threads", [])),
        "suspiciousThreadCount": len(preprocessed.get("suspiciousThreads", [])),
        "dominantBlockHint": (preprocessed.get("threadSummary") or {}).get("dominantBlockHint"),
        "selectedSectionIndex": preprocessed.get("selectedSectionIndex"),
        "sectionCount": preprocessed.get("sectionCount"),
        "matchedPattern": matched_pattern,
        "reasonCategory": matched_pattern,
    }


def _normalize_event_log(content: str, evidence: dict[str, Any]) -> dict[str, Any]:
    first_line = next((line for line in content.splitlines() if line.strip()), "")
    timestamp = _extract_timestamp(first_line)
    tokens = first_line.split()
    event_tag = tokens[2] if len(tokens) >= 3 else None
    pkg_match = PACKAGE_HINT_RE.search(first_line)
    return {
        "eventTag": event_tag,
        "eventTimestamp": timestamp,
        "packageHint": pkg_match.group("pkg") if pkg_match else None,
        "markerKind": "am_anr" if "am_anr" in first_line.lower() else None,
        "preWindowRelation": "preceding-window" if evidence.get("id") == "event_pre_window" else None,
        "lineRole": "marker" if evidence.get("id") == "event_am_anr" else "window",
    }


def _normalize_logcat(content: str, evidence: dict[str, Any]) -> dict[str, Any]:
    first_line = next((line for line in content.splitlines() if line.strip()), "")
    timestamp = _extract_timestamp(first_line)
    match = LOGCAT_RE.search(first_line)
    symptom = _symptom_category(content)
    return {
        "timestamp": timestamp,
        "priority": match.group("priority") if match else None,
        "tag": match.group("tag") if match else None,
        "matchedSymptomCategory": symptom,
        "lineRole": "type-context" if evidence.get("extractionMode") == "template-additive" else "anchor-window",
        "windowKind": "type-context" if evidence.get("extractionMode") == "template-additive" else "anchor-window",
    }


def _normalize_kernel_log(content: str) -> dict[str, Any]:
    first_line = next((line for line in content.splitlines() if line.strip()), "")
    timestamp = _extract_timestamp(first_line)
    subsystem = None
    if ":" in first_line:
        subsystem = first_line.split(":", 1)[0].split()[-1]
    return {
        "timestamp": timestamp,
        "subsystemHint": subsystem,
        "matchedSymptomCategory": _symptom_category(content),
        "windowKind": "anchor-window",
    }


def _anchor_ref(package: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any] | None:
    anchor = deepcopy(evidence.get("provenance", {}).get("anchorUsed"))
    if not anchor:
        return None
    primary = package.get("anchors", {}).get("primaryAnchor")
    relation = "primary"
    if primary and (anchor.get("sourceKind"), anchor.get("timestamp")) != (primary.get("sourceKind"), primary.get("timestamp")):
        relation = "secondary"
    return {
        "relation": relation,
        "sourceKind": anchor.get("sourceKind"),
        "timestamp": anchor.get("timestamp"),
        "line": anchor.get("line"),
    }


def _symptom_category(content: str) -> str | None:
    lower = content.lower()
    if "focused window" in lower:
        return "focus_window_issue"
    if "input dispatching" in lower:
        return "input_dispatch_timeout"
    if "binder" in lower:
        return "binder_or_ipc_pressure"
    if "sched" in lower:
        return "scheduler_pressure"
    return None


def _extract_timestamp(line: str) -> str | None:
    match = TIMESTAMP_RE.search(line)
    if not match:
        return None
    return match.group("ts")
