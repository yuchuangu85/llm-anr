"""Shared source-kind detection, ranking, and de-duplication helpers."""

from __future__ import annotations

from hashlib import sha1
from pathlib import Path
import re
from typing import Any

from ...constants import SOURCE_KINDS
from ...path_utils import normalize_path_text, path_name

TIMESTAMP_RE = re.compile(r"(?P<ts>\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})")

KNOWN_SOURCE_FILENAMES = {
    "trace": ("trace", "traces", "anr_history", "system_app_anr", "system_server_anr"),
    "event_log": ("event", "event_log", "events_log", "logcat_event"),
    "logcat": ("logcat", "logcat_main", "logcat_system", "logcat_radio", "main_log", "system_log", "system_mt_logcat"),
    "kernel_log": ("kernel", "kernel_log", "kmsg", "dmesg", "lastkmsg"),
}

SOURCE_PATH_PATTERNS = {
    "trace": (
        "traces.txt",
        "trace.txt",
        "anr_trace",
        "anr-history",
        "anr_history",
        "system_app_anr",
        "system_server_anr",
        "/dropbox/",
        "/anr/",
        "/traces/",
    ),
    "event_log": (
        "events.txt",
        "event-log",
        "eventlog",
        "events.log",
        "event_log",
        "events_log",
        "events-log",
        "logcat_event",
        "mt_logcat_event",
        "_event_",
    ),
    "logcat": (
        "logcat",
        "main.txt",
        "system.txt",
        "radio.txt",
        "crash.txt",
        "logcat_main",
        "logcat_system",
        "logcat_radio",
        "log-main",
        "log-system",
        "log-radio",
        "system_mt_logcat",
        "mt_logcat_",
    ),
    "kernel_log": (
        "kernel",
        "kernel_log",
        "kernel-log",
        "kmsg",
        "dmesg",
        "last_kmsg",
        "lastkmsg",
        "console-ramoops",
        "console_ramoops",
        "panic_console",
        "ramoops",
    ),
}

CONTENT_SIGNATURES = {
    "trace": (
        "cmd line:",
        "tid=",
        "dalvik threads",
        "\"main\" prio=",
        "\"main\" tid=",
        "native: waiting",
    ),
    "event_log": (
        "am_anr",
        "am_proc_start",
        "input_focus",
        "wm_focus",
        "am_activity_launch_time",
    ),
    "logcat": (
        "inputdispatcher",
        "activitymanager",
        "windowmanager",
        "system_server",
        "broadcastqueue",
        "contentprovider",
    ),
    "kernel_log": (
        "binder:",
        "sched:",
        "irq",
        "kworker",
        "console-ramoops",
        "blocked for more than",
        "hung task",
    ),
}

EVENT_SHARD_HINTS = (
    "logcat_event",
    "_event_",
    "event_",
)

MONKEY_EVENT_LOG_PATTERN = re.compile(
    r".*(?:System_MT_logcat_event|logcat_event|_event_)_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}\.txt$",
)
MONKEY_LOGCAT_PATTERN = re.compile(
    r".*(?:System_MT_logcat|logcat_main|mainlog)_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}\.txt$",
)


def detect_source_kind(relative_path: Path, content: str) -> str | None:
    lower_path = normalize_path_text(relative_path).lower()
    filename = path_name(relative_path).lower()
    lowered_content = content.lower()
    content_head = "\n".join(lowered_content.splitlines()[:20])
    if (
        filename == "meminfo.txt"
        or lower_path.endswith("/system_log/meminfo.txt")
        or ("applications memory usage" in content_head and "total pss by oom adjustment" in lowered_content[:8192])
    ):
        return "meminfo"
    best_kind = None
    best_score = 0
    for source_kind in SOURCE_KINDS:
        score = 0
        for token in KNOWN_SOURCE_FILENAMES[source_kind]:
            if token in filename:
                score += 4
        for token in SOURCE_PATH_PATTERNS[source_kind]:
            if token in lower_path:
                score += 3
        if source_kind == "logcat" and "logcat" in lower_path and not any(hint in lower_path for hint in EVENT_SHARD_HINTS):
            score += 5
        if source_kind == "event_log" and ("event" in filename or "event" in lower_path):
            score += 2
        if source_kind == "event_log" and any(hint in lower_path for hint in EVENT_SHARD_HINTS):
            score += 8
        if source_kind == "trace" and (
            "traces" in filename
            or "/anr/" in lower_path
            or lower_path.startswith("anr/")
            or "/anr_" in lower_path
            or lower_path.startswith("anr_")
        ):
            score += 2
        if source_kind == "trace" and "dropbox" in lower_path:
            score += 8
        if source_kind == "kernel_log" and ("kernel" in filename or "kmsg" in filename or "ramoops" in lower_path):
            score += 2
        for signature in CONTENT_SIGNATURES[source_kind]:
            if signature in content_head:
                score += 4
        if source_kind == "logcat" and TIMESTAMP_RE.search(content_head) and any(level in content_head for level in (" e ", " w ", " i ", " d ")):
            score += 2
        if score > best_score:
            best_score = score
            best_kind = source_kind
    return best_kind if best_score > 0 else None


def source_entry_priority(source_kind: str, path: str, content: str) -> int:
    lower_path = normalize_path_text(path).lower()
    if source_kind == "logcat":
        if any(hint in lower_path for hint in EVENT_SHARD_HINTS):
            return 95  # misclassified event log, push to bottom
        # Monkey test logcat files with timestamps are highly relevant.
        if MONKEY_LOGCAT_PATTERN.search(lower_path):
            return 5
        if "main" in lower_path:
            return 10
        if "system" in lower_path:
            return 20
        if "radio" in lower_path:
            return 30
        if "crash" in lower_path:
            return 40
        return 90
    if source_kind == "kernel_log":
        if "last_kmsg" in lower_path or "lastkmsg" in lower_path:
            return 10
        if "console-ramoops" in lower_path or "console_ramoops" in lower_path:
            return 20
        if "dmesg" in lower_path:
            return 30
        if "kmsg" in lower_path:
            return 40
        return 90
    if source_kind == "trace":
        if "traces.txt" in lower_path:
            return 10
        if "/anr/" in lower_path:
            return 20
        if "dropbox" in lower_path:
            return 30
        return 90
    if source_kind == "event_log":
        if any(hint in lower_path for hint in EVENT_SHARD_HINTS):
            return 5
        # Monkey test event log files get top priority.
        if MONKEY_EVENT_LOG_PATTERN.search(lower_path):
            return 3
        if "events.txt" in lower_path:
            return 10
        if "eventlog" in lower_path or "event-log" in lower_path:
            return 20
        return 90
    return 100


def dedupe_and_rank_entries(source_kind: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = sorted(entries, key=lambda entry: (source_entry_priority(source_kind, entry["path"], entry["content"]), entry["path"]))
    by_content: dict[str, dict[str, Any]] = {}
    for entry in ranked:
        content_key = sha1(entry["content"].strip().encode("utf-8")).hexdigest() if entry["content"].strip() else f"empty::{entry['path']}"
        incumbent = by_content.get(content_key)
        if incumbent is None:
            by_content[content_key] = entry
            continue
        incumbent_priority = source_entry_priority(source_kind, incumbent["path"], incumbent["content"])
        current_priority = source_entry_priority(source_kind, entry["path"], entry["content"])
        if current_priority < incumbent_priority:
            by_content[content_key] = entry
        elif current_priority == incumbent_priority and entry["path"] < incumbent["path"]:
            by_content[content_key] = entry
    return sorted(by_content.values(), key=lambda entry: (source_entry_priority(source_kind, entry["path"], entry["content"]), entry["path"]))
