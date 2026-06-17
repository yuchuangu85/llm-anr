"""Conservative root-cause pattern hint inference for ANR evidence.

These hints are intentionally non-final: they enrich evidence packages and AI
context prompts without replacing trigger-type classification or claiming a root
cause.  Keep this module pattern-based and additive so Phase 1/2 contracts stay
recall-first and deterministic.
"""

from __future__ import annotations

from typing import Any, Iterable

ROOT_CAUSE_PATTERN_LABELS = {
    "deadlock": "Deadlock or lock-chain contention",
    "memory_leak_oom_pressure": "Memory leak / OOM / memory pressure candidate",
    "high_load_anr": "High CPU/IO/load or scheduler pressure candidate",
}

_HINT_PATTERNS: dict[str, tuple[str, ...]] = {
    "deadlock": (
        "deadlock",
        "waiting to lock",
        "held by tid",
        "held by thread",
        "long monitor contention",
        "dvm_lock_sample",
        "monitor contention",
        "mutex contention",
    ),
    "memory_leak_oom_pressure": (
        "out of memory",
        "oom",
        "lowmemorykiller",
        "lmkd",
        "kswapd",
        "memory pressure",
        "psi memory",
        "pressure/memory",
        "allocation failed",
        "gc concurrent",
        "waitforgctocomplete",
        "low memory",
        "anon rss",
        "pss",
    ),
    "high_load_anr": (
        "cpu usage",
        "cpu total",
        " total:",
        "load:",
        "iowait",
        "cpu saturated",
        "system_cpu_saturated",
        "anr_process_cpu_high",
        "system_server_cpu_high",
        "system_io_pressure",
        "high_cpu_process_over_90",
        "anr_process_cpu_critical",
        "sched",
        "runnable pressure",
        "high load",
        "blocked for more than",
    ),
}

_HINT_IDS: dict[str, str] = {
    "DEADLOCK_CYCLE": "deadlock",
    "DEADLOCK_LIKELY": "deadlock",
    "DEADLOCK_SELF": "deadlock",
    "LOCK_OWNER_BLOCKED": "deadlock",
    "LOCK_OWNER_SLEEPING": "deadlock",
    "LOCK_CONTENTION_BLOCKED": "deadlock",
    "SYSTEM_MEMORY_PRESSURE": "memory_leak_oom_pressure",
    "ANR_PROCESS_CPU_HIGH": "high_load_anr",
    "ANR_PROCESS_CPU_CRITICAL": "high_load_anr",
    "HIGH_CPU_PROCESS_OVER_90": "high_load_anr",
    "SYSTEM_CPU_SATURATED": "high_load_anr",
    "SYSTEM_SERVER_CPU_HIGH": "high_load_anr",
    "SYSTEM_IO_PRESSURE": "high_load_anr",
}


def infer_root_cause_pattern_hints(package_or_sources: dict[str, Any]) -> list[str]:
    """Infer non-final root-cause pattern hints from raw source text.

    Accepts either a package with ``sources`` or a plain sources mapping.  The
    output is stable, sorted in policy order, and contains only known hint ids.
    """

    sources = package_or_sources.get("sources", package_or_sources)
    texts: list[str] = []
    if isinstance(sources, dict):
        for source in sources.values():
            if isinstance(source, dict):
                texts.append(str(source.get("content", "")))
            else:
                texts.append(str(source))
    return infer_root_cause_pattern_hints_from_texts(texts)


def infer_root_cause_pattern_hints_from_texts(texts: Iterable[str]) -> list[str]:
    blob = "\n".join(texts).lower()
    found = {
        hint
        for hint, patterns in _HINT_PATTERNS.items()
        if any(pattern in blob for pattern in patterns)
    }
    return _ordered(found)


def infer_root_cause_pattern_hints_from_ids(hints: Iterable[dict[str, Any]]) -> list[str]:
    found: set[str] = set()
    for hint in hints:
        mapped = _HINT_IDS.get(str(hint.get("id", "")))
        if mapped:
            found.add(mapped)
    return _ordered(found)


def merge_root_cause_pattern_hints(*hint_lists: Iterable[str]) -> list[str]:
    found: set[str] = set()
    for hints in hint_lists:
        found.update(h for h in hints if h in ROOT_CAUSE_PATTERN_LABELS)
    return _ordered(found)


def root_cause_hint_details(hints: Iterable[str]) -> list[dict[str, str]]:
    return [
        {"id": hint, "label": ROOT_CAUSE_PATTERN_LABELS[hint], "kind": "candidate_hint"}
        for hint in _ordered(set(hints))
    ]


def _ordered(hints: Iterable[str]) -> list[str]:
    found = set(hints)
    return [hint for hint in ROOT_CAUSE_PATTERN_LABELS if hint in found]


__all__ = [
    "ROOT_CAUSE_PATTERN_LABELS",
    "infer_root_cause_pattern_hints",
    "infer_root_cause_pattern_hints_from_ids",
    "infer_root_cause_pattern_hints_from_texts",
    "merge_root_cause_pattern_hints",
    "root_cause_hint_details",
]
