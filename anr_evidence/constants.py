"""Constants for Phase 1 ANR evidence extraction."""

SUPPORTED_TYPES = {
    "no_focus_window": "No focus window",
    "input_dispatching_timeout": "Input dispatching timeout",
    "broadcast_timeout": "Broadcast timeout",
    "service_timeout": "Service timeout",
    "content_provider_timeout": "ContentProvider timeout",
    "job_scheduler_timeout": "JobScheduler timeout",
    "system_watchdog_swt": "System Watchdog/SWT",
}

SOURCE_KINDS = ("trace", "event_log", "logcat", "kernel_log")
OPTIONAL_SOURCE_KINDS = ("meminfo",)

TYPE_PATTERNS = {
    "no_focus_window": (
        "no focused window",
        "no focus window",
        "focused window is null",
        "application does not have a focused window",
        "no window has focus",
    ),
    "input_dispatching_timeout": (
        "input dispatching timed out",
        "input dispatching timeout",
        "waiting because the touched window has not finished processing",
    ),
    "broadcast_timeout": (
        "broadcastqueue timeout",
        "broadcast queue timeout",
        "broadcast of intent",
        "timeout of broadcast",
        "receiver during timeout",
        "finishreceiver timeout",
    ),
    "service_timeout": (
        "timeout executing service",
        "executing service",
        "foreground service did not start in time",
        "fgs did not start",
    ),
    "content_provider_timeout": (
        "timeout publishing content providers",
        "timeout publishing content provider",
        "contentprovider not responding",
        "content provider not responding",
        "provider not responding",
        "contentprovider timeout",
    ),
    "job_scheduler_timeout": (
        "jobservice timeout",
        "job scheduler timeout",
        "jobscheduler timeout",
        "onstartjob timeout",
        "onstopjob timeout",
        "jobservicecontext timeout",
    ),
    "system_watchdog_swt": (
        "watchdog timeout",
        "system_server watchdog",
        "watchdog detected",
        "swt watchdog",
        "swt timeout",
        "blocked in handler on foreground thread",
    ),
}

TIME_ANCHOR_PRECEDENCE = ("event_log", "trace", "logcat", "kernel_log")

DEFAULT_WINDOWS = {
    "event_log_pre_lines": 3,
    "event_log_before_seconds": 12,
    "logcat_before_seconds": 15,
    "logcat_after_seconds": 15,
    "kernel_before_seconds": 15,
    "kernel_after_seconds": 15,
}
