"""Constants for Phase 1 ANR evidence extraction."""

SUPPORTED_TYPES = {
    "no_focus_window": "No focus window",
    "input_dispatching_timeout": "Input dispatching timeout",
}

SOURCE_KINDS = ("trace", "event_log", "logcat", "kernel_log")
OPTIONAL_SOURCE_KINDS = ("meminfo",)

TYPE_PATTERNS = {
    "no_focus_window": (
        "no focused window",
        "no focus window",
        "focused window is null",
    ),
    "input_dispatching_timeout": (
        "input dispatching timed out",
        "input dispatching timeout",
        "waiting because the touched window has not finished processing",
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
