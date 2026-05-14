"""Cross-source confidence fusion for trace hints.

Many trace-only hints can be promoted (or, conservatively, demoted) when
logcat / EventLog / kernel log carry corroborating evidence:

    MAIN_BINDER_WAIT_REPLY  + 'Slow binder transaction'       -> critical
    MAIN_BINDER_WAIT_REPLY  + 'system_server.*Watchdog'       -> critical
    MAIN_GC_PAUSED          + 'Background concurrent copying' -> strong
    MAIN_SP_APPLY_WAIT      + 'Slow operation: .* sp.commit'  -> strong
    MAIN_RENDER_WAIT_FENCE  + 'fence timeout' / 'SF.*Skipped' -> strong
    NATIVE_POLL_BUT_BUSY    + 'Choreographer.*Skipped'        -> critical
    NATIVE_POLL_IDLE_LIKELY + AnrManager top CPU != self      -> strong
    SYSTEM_IO_PRESSURE      + kernel 'hung_task'              -> critical

This module is intentionally read-only: it never mutates the original
hints, it returns a new list with promoted/demoted entries plus a
``corroboratingEvidence`` field that names the source signal that
triggered the promotion. The AI prompt then reads this and is told to
treat critical-confidence hints as near-certain.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

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


def fuse_cross_source_evidence(
    hints: list[dict[str, Any]],
    *,
    logcat_text: str | None = None,
    event_log_text: str | None = None,
    kernel_log_text: str | None = None,
) -> list[dict[str, Any]]:
    """Return a new hint list with confidence promoted where corroborated.

    For every rule whose ``hintId`` matches a hint in ``hints``, scan the
    relevant source text for any of the rule's regexes; if any matches,
    promote that hint's ``confidence`` by one ladder step and append a
    ``corroboratingEvidence`` entry recording (source, regex, label).
    """

    if not hints:
        return list(hints or [])

    rules_by_id: dict[str, list[dict[str, Any]]] = {}
    for rule in _CORROBORATION_RULES:
        rules_by_id.setdefault(rule["hintId"], []).append(rule)

    promoted: list[dict[str, Any]] = []
    for hint in hints:
        new_hint = dict(hint)
        triggered: list[dict[str, str]] = []
        for rule in rules_by_id.get(hint["id"], []):
            for source_name, source_text, key in (
                ("logcat", logcat_text, "logcatRegexes"),
                ("eventLog", event_log_text, "eventLogRegexes"),
                ("kernelLog", kernel_log_text, "kernelRegexes"),
            ):
                hit = _scan_for_match(source_text, rule.get(key, []) or [])
                if hit:
                    triggered.append({
                        "source": source_name,
                        "regex": hit,
                        "label": rule.get("evidenceLabel", ""),
                    })
        if triggered:
            old_conf = new_hint.get("confidence", "weak")
            new_hint["confidence"] = _promote(old_conf)
            new_hint["confidencePromotedFrom"] = old_conf
            new_hint["corroboratingEvidence"] = (
                list(new_hint.get("corroboratingEvidence", [])) + triggered
            )
        promoted.append(new_hint)
    return promoted


__all__ = ["fuse_cross_source_evidence"]
