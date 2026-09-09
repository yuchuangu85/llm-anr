"""Fail-closed Binder peer and Watchdog lifecycle observations.

This module is deliberately separate from trace hinting.  A stack that is in
``BinderProxy.transact`` or a log line saying "slow binder transaction" is not
sufficient to identify a remote peer or a cause.  We only emit a Binder edge
when a source explicitly provides one correlation key plus caller and peer
PID/process identities, and the caller is the selected ANR target.  Likewise,
Watchdog states require an explicit checker key and an ordered start/blocked
sequence.  Missing terminal observations remain unknown rather than becoming
an inferred timeout cause.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import re
from typing import Any, Iterable

from .log_filter import parse_log_timestamp
from .log_identity import parse_line_identity


_SCHEMA_VERSION = "binder-watchdog-lifecycle-v1"
_AM_ANR_RE = re.compile(
    r"\bam_anr\s*:?\s*\[\s*\d+\s*,\s*(?P<pid>\d+)\s*,\s*(?P<process>[^,\]\s]+)",
    re.IGNORECASE,
)
_CORRELATION_RE = re.compile(
    r"\b(?:transactionId|txn|debug[_-]?id|callId)\s*(?:[:=]|\s)\s*(?P<value>[A-Za-z0-9_.:@/-]+)"
    r"|\btransaction\s*[:=]\s*(?P<bare_value>[A-Za-z0-9_.:@/-]+)",
    re.IGNORECASE,
)
_CALLER_PID_RE = re.compile(r"\b(?:callerPid|fromPid|senderPid)\s*[:=]\s*(?P<value>\d+)\b", re.IGNORECASE)
_PEER_PID_RE = re.compile(r"\b(?:targetPid|peerPid|toPid|receiverPid)\s*[:=]\s*(?P<value>\d+)\b", re.IGNORECASE)
_CALLER_PROCESS_RE = re.compile(
    r"\b(?:callerProcess|fromProcess|senderProcess)\s*[:=]\s*(?P<value>[A-Za-z][\w.]*(?::[\w.-]+)?)\b",
    re.IGNORECASE,
)
_PEER_PROCESS_RE = re.compile(
    r"\b(?:targetProcess|peerProcess|toProcess|receiverProcess)\s*[:=]\s*(?P<value>[A-Za-z][\w.]*(?::[\w.-]+)?)\b",
    re.IGNORECASE,
)
_BINDER_WORD_RE = re.compile(r"\bbinder\w*\b|\btransact(?:ion)?\b", re.IGNORECASE)
_BINDER_START_RE = re.compile(r"\b(?:start(?:ed)?|begin|sent|transact(?:ing)?)\b", re.IGNORECASE)
_BINDER_REPLY_RE = re.compile(r"\b(?:reply|replied|complete(?:d)?|finish(?:ed)?)\b", re.IGNORECASE)
_WATCHDOG_WORD_RE = re.compile(r"\b(?:watchdog|handlerchecker|monitor checker|blocked in handler)\b", re.IGNORECASE)
_CHECKER_RE = re.compile(
    r"\b(?:checker|handler|monitor)\s*[:=]\s*(?P<value>[A-Za-z0-9_.:@/-]+)", re.IGNORECASE
)
_WATCHDOG_START_RE = re.compile(r"\b(?:start(?:ed)?|schedule[sd]?|begin)\b", re.IGNORECASE)
_WATCHDOG_BLOCKED_RE = re.compile(r"\b(?:blocked|waited|overdue|timeout)\b", re.IGNORECASE)
_WATCHDOG_FINISH_RE = re.compile(r"\b(?:finish(?:ed)?|complete(?:d)?|recovered|passed)\b", re.IGNORECASE)
_RUNNABLE_RE = re.compile(r"(?:\bstate\s*=\s*R\b|\brunnable\b)", re.IGNORECASE)


def _append_gap(result: dict[str, Any], gap: str) -> None:
    if gap not in result["gaps"]:
        result["gaps"].append(gap)


def _event_id(source: str, index: int) -> str:
    return f"{source}:{index}"


def _line_timestamp(line: str) -> datetime | None:
    return parse_log_timestamp(line)


def _anchor_target(anchor: dict[str, Any] | None) -> tuple[dict[str, str] | None, datetime | None]:
    if not anchor:
        return None, None
    line = anchor.get("line")
    pid = anchor.get("pid")
    process = anchor.get("packageName") or anchor.get("process")
    if isinstance(line, str):
        match = _AM_ANR_RE.search(line)
        if match:
            pid = pid or match.group("pid")
            process = process or match.group("process")
        identity = parse_line_identity(line)
        if not pid and identity.target_pids:
            pid = identity.target_pids[0]
        if not process and identity.target_processes:
            process = identity.target_processes[0]
    timestamp = anchor.get("timestamp")
    if not isinstance(timestamp, datetime) and isinstance(line, str):
        timestamp = _line_timestamp(line)
    if not isinstance(pid, (str, int)) or not isinstance(process, str) or not process.strip():
        return None, timestamp if isinstance(timestamp, datetime) else None
    return {"kind": "process", "pid": str(pid), "name": process.strip()}, timestamp if isinstance(timestamp, datetime) else None


def _field(pattern: re.Pattern[str], line: str) -> str | None:
    match = pattern.search(line)
    return (match.group("value") or match.groupdict().get("bare_value")) if match else None


def _binder_state(line: str) -> str:
    if _BINDER_REPLY_RE.search(line):
        return "reply_observed"
    if _BINDER_START_RE.search(line):
        return "started"
    return "observed"


def _watchdog_state(line: str) -> str:
    if _WATCHDOG_FINISH_RE.search(line):
        return "finished"
    if _WATCHDOG_BLOCKED_RE.search(line):
        return "blocked"
    if _WATCHDOG_START_RE.search(line):
        return "started"
    return "observed"


def _trace_eligible(dump_quality: dict[str, Any] | None) -> bool:
    if not isinstance(dump_quality, dict):
        return False
    # DQ1's association combines target PID/process identity and anchor-window
    # membership.  Provenance is a separate dimension and must never be
    # silently substituted for association.
    return dump_quality.get("associationStatus") == "associated"


def _within_anchor_window(timestamp: datetime | None, anchor_timestamp: datetime | None, *, seconds: int) -> bool:
    if timestamp is None or anchor_timestamp is None:
        # Absence of a parseable timestamp does not fabricate temporal
        # ordering.  It can still contribute an event, but never establishes
        # a cross-source order by itself.
        return True
    return abs((timestamp - anchor_timestamp).total_seconds()) <= seconds


def _ordered(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        events,
        key=lambda event: (
            event.get("timestamp") is None,
            event.get("timestamp") or datetime.max,
            event["ordinal"],
        ),
    )


def _pressure_observations(
    anrmanager_summary: dict[str, Any] | None,
    trace_lines: Iterable[str],
) -> dict[str, list[dict[str, Any]]]:
    """Keep pressure separate from causation and from thread-stack hints."""
    pressure: dict[str, list[dict[str, Any]]] = {
        "causeCandidates": [],
        "amplifiers": [],
        "context": [],
    }
    if isinstance(anrmanager_summary, dict):
        cpu = anrmanager_summary.get("cpuTotal")
        if isinstance(cpu, dict) and isinstance(cpu.get("totalPct"), (int, float)) and cpu["totalPct"] >= 90:
            pressure["amplifiers"].append({
                "kind": "system_cpu_saturation",
                "classification": "amplifier",
                "evidence": {"cpuTotal": cpu},
                "notCauseAlone": True,
            })
        if isinstance(cpu, dict) and isinstance(cpu.get("iowaitPct"), (int, float)) and cpu["iowaitPct"] >= 20:
            pressure["amplifiers"].append({
                "kind": "system_io_pressure",
                "classification": "amplifier",
                "evidence": {"cpuTotal": cpu},
                "notCauseAlone": True,
            })
        memory_some = ((anrmanager_summary.get("memoryPressure") or {}).get("some") or {})
        if isinstance(memory_some.get("avg10"), (int, float)) and memory_some["avg10"] >= 20:
            pressure["amplifiers"].append({
                "kind": "system_memory_pressure",
                "classification": "amplifier",
                "evidence": {"memoryPressureSome": memory_some},
                "notCauseAlone": True,
            })
        for process in anrmanager_summary.get("cpuTopProcesses") or []:
            if isinstance(process, dict) and isinstance(process.get("totalPct"), (int, float)) and process["totalPct"] >= 90:
                pressure["context"].append({
                    "kind": "high_cpu_process_observed",
                    "classification": "context",
                    "evidence": {"process": process},
                    "notCauseAlone": True,
                })
    runnable_lines = [line for line in trace_lines if _RUNNABLE_RE.search(line)]
    if runnable_lines:
        pressure["context"].append({
            "kind": "runnable_thread_state_only",
            "classification": "context",
            "lineCount": len(runnable_lines),
            "notHighCpuProof": True,
            "notCauseAlone": True,
        })
    return pressure


def build_binder_watchdog_lifecycle(
    *,
    group_id: str,
    anchor: dict[str, Any] | None,
    logcat_lines: Iterable[str] = (),
    event_log_lines: Iterable[str] = (),
    trace_lines: Iterable[str] = (),
    dump_quality: dict[str, Any] | None = None,
    anrmanager_summary: dict[str, Any] | None = None,
    anchor_window_seconds: int = 300,
) -> dict[str, Any]:
    """Build a conservative, source-coordinate-friendly F2 observation view.

    Input lines are expected to be retained source lines in their original
    order.  The returned event IDs use source-relative offsets so callers can
    bind them to immutable EvidenceStore spans later; this builder never
    invents immutable references or root-cause confidence.
    """
    target, anchor_timestamp = _anchor_target(anchor)
    result: dict[str, Any] = {
        "schemaVersion": _SCHEMA_VERSION,
        "groupId": group_id,
        "target": target,
        "binderTransactions": [],
        "watchdogChecks": [],
        "edges": [],
        "pressure": _pressure_observations(anrmanager_summary, trace_lines),
        "gaps": [],
        "eligibleForHighCausalConfidence": False,
        "finalJudgment": False,
        "notRootCauseYet": True,
        "requiresHumanConfirmation": True,
    }
    if target is None:
        _append_gap(result, "selected_target_identity_unknown")

    binder_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    watchdog_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rejected_binder_event_ids: list[str] = []

    source_sets = (
        ("eventLog", list(event_log_lines), False),
        ("logcat", list(logcat_lines), False),
        ("trace", list(trace_lines), True),
    )
    ordinal = 0
    for source, lines, requires_dump_association in source_sets:
        if requires_dump_association and lines and not _trace_eligible(dump_quality):
            _append_gap(result, "trace_dump_not_associated")
            continue
        for index, line in enumerate(lines):
            event_id = _event_id(source, index)
            timestamp = _line_timestamp(line)
            if not _within_anchor_window(timestamp, anchor_timestamp, seconds=anchor_window_seconds):
                continue
            if _BINDER_WORD_RE.search(line):
                correlation_key = _field(_CORRELATION_RE, line)
                caller_pid = _field(_CALLER_PID_RE, line)
                caller_process = _field(_CALLER_PROCESS_RE, line)
                peer_pid = _field(_PEER_PID_RE, line)
                peer_process = _field(_PEER_PROCESS_RE, line)
                if not correlation_key:
                    _append_gap(result, "binder_correlation_key_unknown")
                elif target is None:
                    rejected_binder_event_ids.append(event_id)
                elif caller_pid != target["pid"] or caller_process != target["name"]:
                    rejected_binder_event_ids.append(event_id)
                elif not peer_pid or not peer_process:
                    binder_events[correlation_key].append({
                        "ordinal": ordinal, "timestamp": timestamp, "sourceEventId": event_id,
                        "state": _binder_state(line), "caller": {"kind": "process", "pid": caller_pid, "name": caller_process},
                        "peer": None, "peerStatus": "unknown",
                    })
                else:
                    binder_events[correlation_key].append({
                        "ordinal": ordinal, "timestamp": timestamp, "sourceEventId": event_id,
                        "state": _binder_state(line),
                        "caller": {"kind": "process", "pid": caller_pid, "name": caller_process},
                        "peer": {"kind": "process", "pid": peer_pid, "name": peer_process},
                        "peerStatus": "verified",
                    })
            if _WATCHDOG_WORD_RE.search(line):
                checker_id = _field(_CHECKER_RE, line)
                if not checker_id:
                    _append_gap(result, "watchdog_checker_identity_unknown")
                else:
                    watchdog_events[checker_id].append({
                        "ordinal": ordinal, "timestamp": timestamp, "sourceEventId": event_id,
                        "state": _watchdog_state(line),
                    })
            ordinal += 1

    if rejected_binder_event_ids:
        _append_gap(result, "binder_transaction_foreign_or_wrong_caller")

    for correlation_key, events in sorted(binder_events.items()):
        ordered_events = _ordered(events)
        verified_events = [event for event in ordered_events if event["peerStatus"] == "verified"]
        unknown_events = [event for event in ordered_events if event["peerStatus"] != "verified"]
        if unknown_events:
            _append_gap(result, "binder_peer_unknown")
        if not verified_events:
            result["binderTransactions"].append({
                "correlationKey": correlation_key,
                "peerStatus": "unknown",
                "lifecycleStatus": "unknown",
                "sourceEventIds": [event["sourceEventId"] for event in ordered_events],
            })
            continue
        first = verified_events[0]
        has_start = any(event["state"] == "started" for event in verified_events)
        has_reply = any(event["state"] == "reply_observed" for event in verified_events)
        lifecycle_status = "reply_observed" if has_reply else "pending" if has_start else "observed"
        transaction = {
            "correlationKey": correlation_key,
            "caller": first["caller"],
            "peer": first["peer"],
            "peerStatus": "verified",
            "lifecycleStatus": lifecycle_status,
            "sourceEventIds": [event["sourceEventId"] for event in verified_events],
        }
        result["binderTransactions"].append(transaction)
        # One explicit, keyed caller/peer observation creates an evidence edge,
        # not a root-cause edge.  A missing reply is deliberately not used to
        # increase causal confidence.
        result["edges"].append({
            "kind": "binder_caller_peer_observation",
            "from": first["caller"],
            "to": first["peer"],
            "correlationKey": correlation_key,
            "lifecycleStatus": lifecycle_status,
            "evidenceEventIds": transaction["sourceEventIds"],
            "causalStatus": "observation_only",
        })

    for checker_id, events in sorted(watchdog_events.items()):
        ordered_events = _ordered(events)
        states = [event["state"] for event in ordered_events]
        try:
            start_index = states.index("started")
        except ValueError:
            start_index = None
        blocked_after_start = (
            start_index is not None
            and any(state == "blocked" for state in states[start_index + 1:])
        )
        finish_after_block = (
            blocked_after_start
            and any(
                state == "finished"
                for state in states[states.index("blocked", start_index + 1) + 1:]
            )
        )
        if finish_after_block:
            status = "recovered"
        elif blocked_after_start:
            status = "terminal_unknown"
            _append_gap(result, "watchdog_terminal_unobserved")
        elif start_index is not None:
            status = "pending"
            _append_gap(result, "watchdog_terminal_unobserved")
        elif "blocked" in states:
            status = "blocked_without_ordered_start"
            _append_gap(result, "watchdog_start_unobserved")
        else:
            status = "observed"
        check = {
            "checkerId": checker_id,
            "lifecycleStatus": status,
            "sourceEventIds": [event["sourceEventId"] for event in ordered_events],
        }
        result["watchdogChecks"].append(check)
        if blocked_after_start:
            result["edges"].append({
                "kind": "watchdog_checker_blocked_observation",
                "checkerId": checker_id,
                "lifecycleStatus": status,
                "evidenceEventIds": check["sourceEventIds"],
                "causalStatus": "observation_only",
            })

    if result["pressure"]["context"]:
        _append_gap(result, "pressure_requires_mechanism_correlation")
    return result
