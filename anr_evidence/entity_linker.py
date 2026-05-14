"""Cross-source entity linkage for ANR evidence.

Extracts PIDs, TIDs, process names, and UIDs from trace content and
cross-references them across all log sources (EventLog, logcat, kernel_log).
This enables sub-agents to find all lines that mention a specific entity
across sources.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .trace_preprocessor import (
    TRACE_PID_RE,
    TRACE_PROCESS_RE,
    TRACE_SYSTID_RE,
)

PID_PATTERN = re.compile(r"(?<!\d)(?P<pid>\d{2,6})(?!\d)")
UID_PATTERN = re.compile(r"uid[=:\s]*(?P<uid>\d+)")


@dataclass(frozen=True)
class EntityRef:
    """A reference to an entity found in a source."""

    entity_type: str          # "pid", "tid", "process_name", "uid"
    entity_value: str         # e.g. "1234", "com.example.app"
    source_kind: str          # "trace", "event_log", "logcat", "kernel_log"
    source_line: str          # the line containing it
    line_index: int
    context: str | None = None  # why it's relevant (e.g. "main_thread", "lock_owner")


@dataclass(frozen=True)
class EntityMap:
    """Cross-source entity linkage for a package."""

    package_id: str
    process_name: str | None
    pids: frozenset[str]
    tids: frozenset[str]
    uids: frozenset[str]
    refs_by_entity: dict[str, list[EntityRef]] = field(default_factory=dict)
    refs_by_source: dict[str, list[EntityRef]] = field(default_factory=dict)


def extract_trace_entities(
    trace_content: str,
) -> tuple[str | None, set[str], set[str]]:
    """Extract process_name, all PIDs, and all TIDs from trace content.

    Returns (process_name, pids, tids).
    """
    process_name: str | None = None
    pids: set[str] = set()
    tids: set[str] = set()

    for line in trace_content.splitlines():
        if process_name is None:
            m = TRACE_PROCESS_RE.search(line)
            if m:
                process_name = m.group("process").strip()

        m = TRACE_PID_RE.search(line)
        if m:
            pids.add(m.group("pid"))

        m = TRACE_SYSTID_RE.search(line)
        if m:
            tids.add(m.group("sys_tid"))

    return process_name, pids, tids


def build_entity_map(
    package: dict[str, Any],
    *,
    trace_preprocessed: dict[str, Any] | None = None,
) -> EntityMap:
    """Build a complete cross-source entity map from a raw or preprocessed package.

    - Extracts entities from trace
    - Searches EventLog, logcat, kernel_log for lines containing those PIDs/TIDs/names
    - Returns an EntityMap with all cross-references
    """
    package_id = str(package.get("package_id", "unknown"))
    sources = package.get("sources", {})
    trace_src = sources.get("trace", {})
    trace_content = trace_src.get("content", "")

    process_name, pids, tids = extract_trace_entities(trace_content)

    # Also extract PIDs from logcat and kernel via pattern matching
    if trace_preprocessed:
        pp_pid = trace_preprocessed.get("pid")
        if pp_pid:
            pids.add(str(pp_pid))
        for thread in trace_preprocessed.get("threads", []) or []:
            tid = thread.get("sysTid")
            if tid:
                tids.add(str(tid))

    # Extract UIDs from sources
    uids: set[str] = set()
    for source_kind in ("event_log", "logcat", "kernel_log"):
        src = sources.get(source_kind, {})
        content = src.get("content", "")
        for m in UID_PATTERN.finditer(content):
            uids.add(m.group("uid"))

    # Build search terms
    search_terms: dict[str, str] = {}  # value -> entity_type
    if process_name:
        search_terms[process_name] = "process_name"
    for pid in pids:
        search_terms[pid] = "pid"
    for tid in tids:
        search_terms[tid] = "tid"
    for uid in list(uids)[:20]:  # cap UIDs
        search_terms[uid] = "uid"

    # Cross-reference across all sources
    refs_by_entity: dict[str, list[EntityRef]] = {v: [] for v in search_terms}
    refs_by_source: dict[str, list[EntityRef]] = {
        "trace": [],
        "event_log": [],
        "logcat": [],
        "kernel_log": [],
    }

    for source_kind in ("trace", "event_log", "logcat", "kernel_log"):
        src = sources.get(source_kind, {})
        content = src.get("content", "")
        for line_index, line in enumerate(content.splitlines()):
            for term, etype in search_terms.items():
                if term in line:
                    ref = EntityRef(
                        entity_type=etype,
                        entity_value=term,
                        source_kind=source_kind,
                        source_line=line.strip(),
                        line_index=line_index,
                    )
                    refs_by_entity.setdefault(term, []).append(ref)
                    refs_by_source.setdefault(source_kind, []).append(ref)

    return EntityMap(
        package_id=package_id,
        process_name=process_name,
        pids=frozenset(pids),
        tids=frozenset(tids),
        uids=frozenset(uids),
        refs_by_entity=refs_by_entity,
        refs_by_source=refs_by_source,
    )


def find_entity_cross_refs(
    entity_map: EntityMap,
    entity_value: str,
    *,
    source_kinds: list[str] | None = None,
) -> list[EntityRef]:
    """Find all references to a specific entity across specified sources."""
    refs = entity_map.refs_by_entity.get(entity_value, [])
    if source_kinds is None:
        return refs
    return [r for r in refs if r.source_kind in source_kinds]


def entity_summary_for_ai(entity_map: EntityMap) -> str:
    """Render a compact text summary of the entity map for inclusion in AI prompts."""
    lines = [
        "## Entity Map",
        f"- Process: `{entity_map.process_name or 'unknown'}`",
        f"- PIDs: {sorted(entity_map.pids)}",
        f"- TIDs: {sorted(entity_map.tids)[:10]}{' (+' + str(len(entity_map.tids) - 10) + ' more)' if len(entity_map.tids) > 10 else ''}",
        f"- UIDs: {sorted(entity_map.uids)}",
        "",
        "### Cross-Source Reference Counts",
    ]
    for source_kind in ("trace", "event_log", "logcat", "kernel_log"):
        refs = entity_map.refs_by_source.get(source_kind, [])
        lines.append(f"- `{source_kind}`: {len(refs)} entity mentions")
    return "\n".join(lines)
