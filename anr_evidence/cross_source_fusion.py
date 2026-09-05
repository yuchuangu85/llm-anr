"""Entity- and time-scoped corroboration of trace hints.

A regex hit is a retrieval signal, not causal proof. Only a known target,
matching trace identity and an overlapping timestamp window permit promotion.
Unscoped, conflicting and system-wide signals remain context, never proof of
an application's root cause. Raw source evidence is never changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import re
from typing import Any, Iterable

from .log_identity import parse_line_identity
from .log_filter import iter_text_lines, parse_log_timestamp, timestamp_to_raw

# (hint_id, [substring patterns to look for in the source text])
# Each entry promotes the hint by one confidence level if any pattern matches.
_CORROBORATION_RULES: tuple[dict[str, Any], ...] = (
    {
        "hintId": "MAIN_BINDER_WAIT_REPLY",
        "logcatRegexes": [
            r"slow\s+binder\s+transaction",
            r"BinderProxy\.transact.*took",
            r"Watchdog.*system_server",
        ],
        "evidenceLabel": "logcat: Slow binder transaction / Watchdog",
    },
    {
        "hintId": "MAIN_GC_PAUSED",
        "logcatRegexes": [
            r"Background\s+concurrent\s+copying\s+GC\s+freed",
            r"Alloc\s+concurrent\s+copying\s+GC\s+freed",
            r"WaitForGcToComplete\s+blocked",
            r"GC\s+freed.*paused",
        ],
        "evidenceLabel": "logcat: GC freed log",
    },
    {
        "hintId": "MAIN_SP_APPLY_WAIT",
        "logcatRegexes": [
            r"Slow\s+operation.*sp\.commit",
            r"Slow\s+operation.*sp\.apply",
            r"Slow\s+operation.*SharedPreferences",
            r"BackgroundThread.*sp",
        ],
        "evidenceLabel": "logcat: Slow operation SharedPreferences",
    },
    {
        "hintId": "MAIN_RENDER_WAIT_FENCE",
        "logcatRegexes": [
            r"Choreographer.*Skipped\s+\d+\s+frames",
            r"SurfaceFlinger.*Skipped",
            r"fence\s+timeout",
            r"hwui.*frame\s+dropped",
        ],
        "evidenceLabel": "logcat: Choreographer Skipped / fence timeout",
    },
    {
        "hintId": "NATIVE_POLL_BUT_BUSY",
        "logcatRegexes": [
            r"Choreographer.*Skipped\s+\d+\s+frames",
            r"Slow\s+operation:\s+.*Looper",
            r"Slow\s+message:\s+",
            r"BlockGuard.*disk",
        ],
        "evidenceLabel": "logcat: Slow Looper message / Choreographer skipped",
    },
    {
        "hintId": "MAIN_DB_BLOCKED",
        "logcatRegexes": [
            r"SQLiteDatabase.*Slow\s+operation",
            r"sqlite\s+took.*ms",
            r"Slow\s+SQL\s+query",
        ],
        "evidenceLabel": "logcat: Slow SQL query",
    },
    {
        "hintId": "MAIN_NETWORK_BLOCKED",
        "logcatRegexes": [
            r"java\.net\.SocketTimeoutException",
            r"OkHttp.*timeout",
            r"BlockGuard.*network",
        ],
        "evidenceLabel": "logcat: SocketTimeoutException / BlockGuard network",
    },
    {
        "hintId": "SYSTEM_IO_PRESSURE",
        "kernelRegexes": [
            r"hung_task",
            r"INFO:\s+task\s+\S+:\d+\s+blocked",
            r"jbd2.*blocked",
            r"f2fs.*timeout",
            r"emmc.*timeout",
        ],
        "evidenceLabel": "kernel: hung_task / IO timeout",
    },
    {
        "hintId": "SYSTEM_MEMORY_PRESSURE",
        "logcatRegexes": [
            r"lowmemorykiller.*Killing",
            r"oom_reaper:\s+reaped",
            r"Out\s+of\s+memory",
        ],
        "kernelRegexes": [
            r"oom-killer:",
            r"kswapd.*high",
        ],
        "evidenceLabel": "logcat/kernel: lowmemorykiller / oom",
    },
    {
        "hintId": "DEADLOCK_CYCLE",
        "logcatRegexes": [
            r"Watchdog",
            r"system_server.*WAITED",
        ],
        "evidenceLabel": "logcat: Watchdog / WAITED",
    },
)

_CONFIDENCE_LADDER = ("weak", "strong", "critical")


def _promote(confidence: str) -> str:
    try:
        idx = _CONFIDENCE_LADDER.index(confidence)
    except ValueError:
        return confidence
    return _CONFIDENCE_LADDER[min(idx + 1, len(_CONFIDENCE_LADDER) - 1)]


def _scan_for_match(text: str | None, regexes: Iterable[str]) -> str | None:
    if not text:
        return None
    for pattern in regexes:
        try:
            if re.search(pattern, text, re.IGNORECASE):
                return pattern
        except re.error:
            continue
    return None


@dataclass(frozen=True)
class FusionScope:
    """The caller's selected ANR and dump; window bounds are retrieval policy.

    This gate excludes out-of-window dumps. It does not establish process
    lifetime, dump freshness for every Android version, or causal confidence.
    """

    group_id: str
    target_pid: str | int | None
    target_process: str | None
    trace_pid: str | int | None
    trace_process: str | None
    anchor_timestamp: datetime | None
    trace_timestamp: datetime | None
    before_seconds: float = 15
    after_seconds: float = 15
    identity_conflict: bool = False

    def rejection_reason(self) -> str | None:
        if self.identity_conflict:
            return "conflicting_target_identity"
        if not self.group_id or self.anchor_timestamp is None:
            return "missing_anchor"
        if not all(math.isfinite(n) and n >= 0 for n in (self.before_seconds, self.after_seconds)):
            return "invalid_window"
        if self.target_pid is None or not str(self.target_pid).isdigit() or not self.target_process:
            return "unknown_target"
        if self.trace_pid is None or not self.trace_process:
            return "unknown_trace_identity"
        if str(self.target_pid) != str(self.trace_pid) or self.target_process != self.trace_process:
            return "wrong_trace_identity"
        if self.trace_timestamp is None:
            return "unknown_trace_timestamp"
        if not self.contains(self.trace_timestamp):
            return "dump_outside_window"
        return None

    def contains(self, timestamp: datetime) -> bool:
        if self.anchor_timestamp is None:
            return False
        delta = (timestamp - self.anchor_timestamp).total_seconds()
        return -self.before_seconds <= delta <= self.after_seconds


def _line_rejection(line: str, timestamp: datetime | None, scope: FusionScope | None,
                    scope_reason: str | None) -> str | None:
    if scope_reason:
        return scope_reason
    if scope is None:
        return "missing_scope"
    if timestamp is None:
        return "unknown_timestamp"
    if not scope.contains(timestamp):
        return "outside_window"
    identity = parse_line_identity(line)
    target_pid = str(scope.target_pid)
    # An explicit target overrides the emitter; never treat a thread ID as PID.
    if identity.target_pids:
        if identity.target_pids != (target_pid,):
            return "conflicting_target"
    elif identity.emitter_pid != target_pid:
        return "unresolved_target"
    if identity.target_processes and identity.target_processes != (scope.target_process,):
        return "conflicting_process"
    return None


def fuse_cross_source_evidence(
    hints: list[dict[str, Any]],
    *,
    logcat_text: str | None = None,
    event_log_text: str | None = None,
    kernel_log_text: str | None = None,
    scope: FusionScope | None = None,
) -> list[dict[str, Any]]:
    """Return new hints with bounded, auditable corroboration/context records.

    Legacy calls without scope remain valid but cannot promote confidence.
    lineIndex identifies the supplied source view, NOT an immutable raw-file
    offset. Each rule/source retains at most one corroboration and one context
    example; full baseline evidence stays with the caller.
    """
    if not hints:
        return []
    scope_reason = scope.rejection_reason() if scope else "missing_scope"
    rules_by_id: dict[str, list[dict[str, Any]]] = {}
    for rule in _CORROBORATION_RULES:
        rules_by_id.setdefault(rule["hintId"], []).append(rule)

    promoted: list[dict[str, Any]] = []
    for hint in hints:
        new_hint = dict(hint)
        triggered: list[dict[str, Any]] = []
        context: list[dict[str, Any]] = []
        for rule in rules_by_id.get(hint["id"], []):
            for source_name, source_text, key in (
                ("logcat", logcat_text, "logcatRegexes"),
                ("eventLog", event_log_text, "eventLogRegexes"),
                ("kernelLog", kernel_log_text, "kernelRegexes"),
            ):
                regexes = rule.get(key, [])
                if not regexes or not source_text:
                    continue
                context_recorded = False
                for index, line in enumerate(iter_text_lines(source_text)):
                    hit = _scan_for_match(line, regexes)
                    if not hit:
                        continue
                    timestamp = (parse_log_timestamp(line, year=scope.anchor_timestamp.year)
                                 if scope and scope.anchor_timestamp else parse_log_timestamp(line))
                    reason = _line_rejection(line, timestamp, scope, scope_reason)
                    if reason is None and hint["id"].startswith("SYSTEM_"):
                        reason = "system_pressure_context"
                    record = {
                        "source": source_name, "regex": hit,
                        "label": rule.get("evidenceLabel", ""), "rawLine": line,
                        "lineIndex": index, "lineIndexScope": "supplied_source_view",
                        "groupId": scope.group_id if scope else None,
                        "timestamp": timestamp_to_raw(timestamp) if timestamp else None,
                        "deltaTSeconds": ((timestamp - scope.anchor_timestamp).total_seconds()
                                          if timestamp and scope and scope.anchor_timestamp else None),
                        "evidenceRole": "context" if reason else "corroboration",
                        "reason": reason or "target_and_time_match",
                    }
                    if reason is None:
                        triggered.append(record)
                        break
                    if not context_recorded:
                        context.append(record)
                        context_recorded = True
        if triggered:
            old_conf = new_hint.get("confidence", "weak")
            new_hint["confidence"] = _promote(old_conf)
            new_hint["confidencePromotedFrom"] = old_conf
            new_hint["corroboratingEvidence"] = list(new_hint.get("corroboratingEvidence", [])) + triggered
        if context:
            new_hint["contextEvidence"] = list(new_hint.get("contextEvidence", [])) + context
        promoted.append(new_hint)
    return promoted


__all__ = ["FusionScope", "fuse_cross_source_evidence"]
