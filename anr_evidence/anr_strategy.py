"""ANR-type specific filtering and analysis strategy registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .constants import SUPPORTED_TYPES, TYPE_PATTERNS
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
BROADCAST_LOGCAT_PATTERNS = GENERIC_LOGCAT_PATTERNS | frozenset({"broadcastqueue", "broadcast of intent", "onreceive", "goasync", "finishreceiver"})
SERVICE_LOGCAT_PATTERNS = GENERIC_LOGCAT_PATTERNS | frozenset({"timeout executing service", "executing service", "foreground service", "fgs", "service timeout"})
PROVIDER_LOGCAT_PATTERNS = GENERIC_LOGCAT_PATTERNS | frozenset({"contentprovider", "content provider", "provider not responding", "publishing content providers"})
JOB_LOGCAT_PATTERNS = GENERIC_LOGCAT_PATTERNS | frozenset({"jobscheduler", "jobservice", "onstartjob", "onstopjob", "jobservicecontext"})
WATCHDOG_LOGCAT_PATTERNS = GENERIC_LOGCAT_PATTERNS | frozenset({"watchdog", "swt", "system_server", "blocked in handler", "monitor checker"})

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
    "broadcast_timeout": AnrTypeStrategy(
        anr_type="broadcast_timeout",
        label="Broadcast timeout",
        event_before_seconds=90,
        logcat_before_seconds=90,
        logcat_after_seconds=30,
        group_tolerance_seconds=5,
        event_tags=GENERIC_EVENT_TAGS,
        logcat_patterns=BROADCAST_LOGCAT_PATTERNS,
        fallback_anchor_patterns={
            "trace": ("broadcast", "onreceive", "goasync", "finish"),
            "logcat": ("am_anr", "broadcastqueue", "broadcast of intent", "receiver", "goasync"),
            "kernel_log": ("binder", "sched", "hung task", "blocked for more than"),
        },
        analysis_focus=(
            "BroadcastQueue timeout / Broadcast of Intent 的触发链路",
            "同步 onReceive 默认检查主线程；goAsync 必须检查 PendingResult finish 与工作线程",
            "区分 receiver 业务耗时、binder/lock/IO 阻塞和系统高负载放大因素",
        ),
    ),
    "service_timeout": AnrTypeStrategy(
        anr_type="service_timeout",
        label="Service timeout",
        event_before_seconds=240,
        logcat_before_seconds=240,
        logcat_after_seconds=30,
        group_tolerance_seconds=5,
        event_tags=GENERIC_EVENT_TAGS,
        logcat_patterns=SERVICE_LOGCAT_PATTERNS,
        fallback_anchor_patterns={
            "trace": ("service", "oncreate", "onstartcommand", "foreground service"),
            "logcat": ("am_anr", "timeout executing service", "executing service", "foreground service", "service timeout"),
            "kernel_log": ("binder", "sched", "hung task", "blocked for more than"),
        },
        analysis_focus=(
            "Service lifecycle / foreground service start / cold start timeout",
            "首要检查目标进程 main thread 是否阻塞 onCreate/onStartCommand/onBind",
            "用较长窗口保留 service 启动链和冷启动前序证据",
        ),
    ),
    "content_provider_timeout": AnrTypeStrategy(
        anr_type="content_provider_timeout",
        label="ContentProvider timeout",
        event_before_seconds=60,
        logcat_before_seconds=60,
        logcat_after_seconds=30,
        group_tolerance_seconds=5,
        event_tags=GENERIC_EVENT_TAGS,
        logcat_patterns=PROVIDER_LOGCAT_PATTERNS,
        fallback_anchor_patterns={
            "trace": ("contentprovider", "content provider", "provider", "binder"),
            "logcat": ("am_anr", "provider not responding", "publishing content providers", "contentprovider"),
            "kernel_log": ("binder", "sched", "hung task", "blocked for more than"),
        },
        analysis_focus=(
            "区分 provider publish timeout 与 provider query / CRUD not responding",
            "publish 优先看 provider 进程 main/cold-start；query 优先看 provider Binder 线程和远端进程",
            "ContentProvider 证据不足时保持 partial/degraded，不要套用 input timeout 结论",
        ),
    ),
    "job_scheduler_timeout": AnrTypeStrategy(
        anr_type="job_scheduler_timeout",
        label="JobScheduler timeout",
        event_before_seconds=120,
        logcat_before_seconds=120,
        logcat_after_seconds=30,
        group_tolerance_seconds=5,
        event_tags=GENERIC_EVENT_TAGS,
        logcat_patterns=JOB_LOGCAT_PATTERNS,
        fallback_anchor_patterns={
            "trace": ("jobservice", "onstartjob", "onstopjob", "jobscheduler"),
            "logcat": ("am_anr", "jobscheduler", "jobservice", "onstartjob", "onstopjob"),
            "kernel_log": ("binder", "sched", "hung task", "blocked for more than"),
        },
        analysis_focus=(
            "JobService onStartJob/onStopJob 主线程超时链路",
            "检查 JobScheduler 调度、service bind/start 和 main thread lifecycle 方法",
            "区分 job 回调业务耗时与系统负载或远端 binder 放大因素",
        ),
    ),
    "system_watchdog_swt": AnrTypeStrategy(
        anr_type="system_watchdog_swt",
        label="System Watchdog/SWT",
        event_before_seconds=300,
        logcat_before_seconds=300,
        logcat_after_seconds=60,
        group_tolerance_seconds=10,
        event_tags=GENERIC_EVENT_TAGS,
        logcat_patterns=WATCHDOG_LOGCAT_PATTERNS,
        fallback_anchor_patterns={
            "trace": ("watchdog", "system_server", "monitor", "blocked in handler"),
            "logcat": ("watchdog", "swt", "system_server", "blocked in handler", "monitor checker"),
            "kernel_log": ("watchdog", "sched", "hung task", "blocked for more than"),
        },
        analysis_focus=(
            "system_server watchdog/SWT 触发链路，不按普通 app ANR 归因",
            "优先检查 watchdog-monitored Handler、锁、Binder 线程和 system_server trace",
            "明确 app/system/remote 边界；缺少 system_server 证据时只能给候选链路",
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
