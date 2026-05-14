"""Smart discovery for Monkey/System_log ANR result directories."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import re
from typing import Any

from ..log_filter import parse_log_timestamp
from ..sources.shared.detection import (
    MONKEY_EVENT_LOG_PATTERN,
    MONKEY_LOGCAT_PATTERN,
    detect_source_kind,
)
from ..sources.trace import parse_trace_content_timestamp, parse_trace_filename_timestamp

# Monkey test / System_log directory patterns. When a directory contains a
# System_log/ subdirectory with System_MT_logcat* files, use smart discovery
# to pick the right files instead of loading everything recursively.
_SYSTEM_LOG_CANDIDATE_DIRS = (
    "System_log",
    "system_log",
    "logs",
    "log",
    "Log",
)

_MONKEY_TRACE_PATTERN = re.compile(
    r"anr[/\\]anr_\d{4}-\d{2}-\d{2}.*$",
)

# When loading from a Monkey test directory, we exclude files that match
# these patterns to avoid pulling in huge generic dumps that aren't
# time-aligned with the ANR being analysed.
_EXCLUDE_FROM_MONKEY_LOGS = re.compile(
    r"(?:Logcat_fail|System_MT_logcat\d*\.txt|System_MT_logcat_event\d*\.txt|System_MT_logcat_event_\d+\.txt)$",
)


def try_smart_monkey_discovery(root: Path) -> list[dict[str, Any]] | None:
    """Attempt smart discovery in a Monkey-test result directory.

    Strategy (phased to avoid loading gigabytes of irrelevant data):
    1. Load event_log files (fewer and smaller) first.
    2. Extract ANR timestamps from the event_log content.
    3. Use those timestamps to load ONLY the logcat and trace files that
       are time-proximate to known ANR events.
    4. Load kernel logs unconditionally (small).

    Returns a list of entries keyed by source kind if discovery succeeds,
    or None to signal that the caller should fall back to full traversal.
    """
    log_dir = _find_system_log_dir(root)
    if log_dir is None:
        return None
    anr_dir = log_dir / "anr"
    entries: list[dict[str, Any]] = []
    # Phase 1: Load event_log files (few and small) to discover ANR anchors.
    event_files = _collect_smart_files(log_dir, MONKEY_EVENT_LOG_PATTERN)
    event_content = ""
    if event_files:
        for fp in event_files:
            entries.append(_make_file_entry(fp, root))
        event_content = "\n".join(e["content"] for e in entries if e.get("content"))
    # Phase 2: Parse ANR timestamps from event_log (if available).
    anr_timestamps = _extract_anr_timestamps_from_content(event_content)
    # Phase 3: Load logcat files near ANR timestamps.
    all_logcat_files = _collect_smart_files(log_dir, MONKEY_LOGCAT_PATTERN)
    all_logcat_files = [f for f in all_logcat_files if f not in event_files]
    logcat_files = _filter_files_by_time_proximity(all_logcat_files, anr_timestamps, proximity_minutes=60)
    if logcat_files:
        for fp in logcat_files:
            entries.append(_make_file_entry(fp, root))
    else:
        # No anchors or no matching logcat files — load a conservative
        # window (last few logcat files by timestamp).
        if all_logcat_files:
            for fp in _pick_recent_files(all_logcat_files, max_files=4):
                entries.append(_make_file_entry(fp, root))
    # Phase 4: Load ANR trace files near ANR timestamps.
    trace_files: list[Path] = []
    if anr_dir.is_dir():
        trace_candidates = sorted(
            f for f in anr_dir.iterdir()
            if f.is_file() and _MONKEY_TRACE_PATTERN.search(str(f))
        )
        if not trace_candidates:
            trace_candidates = sorted(
                f for f in anr_dir.iterdir()
                if f.is_file() and detect_source_kind(Path(f.name), _sample_file(f)) == "trace"
            )
        trace_files = _filter_trace_files_by_time_proximity(trace_candidates, anr_timestamps, proximity_minutes=30)
        if not trace_files and trace_candidates:
            trace_files = _pick_recent_trace_files(trace_candidates, max_files=4)
    if trace_files:
        for fp in trace_files:
            entries.append(_make_file_entry(fp, root))
    # Phase 5: Load kernel log files (small, load all).
    kernel_files = _collect_kernel_files(root, log_dir)
    if kernel_files:
        for fp in kernel_files:
            entries.append(_make_file_entry(fp, root))
    meminfo_file = log_dir / "meminfo.txt"
    if meminfo_file.is_file():
        entries.append(_make_file_entry(meminfo_file, root))
    # Only signal success if we found at least trace or logcat.
    found_kinds = {
        "trace" if trace_files else None,
        "logcat" if logcat_files else None,
    }
    if "trace" in found_kinds or "logcat" in found_kinds:
        return entries
    return None


def _make_file_entry(file_path: Path, root: Path) -> dict[str, Any]:
    """Create a single-file entry compatible with package construction."""
    try:
        content = file_path.read_text(encoding="utf-8")
        readable = True
    except UnicodeDecodeError:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        readable = False
    return {
        "path": str(file_path.relative_to(root)),
        "content": content,
        "readable": readable,
    }


def _parse_monkey_filename_timestamp(filename: str) -> datetime | None:
    """Parse a timestamp from a Monkey-test log filename.

    Expected format: ..._MM_DD_HH_MM_SS.txt (month, day, hour, minute, second).
    The year is not present in the filename — we use 2026 as a reasonable
    default for log filtering purposes (exact year doesn't matter for
    time-proximity comparisons within a single test run).
    """
    m = re.search(r"_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})\.txt$", filename)
    if not m:
        return None
    try:
        return datetime(2026, int(m[1]), int(m[2]), int(m[3]), int(m[4]), int(m[5]))
    except ValueError:
        return None


def _extract_anr_timestamps_from_content(event_log_content: str) -> list[datetime]:
    """Extract ANR timestamps from event_log content (am_anr lines)."""
    timestamps: list[datetime] = []
    for line in event_log_content.splitlines():
        if "am_anr" not in line.lower():
            continue
        ts = parse_log_timestamp(line)
        if ts is not None:
            timestamps.append(ts)
    return timestamps


def _filter_files_by_time_proximity(
    files: list[Path],
    anr_timestamps: list[datetime],
    proximity_minutes: int,
) -> list[Path]:
    """Filter files to those whose filename timestamp is near an ANR timestamp."""
    if not anr_timestamps:
        return []
    proximity = timedelta(minutes=proximity_minutes)
    result: list[Path] = []
    for fp in files:
        file_ts = _parse_monkey_filename_timestamp(fp.name)
        if file_ts is None:
            # Can't parse timestamp — include it conservatively.
            result.append(fp)
            continue
        for anr_ts in anr_timestamps:
            if abs(file_ts - anr_ts) <= proximity:
                result.append(fp)
                break
    return result


def _filter_trace_files_by_time_proximity(
    files: list[Path],
    anr_timestamps: list[datetime],
    proximity_minutes: int,
) -> list[Path]:
    """Filter trace files by ANR timestamp using trace-specific timestamps."""
    if not anr_timestamps:
        return []
    proximity = timedelta(minutes=proximity_minutes)
    result: list[Path] = []
    for fp in files:
        file_ts = parse_trace_filename_timestamp(fp.name)
        if file_ts is None:
            file_ts = parse_trace_content_timestamp(_sample_file(fp, max_bytes=8192))
        if file_ts is None:
            # Can't parse timestamp — include it conservatively.
            result.append(fp)
            continue
        for anr_ts in anr_timestamps:
            if abs(file_ts - anr_ts) <= proximity:
                result.append(fp)
                break
    return result


def _pick_recent_files(files: list[Path], max_files: int) -> list[Path]:
    """Pick the most recent files by filename timestamp (up to max_files)."""
    scored: list[tuple[datetime | None, Path]] = []
    for fp in files:
        scored.append((_parse_monkey_filename_timestamp(fp.name), fp))
    # Sort by timestamp descending (None goes last).
    scored.sort(key=lambda item: (item[0] is None, item[0] or datetime.min), reverse=True)
    return [fp for _, fp in scored[:max_files]]


def _pick_recent_trace_files(files: list[Path], max_files: int) -> list[Path]:
    """Pick the most recent trace files by trace filename/content timestamp."""
    scored: list[tuple[datetime | None, Path]] = []
    for fp in files:
        timestamp = parse_trace_filename_timestamp(fp.name)
        if timestamp is None:
            timestamp = parse_trace_content_timestamp(_sample_file(fp, max_bytes=8192))
        scored.append((timestamp, fp))
    scored.sort(key=lambda item: (item[0] is None, item[0] or datetime.min), reverse=True)
    return [fp for _, fp in scored[:max_files]]


def _find_system_log_dir(root: Path) -> Path | None:
    """Find the System_log directory (or a similar log directory)."""
    for candidate_name in _SYSTEM_LOG_CANDIDATE_DIRS:
        candidate = root / candidate_name
        if candidate.is_dir():
            return candidate
    # Also try one level deep in case the data is nested.
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        for candidate_name in _SYSTEM_LOG_CANDIDATE_DIRS:
            candidate = child / candidate_name
            if candidate.is_dir():
                return candidate
    return None


def _collect_smart_files(log_dir: Path, pattern: re.Pattern) -> list[Path]:
    """Collect files in log_dir matching *pattern*, excluding noise files."""
    files: list[Path] = []
    for child in sorted(log_dir.iterdir()):
        if not child.is_file():
            continue
        name = str(child)
        if _EXCLUDE_FROM_MONKEY_LOGS.search(name):
            continue
        if pattern.search(name):
            files.append(child)
    return files


def _sample_file(file_path: Path, max_bytes: int = 4096) -> str:
    """Read the first max_bytes of a file for content-based detection."""
    try:
        with file_path.open("rb") as fh:
            data = fh.read(max_bytes)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def _collect_kernel_files(root: Path, log_dir: Path) -> list[Path]:
    """Collect kernel log files from common locations."""
    kernel_files: list[Path] = []
    kernel_dir_names = ("debuglogger", "kernel", "kernel_log")
    for dir_name in kernel_dir_names:
        candidate = root / dir_name
        if not candidate.is_dir():
            continue
        for child in sorted(candidate.iterdir()):
            if not child.is_file():
                continue
            if detect_source_kind(
                Path(child.name),
                _sample_file(child),
            ) == "kernel_log":
                kernel_files.append(child)
    # Also check log_dir parent for kernel-related files.
    for child in sorted(log_dir.iterdir()):
        if not child.is_file():
            continue
        name = child.name.lower()
        if any(kw in name for kw in ("kernel", "kmsg", "dmesg", "ramoops", "meminfo_kernel")):
            kernel_files.append(child)
    return kernel_files
