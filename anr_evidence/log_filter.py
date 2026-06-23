"""Reusable timestamp-window log filtering for ANR evidence sources.

The helpers in this module keep the filtering policy shared across EventLog,
logcat, and kernel logs.  They operate on in-memory content for evidence
packages and also expose a chunked file scanner for large standalone EventLog
files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re
from pathlib import Path
from typing import Callable, Iterable

TimestampParser = Callable[[str], datetime | None]
LinePredicate = Callable[[str], bool]

TIMESTAMP_RE = re.compile(r"(?P<ts>\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})")
ANRMANAGER_LINE_RE = re.compile(
    r"(?:\b[A-Z]/AnrManager\(\s*\d+\):|\b[A-Z]\s+AnrManager:|\bAnrManager:)"
)
_START_ANR_DUMP_RE = re.compile(r"AnrManager(?:\(\s*\d+\))?\s*:\s*startAnrDump\b", re.IGNORECASE)
DEFAULT_TIMESTAMP_YEAR = 2026

EVENT_LOG_TAG_PATTERNS = (
    r"\b(am_[A-Za-z0-9_]+)\b",
    r"\b(wm_[A-Za-z0-9_]+)\b",
    r"\b(input_[A-Za-z0-9_]+)\b",
    r"\b(battery_[A-Za-z0-9_]+)\b",
    r"\b(power_[A-Za-z0-9_]+)\b",
    r"\b(ssm_[A-Za-z0-9_]+)\b",
)

FALLBACK_EVENT_LOG_TAGS = frozenset(
    {
        "am_anr",
        "am_freeze",
        "am_proc_died",
        "am_proc_bound",
        "am_proc_bad",
        "am_proc_good",
        "am_proc_start",
        "am_kill",
        "am_mem_factor",
        "am_pre_boot",
        "am_meminfo",
        "am_pss",
        "am_uid_active",
        "am_uid_idle",
        "am_uid_running",
        "am_uid_stopped",
        "am_unfreeze",
        "wm_task_to_front",
        "wm_task_created",
        "wm_task_moved",
        "wm_create_task",
        "wm_create_activity",
        "wm_remove_task",
        "wm_finish_activity",
        "wm_new_intent",
        "wm_activity_launch_time",
        "wm_add_to_stopping",
        "wm_failed_to_pause",
        "wm_on_paused_called",
        "wm_on_resume_called",
        "wm_on_stop_called",
        "wm_on_top_resumed_gained_called",
        "wm_on_top_resumed_lost_called",
        "wm_pause_activity",
        "wm_restart_activity",
        "wm_resume_activity",
        "wm_set_resumed_activity",
        "wm_focused_root_task",
        "wm_stop_activity",
        "wm_destroy_activity",
        "wm_focus",
        "wm_wallpaper_surface",
        "input_interaction",
        "input_focus",
        "input_cancel",
        "battery_level",
        "battery_status",
        "battery_discharge",
        "power_sleep_continuous",
        "power_screen_broadcast_send",
        "power_screen_state",
        "power_partial_wake_state",
        "ssm_user_starting",
        "ssm_user_switching",
        "ssm_user_unlocking",
    }
)


def event_log_tags_reference_path() -> Path:
    """Return the repository-local EventLog tag reference document."""

    return Path(__file__).resolve().parent.parent / "docs" / "event-log-tags-reference.md"


def event_log_tags_master_path() -> Path:
    """Backward-compatible alias for the renamed tag reference document."""

    return event_log_tags_reference_path()


def load_event_log_tags_from_docs(md_paths: Iterable[str | Path] | None = None) -> frozenset[str]:
    """Load EventLog filter tags from docs, falling back to the built-in set.

    The EventLog filtering contract is documented in ``docs/hermes-gemma-algorithm-design.md`` and
    ``docs/event-log-tags-reference.md``: anchor on ``am_anr`` and retain all
    documented EventLog tags in the 12s pre-ANR window.  This helper keeps the
    executable tag set aligned with the markdown reference while preserving a
    safe fallback for packaged/test environments that do not include docs.
    """

    paths = list(md_paths) if md_paths is not None else [event_log_tags_reference_path()]
    tags = parse_tags_from_markdown(paths)
    if "am_anr" not in tags:
        tags.add("am_anr")
    return frozenset(tags or FALLBACK_EVENT_LOG_TAGS)


LOGCAT_SIGNAL_PATTERNS = frozenset(
    {
        "anr",
        "am_anr",
        "anrmanager",
        "inputdispatcher",
        "input dispatching timed out",
        "focused window",
        "activitymanager",
        "windowmanager",
        "broadcastqueue",
        "contentprovider",
        "system_server",
        "slow operation",
        "slow binder transaction",
        "timeout",
        "not responding",
    }
)

LOGCAT_SYSTEM_CONTEXT_TAGS = frozenset(
    {
        "ActivityManager",
        "AndroidRuntime",
        "AnrManager",
        "BinderProxy",
        "Choreographer",
        "InputDispatcher",
        "SurfaceFlinger",
        "WindowManager",
    }
)
LOGCAT_SYSTEM_CONTEXT_TAG_RE = re.compile(
    r"\s[VDIWEF]\s+(?P<tag>"
    + "|".join(re.escape(tag) for tag in sorted(LOGCAT_SYSTEM_CONTEXT_TAGS))
    + r")(?:\s*:|\s+)"
)

KERNEL_SIGNAL_PATTERNS = frozenset(
    {
        "binder",
        "sched",
        "hung task",
        "blocked for more than",
        "lowmemorykiller",
        "lmkd",
        "oom",
        "psi",
        "pressure",
        "kworker",
        "irq",
        "input",
        "watchdog",
    }
)


@dataclass(frozen=True)
class LogFilterSpec:
    """Filtering policy for one timestamped log source."""

    source_kind: str
    before_seconds: int
    after_seconds: int = 0
    include_patterns: frozenset[str] = field(default_factory=frozenset)
    package_name: str | None = None
    require_pattern: bool = True
    package_filter_scope: str = "all"
    importance_filter: bool = False
    min_importance: str = "contextual"


@dataclass(frozen=True)
class FilterResult:
    lines: list[str]
    warnings: list[dict[str, str]]
    matched_anchor: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AnrManagerBlock:
    """One contiguous AnrManager diagnostic flow in logcat."""

    lines: list[str]
    matched_anchor: str | None
    anchor_dt: datetime | None
    start_dt: datetime | None
    end_dt: datetime | None
    start_line_index: int
    end_line_index: int
    anchor_priority: int

    def metadata(self, anchor_dt: datetime | None = None) -> dict[str, object]:
        data: dict[str, object] = {
            "matchedAnchor": self.matched_anchor,
            "anchorTimestamp": timestamp_to_raw(self.anchor_dt) if self.anchor_dt else None,
            "blockStartTimestamp": timestamp_to_raw(self.start_dt) if self.start_dt else None,
            "blockEndTimestamp": timestamp_to_raw(self.end_dt) if self.end_dt else None,
            "startLineIndex": self.start_line_index,
            "endLineIndex": self.end_line_index,
            "anchorPriority": self.anchor_priority,
        }
        if anchor_dt and self.anchor_dt:
            data["anchorDeltaMs"] = int(abs((self.anchor_dt - anchor_dt).total_seconds()) * 1000)
        return data


def parse_log_timestamp(line: str, *, year: int = DEFAULT_TIMESTAMP_YEAR) -> datetime | None:
    """Parse Android log timestamps that omit the year."""

    match = TIMESTAMP_RE.search(line)
    if not match:
        return None
    # Manual field parsing instead of strptime: TIMESTAMP_RE already pins the
    # exact "MM-DD HH:MM:SS.mmm" shape, and strptime dominated profiles when
    # multi-ANR runs parse hundreds of thousands of log lines.
    ts = match.group("ts")
    try:
        return datetime(
            year,
            int(ts[0:2]),
            int(ts[3:5]),
            int(ts[6:8]),
            int(ts[9:11]),
            int(ts[12:14]),
            int(ts[15:18]) * 1000,
        )
    except ValueError:
        return None


def timestamp_to_raw(timestamp: datetime) -> str:
    return timestamp.strftime("%m-%d %H:%M:%S.%f")[:-3]


def parse_tags_from_markdown(md_paths: Iterable[str | Path]) -> set[str]:
    """Extract known EventLog-style tags from markdown reference files."""

    tags: set[str] = set()
    for md_path in md_paths:
        path = Path(md_path)
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for pattern in EVENT_LOG_TAG_PATTERNS:
            tags.update(match.lower() for match in re.findall(pattern, content))
    return tags


DEFAULT_EVENT_LOG_TAGS = load_event_log_tags_from_docs()


def iter_text_lines(content: str, *, skip_empty: bool = False) -> Iterable[str]:
    """Yield lines from a string without materializing ``content.splitlines()``.

    The ANR pipeline frequently receives tens or hundreds of megabytes of
    logcat/EventLog text already loaded by package loaders.  Calling
    ``splitlines()`` creates a second list-sized copy before filtering starts.
    This generator walks newline offsets instead, preserving ``splitlines()``
    style CR stripping for Android logs.
    """

    start = 0
    length = len(content)
    while start < length:
        end = content.find("\n", start)
        if end == -1:
            end = length
            next_start = length
        else:
            next_start = end + 1
        line = content[start:end].rstrip("\r")
        if not skip_empty or line.strip():
            yield line
        start = next_start


def default_patterns_for_source(source_kind: str) -> frozenset[str]:
    if source_kind == "event_log":
        return DEFAULT_EVENT_LOG_TAGS
    if source_kind == "logcat":
        return LOGCAT_SIGNAL_PATTERNS
    if source_kind == "kernel_log":
        return KERNEL_SIGNAL_PATTERNS
    return frozenset()


def prepare_timestamped_lines(
    content: str,
    *,
    timestamp_parser: TimestampParser = parse_log_timestamp,
) -> list[tuple[datetime | None, str]]:
    """Pre-parse (timestamp, line) pairs once for repeated window filtering.

    Multi-ANR callers filter the same source against several anchors; parsing
    timestamps up front turns O(anchors x lines) parsing into O(lines).
    """

    return [(timestamp_parser(line), line) for line in iter_text_lines(content, skip_empty=True)]


def filter_timestamp_window(
    content: str,
    anchor_dt: datetime | None,
    spec: LogFilterSpec,
    *,
    fallback_label: str,
    timestamp_parser: TimestampParser = parse_log_timestamp,
    max_fallback_lines: int = 25,
) -> FilterResult:
    """Filter lines by anchor-relative time window and source-specific signals.

    For repeated filtering of the same source, use
    :func:`filter_prepared_timestamp_window` with
    :func:`prepare_timestamped_lines` so the prepared source is explicit and
    cannot drift from ``content``.
    """

    prepared_lines = prepare_timestamped_lines(content, timestamp_parser=timestamp_parser)
    return filter_prepared_timestamp_window(
        prepared_lines,
        anchor_dt,
        spec,
        fallback_label=fallback_label,
        max_fallback_lines=max_fallback_lines,
    )


def filter_prepared_timestamp_window(
    prepared_lines: list[tuple[datetime | None, str]],
    anchor_dt: datetime | None,
    spec: LogFilterSpec,
    *,
    fallback_label: str,
    max_fallback_lines: int = 25,
) -> FilterResult:
    """Filter pre-parsed timestamped lines by anchor-relative window."""

    warnings: list[dict[str, str]] = []
    if not prepared_lines:
        return FilterResult([], [{"code": f"empty-{fallback_label}", "message": f"No lines retained for {fallback_label}."}])
    if anchor_dt is None:
        warnings.append({"code": "missing-anchor", "message": "Primary anchor missing; full source fallback retained."})
        return FilterResult([line for _, line in prepared_lines[:max_fallback_lines]], warnings)

    start = anchor_dt - timedelta(seconds=spec.before_seconds)
    end = anchor_dt + timedelta(seconds=spec.after_seconds)
    selected = [
        line
        for ts, line in prepared_lines
        if ts is not None and start <= ts <= end and _line_matches_spec(line, spec)
    ]
    if selected:
        return FilterResult(selected, warnings)

    warnings.append({"code": "empty-anchor-window", "message": f"No timestamped lines matched anchor window for {fallback_label}; no fallback lines retained because they would be outside the ANR window."})
    return FilterResult([], warnings)


def filter_timestamp_windows(
    content: str,
    window_specs: list[tuple[datetime | None, LogFilterSpec, str]],
    *,
    timestamp_parser: TimestampParser = parse_log_timestamp,
    max_fallback_lines: int = 25,
) -> list[FilterResult]:
    """Filter one timestamped source for several anchor windows in one pass.

    This is the memory-oriented counterpart to ``prepare_timestamped_lines`` +
    repeated ``filter_prepared_timestamp_window``.  It trades a small
    ``O(line_count * window_count)`` predicate cost for avoiding a large list of
    ``(timestamp, line)`` tuples over full logcat content.
    """

    if not window_specs:
        return []
    warnings: list[list[dict[str, str]]] = [[] for _ in window_specs]
    selected: list[list[str]] = [[] for _ in window_specs]
    fallback_lines: list[str] = []
    if not content:
        return [
            FilterResult([], [{"code": f"empty-{fallback_label}", "message": f"No lines retained for {fallback_label}."}])
            for _, _, fallback_label in window_specs
        ]

    windows: list[tuple[int, datetime, datetime, LogFilterSpec] | None] = []
    for idx, (anchor_dt, spec, _fallback_label) in enumerate(window_specs):
        if anchor_dt is None:
            warnings[idx].append({"code": "missing-anchor", "message": "Primary anchor missing; full source fallback retained."})
            windows.append(None)
            continue
        windows.append((
            idx,
            anchor_dt - timedelta(seconds=spec.before_seconds),
            anchor_dt + timedelta(seconds=spec.after_seconds),
            spec,
        ))

    for line in iter_text_lines(content, skip_empty=True):
        if len(fallback_lines) < max_fallback_lines:
            fallback_lines.append(line)
        ts = timestamp_parser(line)
        if ts is None:
            continue
        for window in windows:
            if window is None:
                continue
            idx, start, end, spec = window
            if start <= ts <= end and _line_matches_spec(line, spec):
                selected[idx].append(line)

    results: list[FilterResult] = []
    for idx, (anchor_dt, _spec, fallback_label) in enumerate(window_specs):
        if anchor_dt is None:
            results.append(FilterResult(fallback_lines[:max_fallback_lines], warnings[idx]))
            continue
        if selected[idx]:
            results.append(FilterResult(selected[idx], warnings[idx]))
        else:
            results.append(FilterResult(
                [],
                warnings[idx] + [{"code": "empty-anchor-window", "message": f"No timestamped lines matched anchor window for {fallback_label}; no fallback lines retained because they would be outside the ANR window."}],
            ))
    return results


def timestamped_context_before_windows(
    content: str,
    anchor_dts: list[datetime | None],
    before_seconds: int,
    *,
    timestamp_parser: TimestampParser = parse_log_timestamp,
) -> list[list[str]]:
    """Return raw timestamped pre-context windows for several anchors."""

    results: list[list[str]] = [[] for _ in anchor_dts]
    windows: list[tuple[int, datetime, datetime]] = []
    for idx, anchor_dt in enumerate(anchor_dts):
        if anchor_dt is None:
            continue
        windows.append((idx, anchor_dt - timedelta(seconds=before_seconds), anchor_dt))
    if not windows or not content:
        return results
    for line in iter_text_lines(content, skip_empty=True):
        ts = timestamp_parser(line)
        if ts is None:
            continue
        for idx, start_dt, anchor_dt in windows:
            if start_dt <= ts < anchor_dt:
                results[idx].append(line.strip())
    return results


def filter_preceding_anchor_window(
    content: str,
    anchor_pattern: str,
    spec: LogFilterSpec,
    *,
    timestamp_parser: TimestampParser = parse_log_timestamp,
) -> FilterResult:
    """Find the first anchor line and retain matching lines in its preceding window."""

    lines = list(iter_text_lines(content, skip_empty=True))
    for index, line in enumerate(lines):
        if anchor_pattern.lower() not in line.lower():
            continue
        if spec.package_name and spec.package_name not in line:
            continue
        anchor_dt = timestamp_parser(line)
        if anchor_dt is None:
            window_start = max(0, index - 3)
            return FilterResult(
                lines[window_start : index + 1],
                [{"code": "missing-anchor-timestamp", "message": "Anchor line had no parseable timestamp; line-count fallback retained."}],
                matched_anchor=line,
            )
        start = anchor_dt - timedelta(seconds=spec.before_seconds)
        selected = [
            candidate
            for candidate in lines[: index + 1]
            if _line_matches_window(candidate, start, anchor_dt, spec, timestamp_parser)
        ]
        if line not in selected:
            selected.append(line)
        return FilterResult(selected, [], matched_anchor=line)
    return FilterResult([], [{"code": "missing-am-anr", "message": "Event log has no am_anr marker; retaining leading context instead."}])


def filter_file_preceding_anchor_window(
    log_file: str | Path,
    anchor_pattern: str,
    spec: LogFilterSpec,
    *,
    timestamp_parser: TimestampParser = parse_log_timestamp,
    chunk_size: int = 64 * 1024,
    encoding: str = "utf-8",
) -> FilterResult:
    """Chunked two-phase scanner for large files.

    Phase 1 streams forward to find the anchor byte offset. Phase 2 scans
    backward in chunks until the lower time boundary is reached.
    """

    path = Path(log_file)
    if not path.exists():
        return FilterResult([], [{"code": "missing-log-file", "message": f"Log file {path} not found."}])

    anchor_line: str | None = None
    anchor_offset: int | None = None
    anchor_dt: datetime | None = None
    with path.open("rb") as handle:
        while True:
            offset = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            line = raw.decode(encoding, errors="replace").rstrip("\r\n")
            if anchor_pattern.lower() in line.lower() and (not spec.package_name or spec.package_name in line):
                anchor_line = line
                anchor_offset = offset
                anchor_dt = timestamp_parser(line)
                break

        if anchor_line is None or anchor_offset is None:
            return FilterResult([], [{"code": "missing-am-anr", "message": "Event log has no am_anr marker."}])
        if anchor_dt is None:
            return FilterResult([anchor_line], [{"code": "missing-anchor-timestamp", "message": "Anchor line had no parseable timestamp."}], matched_anchor=anchor_line)

        start = anchor_dt - timedelta(seconds=spec.before_seconds)
        selected: list[str] = []
        stop = False
        for line in _iter_lines_backward(handle, anchor_offset, chunk_size=chunk_size, encoding=encoding):
            ts = timestamp_parser(line)
            if ts is not None and ts < start:
                stop = True
                break
            if _line_matches_window(line, start, anchor_dt, spec, timestamp_parser):
                selected.append(line)
        selected.reverse()
        if anchor_line not in selected:
            selected.append(anchor_line)
        return FilterResult(selected, [], matched_anchor=anchor_line)


def find_anrmanager_anchor(
    content: str,
    package_name: str | None = None,
    *,
    timestamp_parser: TimestampParser = parse_log_timestamp,
) -> tuple[str | None, datetime | None, int | None]:
    """Find the best AnrManager anchor line for the target package name.

    Scans logcat content for lines containing ``AnrManager`` and, when
    supplied, the target package.  ``dumpAnrDebugInfo end`` /
    ``addErrorToDropBox`` are better anchors than the opening line because
    they carry the final ANR record and traces path after stack dumping has
    completed.
    """
    best_line: str | None = None
    best_ts: datetime | None = None
    best_idx: int | None = None
    best_priority = 999

    for idx, line in enumerate(iter_text_lines(content)):
        lowered = line.lower()
        if not _is_anrmanager_line(line):
            continue
        if package_name and package_name not in line:
            continue
        ts = timestamp_parser(line)
        priority = _anrmanager_anchor_priority(lowered)
        if priority < best_priority:
            best_line = line.strip()
            best_ts = ts
            best_idx = idx
            best_priority = priority
            if priority == 0:
                break

    return best_line, best_ts, best_idx


def extract_anrmanager_blocks(
    content: str,
    package_name: str | None = None,
    *,
    timestamp_parser: TimestampParser = parse_log_timestamp,
) -> list[AnrManagerBlock]:
    """Extract every package-matching AnrManager diagnostic flow.

    Multi-ANR logcats can contain several AnrManager dumps for the same
    package.  This scanner first identifies package-specific anchor lines,
    then expands each anchor to its surrounding AnrManager block and de-dupes
    identical ranges.  Callers can then select the block nearest to their
    current ANR anchor instead of accidentally reusing the first package match.
    """

    if not content:
        return []

    anr_lines = [
        (idx, line.strip())
        for idx, line in enumerate(iter_text_lines(content))
        if _is_anrmanager_line(line)
    ]
    if not anr_lines:
        return []

    ranges: dict[tuple[int, int], AnrManagerBlock] = {}
    for pos, (line_idx, line) in enumerate(anr_lines):
        if package_name and package_name not in line:
            continue
        start_pos = _find_anrmanager_block_start(anr_lines, pos)
        end_pos = _find_anrmanager_block_end(anr_lines, pos)
        start_idx = anr_lines[start_pos][0]
        end_idx = anr_lines[end_pos][0]
        key = (start_idx, end_idx)
        block_lines = [candidate_line for _, candidate_line in anr_lines[start_pos : end_pos + 1]]
        anchor_line, anchor_dt, anchor_priority = _best_anchor_in_block(
            block_lines,
            package_name,
            timestamp_parser=timestamp_parser,
        )
        if anchor_line is None:
            continue
        candidate = AnrManagerBlock(
            lines=block_lines,
            matched_anchor=anchor_line,
            anchor_dt=anchor_dt,
            start_dt=_first_timestamp(block_lines, timestamp_parser),
            end_dt=_last_timestamp(block_lines, timestamp_parser),
            start_line_index=start_idx,
            end_line_index=end_idx,
            anchor_priority=anchor_priority,
        )
        current = ranges.get(key)
        if current is None or candidate.anchor_priority < current.anchor_priority:
            ranges[key] = candidate

    return sorted(ranges.values(), key=lambda block: (block.anchor_dt or datetime.max, block.start_line_index))


def extract_anrmanager_block(
    content: str,
    package_name: str | None = None,
    *,
    timestamp_parser: TimestampParser = parse_log_timestamp,
    anchor_dt: datetime | None = None,
    max_delta_seconds: int | None = 60,
) -> FilterResult:
    """Extract the full AnrManager diagnostic flow for a given package.

    Real logcat often interleaves unrelated lines while AnrManager is dumping
    traces.  Therefore this function does **not** require physical adjacency.
    It locates the package-specific anchor, then keeps every AnrManager line
    from the nearest ``startAnrDump`` / ``dumpAnrDebugInfo begin`` through the
    terminal ``controller = null`` / ``addErrorToDropBox`` / dump-end marker.

    Returns a ``FilterResult`` whose ``lines`` contain the full block and
    ``matched_anchor`` holds the anchor line text.
    """
    if not content:
        return FilterResult([], [{"code": "empty-logcat", "message": "Logcat content is empty."}])

    blocks = extract_anrmanager_blocks(content, package_name, timestamp_parser=timestamp_parser)
    if not blocks:
        return FilterResult(
            [],
            [{"code": "missing-anrmanager", "message": f"No AnrManager line found matching package '{package_name or '?'}'."}],
        )

    selected = _select_anrmanager_block(blocks, anchor_dt=anchor_dt, max_delta_seconds=max_delta_seconds)
    if selected is None:
        return FilterResult(
            [],
            [{
                "code": "missing-anrmanager-for-anchor",
                "message": (
                    f"No AnrManager block for package '{package_name or '?'}' matched "
                    f"anchor {timestamp_to_raw(anchor_dt) if anchor_dt else '?'} within {max_delta_seconds}s."
                ),
            }],
        )

    return FilterResult(
        selected.lines,
        [],
        matched_anchor=selected.matched_anchor,
        metadata=selected.metadata(anchor_dt),
    )


def _is_anrmanager_line(line: str) -> bool:
    return bool(ANRMANAGER_LINE_RE.search(line))


def _anrmanager_anchor_priority(lowered_line: str) -> int:
    if "dumpanrdebuginfo end" in lowered_line:
        return 0
    if "adderrortodropbox" in lowered_line:
        return 1
    if "anr in " in lowered_line:
        return 2
    if "reason:" in lowered_line:
        return 3
    if "dumpanrdebuginfo begin" in lowered_line:
        return 4
    return 10


def _best_anchor_in_block(
    block_lines: list[str],
    package_name: str | None,
    *,
    timestamp_parser: TimestampParser,
) -> tuple[str | None, datetime | None, int]:
    best_line: str | None = None
    best_ts: datetime | None = None
    best_priority = 999
    for line in block_lines:
        if package_name and package_name not in line:
            continue
        lowered = line.lower()
        priority = _anrmanager_anchor_priority(lowered)
        if priority < best_priority:
            best_line = line
            best_ts = timestamp_parser(line)
            best_priority = priority
    return best_line, best_ts, best_priority


def _first_timestamp(lines: list[str], timestamp_parser: TimestampParser) -> datetime | None:
    for line in lines:
        ts = timestamp_parser(line)
        if ts is not None:
            return ts
    return None


def _last_timestamp(lines: list[str], timestamp_parser: TimestampParser) -> datetime | None:
    for line in reversed(lines):
        ts = timestamp_parser(line)
        if ts is not None:
            return ts
    return None


def _select_anrmanager_block(
    blocks: list[AnrManagerBlock],
    *,
    anchor_dt: datetime | None,
    max_delta_seconds: int | None,
) -> AnrManagerBlock | None:
    if not blocks:
        return None
    if anchor_dt is None:
        return sorted(blocks, key=lambda block: (block.anchor_priority, block.anchor_dt or datetime.max))[0]

    scored: list[tuple[float, int, AnrManagerBlock]] = []
    for block in blocks:
        candidate_dt = block.anchor_dt or block.start_dt or block.end_dt
        if candidate_dt is None:
            continue
        delta = abs((candidate_dt - anchor_dt).total_seconds())
        if max_delta_seconds is not None and delta > max_delta_seconds:
            continue
        scored.append((delta, block.anchor_priority, block))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1], item[2].start_line_index))
    return scored[0][2]


def _nearest_anrmanager_position(anr_indices: list[int], anchor_idx: int) -> int:
    try:
        return anr_indices.index(anchor_idx)
    except ValueError:
        for pos, idx in enumerate(anr_indices):
            if idx >= anchor_idx:
                return pos
        return len(anr_indices) - 1


def _find_anrmanager_block_start(anr_lines: list[tuple[int, str]], anchor_pos: int) -> int:
    begin_pos: int | None = None
    for pos in range(anchor_pos, -1, -1):
        line = anr_lines[pos][1]
        lowered = line.lower()
        if _START_ANR_DUMP_RE.search(line):
            return pos
        if "dumpanrdebuginfo begin" in lowered and begin_pos is None:
            begin_pos = pos
        # Do not drift into a previous ANR dump when multiple ANRs are present.
        if pos < anchor_pos and ("controller = " in lowered or "adderrortodropbox" in lowered):
            return begin_pos if begin_pos is not None else pos + 1
    return begin_pos if begin_pos is not None else 0


def _find_anrmanager_block_end(anr_lines: list[tuple[int, str]], anchor_pos: int) -> int:
    fallback_end_pos = anchor_pos
    for pos in range(anchor_pos, len(anr_lines)):
        line = anr_lines[pos][1]
        lowered = line.lower()
        if pos > anchor_pos and _START_ANR_DUMP_RE.search(line):
            break
        if "dumpanrdebuginfo end" in lowered:
            fallback_end_pos = pos
        if "adderrortodropbox" in lowered:
            fallback_end_pos = pos
        if "controller = " in lowered:
            return pos
    return fallback_end_pos


def _line_matches_spec(line: str, spec: LogFilterSpec) -> bool:
    if spec.package_name and spec.package_filter_scope == "all" and spec.package_name not in line:
        return False
    if (
        spec.package_name
        and spec.package_filter_scope == "system_or_package"
        and spec.package_name not in line
        and not _is_logcat_system_context_line(line)
    ):
        return False
    if not spec.require_pattern:
        return True
    lowered = line.lower()
    return any(pattern.lower() in lowered for pattern in spec.include_patterns)


def _is_logcat_system_context_line(line: str) -> bool:
    return bool(LOGCAT_SYSTEM_CONTEXT_TAG_RE.search(line))


def _line_matches_window(line: str, start: datetime, end: datetime, spec: LogFilterSpec, timestamp_parser: TimestampParser) -> bool:
    ts = timestamp_parser(line)
    if ts is None or not (start <= ts <= end):
        return False
    return _line_matches_spec(line, spec)


def _iter_lines_backward(handle, end_offset: int, *, chunk_size: int, encoding: str) -> Iterable[str]:
    position = end_offset
    remainder = b""
    while position > 0:
        read_size = min(chunk_size, position)
        position -= read_size
        handle.seek(position)
        data = handle.read(read_size) + remainder
        parts = data.split(b"\n")
        if position > 0:
            remainder = parts[0]
            parts = parts[1:]
        else:
            remainder = b""
        for raw_line in reversed(parts):
            if raw_line:
                yield raw_line.decode(encoding, errors="replace").rstrip("\r")
    if remainder:
        yield remainder.decode(encoding, errors="replace").rstrip("\r")
