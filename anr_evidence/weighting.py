"""Semantic weighting for ANR evidence tags.

Assigns a 3-level importance hierarchy to every log tag / filter pattern used
in the pipeline, enabling importance-aware filtering, context flooding
prevention, and structured evidence slice construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .log_filter import DEFAULT_EVENT_LOG_TAGS

if TYPE_CHECKING:
    from .anr_strategy import AnrTypeStrategy
    from .log_filter import LogFilterSpec


class ImportanceLevel(Enum):
    """Three-level tag importance hierarchy."""

    CRITICAL = "critical"       # Direct ANR trigger signals — must always include
    WARNING = "warning"         # Performance pressure indicators — include by default
    CONTEXTUAL = "contextual"   # Background / environmental detail — drop when budget tight

    def __lt__(self, other: "ImportanceLevel") -> bool:
        order = {ImportanceLevel.CRITICAL: 0, ImportanceLevel.WARNING: 1, ImportanceLevel.CONTEXTUAL: 2}
        return order[self] < order[other]

    def __le__(self, other: "ImportanceLevel") -> bool:
        order = {ImportanceLevel.CRITICAL: 0, ImportanceLevel.WARNING: 1, ImportanceLevel.CONTEXTUAL: 2}
        return order[self] <= order[other]


@dataclass(frozen=True)
class TagWeight:
    """A single tag with its importance classification."""

    tag: str
    level: ImportanceLevel
    description: str


# ══════════════════════════════════════════════════════════════════════════════
# Pre-built weighted dictionaries for all 60+ tags across three source kinds
# ══════════════════════════════════════════════════════════════════════════════

def categorize_event_log_tags() -> dict[str, ImportanceLevel]:
    """Classify documented EventLog tags into 3 importance levels."""

    weights = {
        # ── CRITICAL: Direct ANR trigger and immediate cause signals ──
        "am_anr": ImportanceLevel.CRITICAL,
        "am_proc_died": ImportanceLevel.CRITICAL,
        "am_kill": ImportanceLevel.CRITICAL,
        "am_proc_start": ImportanceLevel.WARNING,
        "am_proc_bound": ImportanceLevel.WARNING,
        "am_proc_bad": ImportanceLevel.WARNING,
        "am_proc_good": ImportanceLevel.CONTEXTUAL,
        "am_pre_boot": ImportanceLevel.CONTEXTUAL,
        "am_mem_factor": ImportanceLevel.WARNING,
        "am_meminfo": ImportanceLevel.WARNING,
        "am_pss": ImportanceLevel.WARNING,
        "am_uid_running": ImportanceLevel.WARNING,
        "am_uid_active": ImportanceLevel.WARNING,
        "am_uid_idle": ImportanceLevel.WARNING,
        "am_uid_stopped": ImportanceLevel.WARNING,
        "am_freeze": ImportanceLevel.WARNING,
        "am_unfreeze": ImportanceLevel.WARNING,
        # ── Window / focus related ──
        "wm_task_to_front": ImportanceLevel.CRITICAL,
        "wm_task_created": ImportanceLevel.WARNING,
        "wm_task_moved": ImportanceLevel.WARNING,
        "wm_create_task": ImportanceLevel.WARNING,
        "wm_create_activity": ImportanceLevel.WARNING,
        "wm_restart_activity": ImportanceLevel.WARNING,
        "wm_resume_activity": ImportanceLevel.WARNING,
        "wm_pause_activity": ImportanceLevel.CRITICAL,
        "wm_remove_task": ImportanceLevel.WARNING,
        "wm_finish_activity": ImportanceLevel.WARNING,
        "wm_destroy_activity": ImportanceLevel.WARNING,
        "wm_new_intent": ImportanceLevel.WARNING,
        "wm_activity_launch_time": ImportanceLevel.WARNING,
        "wm_add_to_stopping": ImportanceLevel.WARNING,
        "wm_failed_to_pause": ImportanceLevel.WARNING,
        "wm_set_resumed_activity": ImportanceLevel.CRITICAL,
        "wm_on_resume_called": ImportanceLevel.CRITICAL,
        "wm_on_paused_called": ImportanceLevel.CRITICAL,
        "wm_on_stop_called": ImportanceLevel.WARNING,
        "wm_on_top_resumed_gained_called": ImportanceLevel.CRITICAL,
        "wm_on_top_resumed_lost_called": ImportanceLevel.CRITICAL,
        "wm_focused_root_task": ImportanceLevel.CRITICAL,
        "wm_stop_activity": ImportanceLevel.WARNING,
        "wm_focus": ImportanceLevel.CRITICAL,
        "wm_wallpaper_surface": ImportanceLevel.CONTEXTUAL,
        # ── Input dispatch ──
        "input_interaction": ImportanceLevel.CRITICAL,
        "input_focus": ImportanceLevel.CRITICAL,
        "input_cancel": ImportanceLevel.WARNING,
        # ── Battery / power ──
        "battery_level": ImportanceLevel.WARNING,
        "battery_status": ImportanceLevel.WARNING,
        "battery_discharge": ImportanceLevel.WARNING,
        "power_sleep_continuous": ImportanceLevel.WARNING,
        "power_screen_broadcast_send": ImportanceLevel.WARNING,
        "power_screen_state": ImportanceLevel.WARNING,
        "power_partial_wake_state": ImportanceLevel.WARNING,
        # ── User session ──
        "ssm_user_starting": ImportanceLevel.WARNING,
        "ssm_user_switching": ImportanceLevel.WARNING,
        "ssm_user_unlocking": ImportanceLevel.WARNING,
    }
    for tag in DEFAULT_EVENT_LOG_TAGS:
        weights.setdefault(tag, ImportanceLevel.CONTEXTUAL)
    return weights


def categorize_logcat_patterns() -> dict[str, ImportanceLevel]:
    """Classify all 13 LOGCAT_SIGNAL_PATTERNS into 3 importance levels."""
    return {
        "anr": ImportanceLevel.CRITICAL,
        "am_anr": ImportanceLevel.CRITICAL,
        "anrmanager": ImportanceLevel.CRITICAL,
        "inputdispatcher": ImportanceLevel.CRITICAL,
        "input dispatching timed out": ImportanceLevel.CRITICAL,
        "focused window": ImportanceLevel.CRITICAL,
        "activitymanager": ImportanceLevel.WARNING,
        "windowmanager": ImportanceLevel.WARNING,
        "broadcastqueue": ImportanceLevel.WARNING,
        "contentprovider": ImportanceLevel.WARNING,
        "system_server": ImportanceLevel.WARNING,
        "slow operation": ImportanceLevel.WARNING,
        "timeout": ImportanceLevel.WARNING,
        "not responding": ImportanceLevel.WARNING,
    }


def categorize_kernel_patterns() -> dict[str, ImportanceLevel]:
    """Classify all 13 KERNEL_SIGNAL_PATTERNS into 3 importance levels."""
    return {
        "binder": ImportanceLevel.CRITICAL,
        "hung task": ImportanceLevel.CRITICAL,
        "blocked for more than": ImportanceLevel.CRITICAL,
        "lowmemorykiller": ImportanceLevel.CRITICAL,
        "oom": ImportanceLevel.CRITICAL,
        "watchdog": ImportanceLevel.CRITICAL,
        "sched": ImportanceLevel.WARNING,
        "lmkd": ImportanceLevel.WARNING,
        "psi": ImportanceLevel.WARNING,
        "pressure": ImportanceLevel.WARNING,
        "kworker": ImportanceLevel.WARNING,
        "irq": ImportanceLevel.WARNING,
        "input": ImportanceLevel.WARNING,
    }


# Pre-built lookup dicts
EVENT_LOG_TAG_WEIGHTS: dict[str, ImportanceLevel] = categorize_event_log_tags()
LOGCAT_SIGNAL_WEIGHTS: dict[str, ImportanceLevel] = categorize_logcat_patterns()
KERNEL_SIGNAL_WEIGHTS: dict[str, ImportanceLevel] = categorize_kernel_patterns()


def get_weights_for_source(source_kind: str) -> dict[str, ImportanceLevel]:
    """Return the pre-built weight dict for a given source kind."""
    if source_kind == "event_log":
        return EVENT_LOG_TAG_WEIGHTS
    if source_kind in ("logcat", "anr_manager"):
        return LOGCAT_SIGNAL_WEIGHTS
    if source_kind == "kernel_log":
        return KERNEL_SIGNAL_WEIGHTS
    return {}


def get_importance(tag: str, source_kind: str) -> ImportanceLevel:
    """Look up the importance of a tag in a given source kind.

    Returns CONTEXTUAL for unrecognized tags (safe default).
    """
    weights = get_weights_for_source(source_kind)
    return weights.get(tag.lower(), ImportanceLevel.CONTEXTUAL)


def resolve_tag(line: str, patterns: frozenset[str]) -> str | None:
    """Find which pattern from the set matches the line (case-insensitive)."""
    lowered = line.lower()
    for pattern in sorted(patterns, key=len, reverse=True):
        if pattern.lower() in lowered:
            return pattern
    return None


def filter_by_importance(
    lines: list[str],
    weights: dict[str, ImportanceLevel],
    patterns: frozenset[str],
    *,
    min_level: ImportanceLevel = ImportanceLevel.WARNING,
) -> list[str]:
    """Filter lines to those matching tags at or above min_level importance.

    Lines that don't match any known tag are retained (conservative default).
    """
    result: list[str] = []
    for line in lines:
        tag = resolve_tag(line, patterns)
        if tag is None:
            result.append(line)
            continue
        level = weights.get(tag.lower(), ImportanceLevel.CONTEXTUAL)
        if level <= min_level:
            result.append(line)
    return result


@dataclass(frozen=True)
class WeightedFilterSpec:
    """Extended filter specification with importance weighting."""

    spec: "LogFilterSpec"
    tag_weights: dict[str, ImportanceLevel]


def weighted_filter_spec_for_strategy(
    strategy: "AnrTypeStrategy",
    spec: "LogFilterSpec",
) -> WeightedFilterSpec:
    """Create a WeightedFilterSpec from an AnrTypeStrategy and LogFilterSpec.

    Maps the strategy's event_tags to EVENT_LOG_TAG_WEIGHTS and logcat_patterns
    to LOGCAT_SIGNAL_WEIGHTS.
    """
    combined: dict[str, ImportanceLevel] = {}
    for tag in strategy.event_tags:
        combined[tag] = EVENT_LOG_TAG_WEIGHTS.get(tag, ImportanceLevel.CONTEXTUAL)
    for pattern in strategy.logcat_patterns:
        combined[pattern] = LOGCAT_SIGNAL_WEIGHTS.get(pattern, ImportanceLevel.CONTEXTUAL)
    for key in strategy.fallback_anchor_patterns:
        for pattern in strategy.fallback_anchor_patterns.get(key, ()):
            combined[pattern] = ImportanceLevel.CRITICAL
    return WeightedFilterSpec(spec=spec, tag_weights=combined)
