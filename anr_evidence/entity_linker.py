"""Cross-source entity linkage for ANR evidence.

Extracts PIDs, TIDs, process names, and UIDs from trace content and
cross-references them across all log sources (EventLog, logcat, kernel_log).
This enables sub-agents to find all lines that mention a specific entity
across sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .log_identity import contains_exact_process, parse_line_identity

from .trace_preprocessor import (
    TRACE_PID_RE,
    TRACE_PROCESS_RE,
    TRACE_SYSTID_RE,
    split_trace_sections,
)

@dataclass(frozen=True)
class EntityRef:
    """A reference to an entity found in a source."""

    entity_type: str          # "pid", "tid", "process_name", "uid"
    entity_value: str         # e.g. "1234", "com.example.app"
    source_kind: str          # "trace", "event_log", "logcat", "kernel_log"
    source_line: str          # the line containing it
    line_index: int           # index in the supplied source, not a raw byte offset
    context: str | None = None  # emitter / target / explicit / trace_process / trace_thread / mention


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

    selected_trace_lines: list[tuple[int, str]] | None = None
    # A selected trace is authoritative for scope; do not union other dumps.
    if trace_preprocessed is not None:
        process_name = trace_preprocessed.get("processName")
        pids, tids = set(), set()
        pp_pid = trace_preprocessed.get("pid")
        if pp_pid:
            pids.add(str(pp_pid))
        for thread in trace_preprocessed.get("threads", []) or []:
            tid = thread.get("sysTid")
            if tid:
                tids.add(str(tid))

        # Build identity inventory from the complete selected section, not
        # from the lossy AI display threads. Preserve original line indices.
        nonempty = [(index, line) for index, line in enumerate(trace_content.splitlines()) if line.strip()]
        sections = split_trace_sections([line for _, line in nonempty])
        selected_index = trace_preprocessed.get("selectedSectionIndex")
        selected_trace_lines = []
        offset = 0
        for section_index, section in enumerate(sections):
            name, section_pids, section_tids = extract_trace_entities("\n".join(section))
            if (pids and process_name and section_pids == pids and name == process_name
                    and (selected_index is None or section_index == selected_index)):
                selected_trace_lines.extend(nonempty[offset:offset + len(section)])
                tids.update(section_tids)
            offset += len(section)

    # Extract UIDs from sources
    uids: set[str] = set()
    for source_kind in ("event_log", "logcat", "kernel_log"):
        src = sources.get(source_kind, {})
        content = src.get("content", "")
        for line in content.splitlines():
            uids.update(parse_line_identity(line).uids)

    # Build search terms
    search_terms: set[tuple[str, str]] = set()  # (type, value), never overwrite equal IDs
    if process_name:
        search_terms.add(("process_name", process_name))
    for pid in pids:
        search_terms.add(("pid", pid))
    for tid in tids:
        search_terms.add(("tid", tid))
    for uid in sorted(uids, key=int)[:20]:  # deterministic inventory cap
        search_terms.add(("uid", uid))

    # Cross-reference across all sources
    refs_by_entity: dict[str, list[EntityRef]] = {v: [] for _, v in sorted(search_terms)}
    refs_by_source: dict[str, list[EntityRef]] = {
        "trace": [],
        "event_log": [],
        "logcat": [],
        "kernel_log": [],
    }

    for source_kind in ("trace", "event_log", "logcat", "kernel_log"):
        src = sources.get(source_kind, {})
        content = src.get("content", "")
        if source_kind == "trace" and trace_preprocessed is not None:
            indexed_lines = selected_trace_lines or []
        else:
            indexed_lines = enumerate(content.splitlines())
        for line_index, line in indexed_lines:
            identity = parse_line_identity(line)
            mentions = [("pid", value, "target") for value in identity.target_pids]
            mentions += [("tid", value, "target") for value in identity.target_tids]
            mentions += [("uid", value, "explicit") for value in identity.uids]
            if identity.emitter_pid:
                mentions.append(("pid", identity.emitter_pid, "emitter"))
            if identity.emitter_tid:
                mentions.append(("tid", identity.emitter_tid, "emitter"))
            if source_kind == "trace":
                match = TRACE_PID_RE.search(line)
                if match:
                    mentions.append(("pid", match.group("pid"), "trace_process"))
                match = TRACE_SYSTID_RE.search(line)
                if match:
                    mentions.append(("tid", match.group("sys_tid"), "trace_thread"))
            if process_name and contains_exact_process(line, process_name):
                mentions.append(("process_name", process_name, "mention"))
            # Stable order, preserving distinct types and emitter/target roles.
            for etype, term, context in sorted(set(mentions)):
                if (etype, term) in search_terms:
                    ref = EntityRef(
                        entity_type=etype,
                        entity_value=term,
                        source_kind=source_kind,
                        source_line=line.strip(),
                        line_index=line_index,
                        context=context,
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
    entity_type: str | None = None,
) -> list[EntityRef]:
    """Find all references to a specific entity across specified sources."""
    refs = entity_map.refs_by_entity.get(entity_value, [])
    return [r for r in refs
            if (source_kinds is None or r.source_kind in source_kinds)
            and (entity_type is None or r.entity_type == entity_type)]


def entity_summary_for_ai(entity_map: EntityMap) -> str:
    """Render a compact text summary of the entity map for inclusion in AI prompts."""
    lines = [
        "## Entity Map",
        f"- Process: `{entity_map.process_name or 'unknown'}`",
        f"- PIDs: {sorted(entity_map.pids)}",
        f"- TIDs: {sorted(entity_map.tids)[:10]}{' (+' + str(len(entity_map.tids) - 10) + ' more)' if len(entity_map.tids) > 10 else ''}",
        f"- UIDs: {sorted(entity_map.uids)}",
        "- References are typed mentions, not proof of ownership or causality; unparsed identities are unknown.",
        "",
        "### Cross-Source Reference Counts",
    ]
    for source_kind in ("trace", "event_log", "logcat", "kernel_log"):
        refs = entity_map.refs_by_source.get(source_kind, [])
        lines.append(f"- `{source_kind}`: {len(refs)} entity mentions")
    return "\n".join(lines)
