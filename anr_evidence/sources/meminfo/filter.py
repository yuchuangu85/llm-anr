"""Independent System_log/meminfo.txt filtering entrypoint."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import re
from typing import Any

from ..shared import SourceFilterContext, SourceFilterResult, build_evidence

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$")
_SECTION_RE = re.compile(r"^Total\s+(?P<metric>RSS|PSS)\s+by\s+OOM\s+adjustment:")
_PROCESS_RE = re.compile(
    r"^\s*(?P<kb>[\d,]+)K:\s+(?P<name>.+?)\s+\(pid\s+(?P<pid>\d+)(?P<details>[^)]*)\)\s*$"
)
_GROUP_RE = re.compile(r"^\s*(?P<kb>[\d,]+)K:\s+(?P<name>[^()]+?)\s*$")
_TOTAL_RAM_RE = re.compile(r"^Total RAM:\s+(?P<kb>[\d,]+)K(?:\s+\(status\s+(?P<status>[^)]+)\))?")
_FREE_RAM_RE = re.compile(r"^\s*Free RAM:\s+(?P<kb>[\d,]+)K")
_USED_RAM_RE = re.compile(r"^\s*Used RAM:\s+(?P<kb>[\d,]+)K")
_LOST_RAM_RE = re.compile(r"^\s*Lost RAM:\s+(?P<kb>-?[\d,]+)K")
_ZRAM_RE = re.compile(r"^\s*ZRAM:\s+(?P<value>.+)$")
_TUNING_RE = re.compile(r"^\s*Tuning:\s+(?P<value>.+)$")
_GPU_RE = re.compile(r"^\s*GPU:\s+(?P<value>.+)$")
_DMABUF_RE = re.compile(r"^\s*DMA-BUF:\s+(?P<value>.+)$")
_DMABUF_HEAPS_RE = re.compile(r"^\s*DMA-BUF Heaps:\s+(?P<value>.+)$")
_DMABUF_HEAPS_POOL_RE = re.compile(r"^\s*DMA-BUF Heaps pool:\s+(?P<value>.+)$")
_UPTIME_RE = re.compile(r"^Uptime:\s+(?P<value>.+)$")


@dataclass(frozen=True)
class MeminfoFilterOptions:
    """Options for filtering Android dumpsys meminfo snapshots."""

    package_name: str | None = None
    high_processes: tuple[str, ...] = ()
    high_pids: tuple[int, ...] = ()
    top_n: int = 5
    include_all_snapshots: bool = False
    window_before_seconds: int = 5
    window_after_seconds: int = 5


@dataclass
class MeminfoSnapshot:
    timestamp: str | None
    index: int
    start_line: int
    end_line: int
    processes: list[dict[str, Any]] = field(default_factory=list)
    system: dict[str, Any] = field(default_factory=dict)
    raw_lines: list[str] = field(default_factory=list)


def filter_meminfo_source(
    source: dict[str, Any],
    context: SourceFilterContext | None = None,
    options: MeminfoFilterOptions | None = None,
) -> SourceFilterResult:
    """Filter target-package and high-memory process evidence from meminfo.txt.

    Keeps only snapshots within the configured window (±``window_before_seconds`` /
    ``window_after_seconds`` around the ANR anchor), and renders each snapshot
    compactly: timestamp, top-N processes by PSS/RSS, target package entries,
    and system-level memory summary (Total/Free/Used/Lost/ZRAM/Tuning).

    Default window: ±5s.
    """

    context = context or SourceFilterContext()
    options = options or MeminfoFilterOptions(package_name=context.package_name)
    package_name = options.package_name or context.package_name
    snapshots = parse_meminfo_snapshots(source.get("content", ""))
    warnings: list[dict[str, str]] = []
    if not snapshots:
        return SourceFilterResult(
            source_kind="meminfo",
            warnings=[{"code": "empty-meminfo", "message": "No meminfo snapshots were parsed."}],
        )
    if not package_name:
        warnings.append({"code": "missing-package", "message": "No package name was provided; target package memory cannot be filtered."})

    selected_snapshots = _select_snapshots_in_window(
        snapshots, context.anchor_dt, window_before_seconds=options.window_before_seconds,
        window_after_seconds=options.window_after_seconds,
    )
    if not selected_snapshots and snapshots:
        analysis_snapshot = _select_snapshot_for_anchor(snapshots, context.anchor_dt)
        if analysis_snapshot is not None:
            selected_snapshots = [analysis_snapshot]
    if not selected_snapshots and snapshots:
        selected_snapshots = [snapshots[-1]]

    latest = selected_snapshots[-1]
    high_processes = tuple(item for item in options.high_processes if item)
    high_pids = tuple(int(pid) for pid in options.high_pids)

    # Build target history across ALL snapshots for long-term trend context,
    # but limit to the selected window to avoid noise (unless include_all_snapshots).
    if options.include_all_snapshots:
        target_history = _build_target_history(snapshots, package_name) if package_name else []
    else:
        window_snapshot_indices: set[int] = {s.get("index", -1) for s in selected_snapshots}
        target_history = [
            item for item in (_build_target_history(snapshots, package_name) if package_name else [])
            if item.get("snapshotIndex") in window_snapshot_indices
        ]

    lines = _render_meminfo_lines(
        source_path=source.get("path", "meminfo.txt"),
        package_name=package_name,
        snapshots_total=len(snapshots),
        selected_snapshots=selected_snapshots,
        latest=latest,
        target_history=target_history,
        top_n=options.top_n,
        high_processes=high_processes,
        high_pids=high_pids,
    )
    if package_name and not target_history:
        warnings.append({"code": "target-package-not-found", "message": f"Package `{package_name}` was not found in meminfo snapshots."})

    evidence = [build_evidence(
        evidence_id="meminfo_target_and_high_memory",
        source_kind="meminfo",
        tier="P1",
        extraction_mode="targeted",
        rule_name="meminfo-target-and-high-memory",
        anchor=context.primary_anchor,
        source_path=source.get("path", "meminfo.txt"),
        content="\n".join(lines),
        time_window="meminfo-snapshots",
        label="meminfo-target-and-high-memory",
        warning_flags=[warning["code"] for warning in warnings],
    )]
    return SourceFilterResult(
        source_kind="meminfo",
        evidence=evidence,
        warnings=warnings,
        lines=lines,
        metadata={
            "snapshotCount": len(snapshots),
            "selectedCount": len(selected_snapshots),
            "latestTimestamp": latest.get("timestamp"),
            "selectedTimestamp": latest.get("timestamp"),
            "selectedSnapshotIndex": latest.get("index"),
            "windowBeforeSeconds": options.window_before_seconds,
            "windowAfterSeconds": options.window_after_seconds,
            "anchorTimestamp": context.anchor_dt.isoformat() if context.anchor_dt else None,
            "packageName": package_name,
            "targetHistory": target_history,
        },
    )


def parse_meminfo_snapshots(content: str) -> list[dict[str, Any]]:
    """Parse Android dumpsys meminfo snapshots from System_log/meminfo.txt."""

    lines = content.splitlines()
    timestamp_lines = [(idx, line.strip()) for idx, line in enumerate(lines) if _TIMESTAMP_RE.match(line.strip())]
    snapshots: list[MeminfoSnapshot] = []
    if timestamp_lines:
        for snap_idx, (line_idx, timestamp) in enumerate(timestamp_lines):
            end = timestamp_lines[snap_idx + 1][0] if snap_idx + 1 < len(timestamp_lines) else len(lines)
            start = line_idx + 1
            # Skip the separator immediately after the timestamp if present.
            if start < end and set(lines[start].strip()) == {"="}:
                start += 1
            snapshots.append(_parse_snapshot(lines, start, end, timestamp=timestamp, index=snap_idx))
    elif content.strip():
        snapshots.append(_parse_snapshot(lines, 0, len(lines), timestamp=None, index=0))

    return [_snapshot_to_dict(snapshot) for snapshot in snapshots if snapshot.processes or snapshot.system]


def _select_snapshots_in_window(
    snapshots: list[dict[str, Any]],
    anchor_dt: datetime | None,
    *,
    window_before_seconds: int = 5,
    window_after_seconds: int = 5,
) -> list[dict[str, Any]]:
    """Return snapshots whose timestamp falls within the window around anchor_dt."""
    if anchor_dt is None or not snapshots:
        return []
    start = anchor_dt - timedelta(seconds=window_before_seconds)
    end = anchor_dt + timedelta(seconds=window_after_seconds)
    result: list[dict[str, Any]] = []
    for snapshot in snapshots:
        ts_raw = snapshot.get("timestamp")
        if not ts_raw:
            continue
        try:
            ts = datetime.strptime(str(ts_raw), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if start <= ts <= end:
            result.append(snapshot)
    return result


def _select_snapshot_for_anchor(snapshots: list[dict[str, Any]], anchor_dt: datetime | None) -> dict[str, Any] | None:
    if not snapshots or anchor_dt is None:
        return None

    scored: list[tuple[float, int, dict[str, Any]]] = []
    for snapshot in snapshots:
        ts_raw = snapshot.get("timestamp")
        if not ts_raw:
            continue
        try:
            ts = datetime.strptime(str(ts_raw), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        delta = abs((ts - anchor_dt).total_seconds())
        # Prefer snapshots at/after the ANR when equally close because meminfo
        # collected after the dump better reflects the ANR-time memory state.
        before_penalty = 1 if ts < anchor_dt else 0
        scored.append((delta, before_penalty, snapshot))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1], item[2].get("index", 0)))
    return scored[0][2]


def _parse_snapshot(lines: list[str], start: int, end: int, *, timestamp: str | None, index: int) -> MeminfoSnapshot:
    snapshot = MeminfoSnapshot(
        timestamp=timestamp, index=index, start_line=start + 1, end_line=end,
        raw_lines=list(lines[start:end]),
    )
    current_metric: str | None = None
    current_group: str | None = None
    for offset in range(start, end):
        raw = lines[offset]
        stripped = raw.strip()
        section_match = _SECTION_RE.match(stripped)
        if section_match:
            current_metric = section_match.group("metric").lower()
            current_group = None
            continue
        if stripped.startswith("Total ") and " by OOM adjustment" not in stripped:
            current_metric = None
            current_group = None
        process_match = _PROCESS_RE.match(raw)
        if process_match and current_metric in {"rss", "pss"}:
            snapshot.processes.append({
                "metric": current_metric,
                "kb": _parse_kb(process_match.group("kb")),
                "processName": process_match.group("name").strip(),
                "pid": int(process_match.group("pid")),
                "details": process_match.group("details").strip(),
                "oomAdjustment": current_group,
                "lineNumber": offset + 1,
                "line": raw.rstrip(),
            })
            continue
        group_match = _GROUP_RE.match(raw)
        if group_match and current_metric in {"rss", "pss"}:
            current_group = group_match.group("name").strip()
            continue
        _parse_system_line(snapshot.system, stripped)
    return snapshot


def _parse_system_line(system: dict[str, Any], stripped: str) -> None:
    for key, regex, extract_value in (
        ("totalRamKb", _TOTAL_RAM_RE, lambda m: _parse_kb(m.group("kb"))),
        ("freeRamKb", _FREE_RAM_RE, lambda m: _parse_kb(m.group("kb"))),
        ("usedRamKb", _USED_RAM_RE, lambda m: _parse_kb(m.group("kb"))),
        ("lostRamKb", _LOST_RAM_RE, lambda m: _parse_kb(m.group("kb"))),
        ("zram", _ZRAM_RE, lambda m: m.group("value").strip()),
        ("tuning", _TUNING_RE, lambda m: m.group("value").strip()),
        ("gpu", _GPU_RE, lambda m: m.group("value").strip()),
        ("dmabuf", _DMABUF_RE, lambda m: m.group("value").strip()),
        ("dmabufHeaps", _DMABUF_HEAPS_RE, lambda m: m.group("value").strip()),
        ("dmabufHeapsPool", _DMABUF_HEAPS_POOL_RE, lambda m: m.group("value").strip()),
        ("uptime", _UPTIME_RE, lambda m: m.group("value").strip()),
    ):
        match = regex.match(stripped)
        if match:
            system[key] = extract_value(match)
            if key == "totalRamKb" and match.groupdict().get("status"):
                system["status"] = match.group("status")
            return


def _snapshot_to_dict(snapshot: MeminfoSnapshot) -> dict[str, Any]:
    return {
        "timestamp": snapshot.timestamp,
        "index": snapshot.index,
        "startLine": snapshot.start_line,
        "endLine": snapshot.end_line,
        "processes": snapshot.processes,
        "system": snapshot.system,
        "rawLines": snapshot.raw_lines,
    }


def _build_target_history(snapshots: list[dict[str, Any]], package_name: str | None) -> list[dict[str, Any]]:
    if not package_name:
        return []
    history: list[dict[str, Any]] = []
    for snapshot in snapshots:
        entries = _matching_entries(snapshot, package_name)
        if not entries:
            continue
        pss_kb = sum(entry["kb"] for entry in entries if entry["metric"] == "pss")
        rss_kb = sum(entry["kb"] for entry in entries if entry["metric"] == "rss")
        history.append({
            "timestamp": snapshot.get("timestamp"),
            "snapshotIndex": snapshot.get("index"),
            "pssKb": pss_kb or None,
            "rssKb": rss_kb or None,
            "entries": entries,
            "system": snapshot.get("system", {}),
        })
    return history


def _matching_entries(snapshot: dict[str, Any], process_query: str | None) -> list[dict[str, Any]]:
    if not process_query:
        return []
    return [entry for entry in snapshot.get("processes", []) if _matches_process(entry.get("processName", ""), process_query)]


def _requested_high_entries(
    snapshots: list[dict[str, Any]],
    high_processes: tuple[str, ...],
    high_pids: tuple[int, ...],
) -> list[dict[str, Any]]:
    if not high_processes and not high_pids:
        return []
    matches: list[dict[str, Any]] = []
    for snapshot in snapshots:
        for entry in snapshot.get("processes", []):
            name = entry.get("processName", "")
            pid = entry.get("pid")
            if pid in high_pids or any(_matches_process(name, proc) for proc in high_processes):
                enriched = dict(entry)
                enriched["timestamp"] = snapshot.get("timestamp")
                enriched["snapshotIndex"] = snapshot.get("index")
                matches.append(enriched)
    return sorted(matches, key=lambda item: (item.get("timestamp") or "", item.get("metric") or "", -int(item.get("kb", 0))))


def _top_entries(snapshot: dict[str, Any], *, metric: str, limit: int) -> list[dict[str, Any]]:
    entries = [entry for entry in snapshot.get("processes", []) if entry.get("metric") == metric]
    entries.sort(key=lambda item: -int(item.get("kb", 0)))
    return [dict(entry) for entry in entries[: max(limit, 0)]]


def _render_meminfo_lines(
    *,
    source_path: str,
    package_name: str | None,
    snapshots_total: int,
    selected_snapshots: list[dict[str, Any]],
    latest: dict[str, Any],
    target_history: list[dict[str, Any]],
    top_n: int,
    high_processes: tuple[str, ...],
    high_pids: tuple[int, ...],
) -> list[str]:
    lines = [
        "# Meminfo target/high-memory filter",
        f"- Source: `{source_path}`",
        f"- Snapshot count (total): `{snapshots_total}`",
        f"- Selected snapshots: `{len(selected_snapshots)}`",
        f"- Latest selected snapshot: `{latest.get('timestamp')}`",
        f"- Target package: `{package_name or '<missing>'}`",
        "",
    ]

    if not selected_snapshots:
        lines.append("_No meminfo snapshots in the configured window._")
        return lines

    for snapshot in selected_snapshots:
        _render_snapshot(lines, snapshot, package_name=package_name, top_n=top_n,
                         high_processes=high_processes, high_pids=high_pids)

    # Append long-term target history for trend context.
    lines.extend(["", "## Target package memory history (all snapshots)"])
    if target_history:
        for item in target_history:
            status = item.get("system", {}).get("status")
            lines.append(
                f"- `{item.get('timestamp')}` status=`{status}` "
                f"PSS=`{_fmt_kb(item.get('pssKb'))}` RSS=`{_fmt_kb(item.get('rssKb'))}`"
            )
    else:
        lines.append("_No target package meminfo retained._")
    return lines


def _render_snapshot(
    lines: list[str],
    snapshot: dict[str, Any],
    *,
    package_name: str | None,
    top_n: int,
    high_processes: tuple[str, ...],
    high_pids: tuple[int, ...],
) -> None:
    ts = snapshot.get("timestamp") or "unknown"
    system = snapshot.get("system", {})
    lines.extend([
        f"## Snapshot: `{ts}`",
        "",
        f"- System memory: status=`{system.get('status')}` total=`{_fmt_kb(system.get('totalRamKb'))}` "
        f"free=`{_fmt_kb(system.get('freeRamKb'))}` used=`{_fmt_kb(system.get('usedRamKb'))}` "
        f"lost=`{_fmt_kb(system.get('lostRamKb'))}`",
    ])
    if system.get("zram"):
        lines.append(f"- ZRAM: {system['zram']}")
    if system.get("tuning"):
        lines.append(f"- Tuning: {system['tuning']}")

    # Target package entries
    if package_name:
        target_entries = _matching_entries(snapshot, package_name)
        if target_entries:
            lines.extend(["", "### Target package"])
            for entry in target_entries:
                oom = f" oom=`{entry['oomAdjustment']}`" if entry.get("oomAdjustment") else ""
                lines.append(
                    f"- `{entry.get('metric')}` `{_fmt_kb(entry.get('kb'))}` "
                    f"pid=`{entry.get('pid')}` `{entry.get('processName')}`{oom}"
                )
        else:
            lines.extend(["", "### Target package", "_Not found in this snapshot._"])

    # Top PSS
    top_pss = _top_entries(snapshot, metric="pss", limit=top_n)
    lines.extend(["", "### Top PSS processes"])
    _append_entries(lines, top_pss, include_timestamp=False)

    # Top RSS
    top_rss = _top_entries(snapshot, metric="rss", limit=top_n)
    lines.extend(["", "### Top RSS processes"])
    _append_entries(lines, top_rss, include_timestamp=False)

    # High-load requested processes
    if high_processes or high_pids:
        requested = _requested_high_entries([snapshot], high_processes, high_pids)
        if requested:
            lines.extend(["", "### AnrManager top CPU process memory"])
            _append_entries(lines, requested, include_timestamp=False)

    lines.append("")


def _append_entries(lines: list[str], entries: list[dict[str, Any]], *, include_timestamp: bool) -> None:
    if not entries:
        lines.append("_No entries retained._")
        return
    for entry in entries:
        ts = f" `{entry.get('timestamp')}`" if include_timestamp and entry.get("timestamp") else ""
        oom = f" oom=`{entry.get('oomAdjustment')}`" if entry.get("oomAdjustment") else ""
        details = f" details=`{entry.get('details')}`" if entry.get("details") else ""
        lines.append(
            f"-{ts} `{entry.get('metric')}` `{_fmt_kb(entry.get('kb'))}` "
            f"pid=`{entry.get('pid')}` `{entry.get('processName')}`{oom}{details} line=`{entry.get('lineNumber')}`"
        )


def _matches_process(process_name: str, query: str) -> bool:
    process_name_l = process_name.lower()
    query_l = query.lower()
    return process_name_l == query_l or process_name_l.startswith(query_l + ":") or query_l in process_name_l


def _parse_kb(value: str) -> int:
    return int(value.replace(",", ""))


def _fmt_kb(value: int | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:,}K"
