"""ANR-type specific filtering and analysis strategy registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .constants import DEFAULT_WINDOWS, SUPPORTED_TYPES, TYPE_PATTERNS
from .log_filter import DEFAULT_EVENT_LOG_TAGS, LOGCAT_SIGNAL_PATTERNS


@dataclass(frozen=True)
class AnrTypeStrategy:
    anr_type: str
    label: str
    event_before_seconds: int
    logcat_before_seconds: int
    logcat_after_seconds: int
    group_tolerance_seconds: int
    event_tags: frozenset[str]
    logcat_patterns: frozenset[str]
    fallback_anchor_patterns: dict[str, tuple[str, ...]]
    analysis_focus: tuple[str, ...]


INPUT_EVENT_TAGS = frozenset(
    {
        "am_anr",
        "wm_focus",
        "input_focus",
        "input_interaction",
        "input_cancel",
        "wm_set_resumed_activity",
        "wm_focused_root_task",
        "wm_activity_launch_time",
    }
) | DEFAULT_EVENT_LOG_TAGS

INPUT_LOGCAT_PATTERNS = frozenset(
    {
        "anr",
        "am_anr",
        "anrmanager",
        "inputdispatcher",
        "input dispatching timed out",
        "input dispatching timeout",
        "focused window",
        "no focused window",
        "no focus window",
        "windowmanager",
        "activitymanager",
        "waiting because the touched window has not finished processing",
        "not responding",
    }
)

GENERIC_EVENT_TAGS = DEFAULT_EVENT_LOG_TAGS
GENERIC_LOGCAT_PATTERNS = LOGCAT_SIGNAL_PATTERNS | frozenset({"anr", "timeout", "not responding", "activitymanager"})

ANR_TYPE_STRATEGIES: dict[str, AnrTypeStrategy] = {
    "input_dispatching_timeout": AnrTypeStrategy(
        anr_type="input_dispatching_timeout",
        label="Input dispatching timeout",
        event_before_seconds=12,
        logcat_before_seconds=15,
        logcat_after_seconds=15,
        group_tolerance_seconds=3,
        event_tags=INPUT_EVENT_TAGS,
        logcat_patterns=INPUT_LOGCAT_PATTERNS,
        fallback_anchor_patterns={
            "trace": ("anr", "input dispatching", "focused window", "no focus window"),
            "logcat": ("am_anr", "anr", "inputdispatcher", "input dispatching", "focused window"),
            "kernel_log": ("binder", "sched", "hung task", "blocked for more than", "input"),
        },
        analysis_focus=(
            "input dispatch timeout / no-focus-window 直接触发链路",
            "主线程是否卡在 binder、lock、render/gpu、native poll 或 runnable pressure",
            "EventLog 中 focus/input/am_anr 顺序与 logcat InputDispatcher 证据是否一致",
        ),
    ),
    "no_focus_window": AnrTypeStrategy(
        anr_type="no_focus_window",
        label="No focus window",
        event_before_seconds=12,
        logcat_before_seconds=15,
        logcat_after_seconds=15,
        group_tolerance_seconds=3,
        event_tags=INPUT_EVENT_TAGS,
        logcat_patterns=INPUT_LOGCAT_PATTERNS,
        fallback_anchor_patterns={
            "trace": ("anr", "focused window", "no focus window", "no focused window"),
            "logcat": ("am_anr", "anr", "inputdispatcher", "focused window", "no focus window"),
            "kernel_log": ("binder", "sched", "input"),
        },
        analysis_focus=(
            "窗口焦点缺失、焦点切换和 Activity/WindowManager 生命周期顺序",
            "主线程是否阻塞导致窗口未及时创建/恢复/获取焦点",
            "区分直接 no-focus 触发与底层性能/锁等待放大因素",
        ),
    ),
    "unknown": AnrTypeStrategy(
        anr_type="unknown",
        label="Unknown or future ANR type",
        event_before_seconds=30,
        logcat_before_seconds=30,
        logcat_after_seconds=15,
        group_tolerance_seconds=5,
        event_tags=GENERIC_EVENT_TAGS,
        logcat_patterns=GENERIC_LOGCAT_PATTERNS,
        fallback_anchor_patterns={
            "trace": ("anr", "timeout", "not responding", "blocked", "waiting"),
            "logcat": ("am_anr", "anr", "timeout", "not responding", "activitymanager"),
            "kernel_log": ("binder", "sched", "hung task", "blocked for more than", "oom", "pressure"),
        },
        analysis_focus=(
            "先识别 ANR 类型和直接触发条件，不要套用 input timeout 专用结论",
            "按证据来源建立时间线并标注缺失证据",
            "只输出被 trace/EventLog/logcat 支撑的候选链路",
        ),
    ),
}


def infer_anr_type(package: dict[str, Any], explicit_type: str | None = None) -> str:
    """Infer the best-known ANR type without running the full extraction pipeline."""

    if explicit_type:
        return explicit_type if explicit_type in ANR_TYPE_STRATEGIES else "unknown"
    provided_type = package.get("provided_type")
    if provided_type in SUPPORTED_TYPES:
        return str(provided_type)
    scores = {anr_type: 0 for anr_type in TYPE_PATTERNS}
    for source in package.get("sources", {}).values():
        content = source.get("content", "").lower()
        for anr_type, patterns in TYPE_PATTERNS.items():
            scores[anr_type] += sum(content.count(pattern) for pattern in patterns)
    matched = [anr_type for anr_type, score in scores.items() if score > 0]
    return matched[0] if len(matched) == 1 else "unknown"


def strategy_for_package(package: dict[str, Any], explicit_type: str | None = None) -> AnrTypeStrategy:
    return ANR_TYPE_STRATEGIES.get(infer_anr_type(package, explicit_type), ANR_TYPE_STRATEGIES["unknown"])
