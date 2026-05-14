"""Deterministic trace.txt preprocessing utilities."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from .pattern_catalog import evaluate_main_thread_patterns

TIMESTAMP_RE = re.compile(r"(?P<ts>\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})")
TRACE_SECTION_HEADER_RE = re.compile(r"-----\s+pid\s+\d+")
TRACE_PROCESS_RE = re.compile(r"Cmd line:\s*(?P<process>.+)")
TRACE_PID_RE = re.compile(r"-----\s+pid\s+(?P<pid>\d+)")
TRACE_SYSTID_RE = re.compile(r"\|\s+sysTid=(?P<sys_tid>\d+)")
TRACE_GROUP_RE = re.compile(r'\|\s+group="(?P<group>[^"]+)"')
TRACE_SCOUNT_RE = re.compile(r"\bsCount=(?P<s_count>\d+)")
TRACE_DSCOUNT_RE = re.compile(r"\bdsCount=(?P<ds_count>\d+)")
TRACE_UCSCOUNT_RE = re.compile(r"\bucsCount=(?P<ucs_count>\d+)")
TRACE_FLAGS_RE = re.compile(r"\bflags=(?P<flags>\d+)")
TRACE_OBJ_RE = re.compile(r"\bobj=(?P<obj>\S+)")
TRACE_SELF_RE = re.compile(r"\bself=(?P<self>\S+)")
TRACE_NICE_RE = re.compile(r"\bnice=(?P<nice>-?\d+)")
TRACE_CGRP_RE = re.compile(r"\bcgrp=(?P<cgrp>\S+)")
TRACE_SCHED_RE = re.compile(r"\bsched=(?P<sched>\S+)")
TRACE_HANDLE_RE = re.compile(r"\bhandle=(?P<handle>\S+)")
TRACE_LINUX_STATE_RE = re.compile(r"\|\s+state=(?P<state>\S+)")
TRACE_SCHEDSTAT_RE = re.compile(r"\bschedstat=\(\s*(?P<schedstat>[^)]*?)\s*\)")
TRACE_UTM_RE = re.compile(r"\butm=(?P<utm>\d+)")
TRACE_STM_RE = re.compile(r"\bstm=(?P<stm>\d+)")
TRACE_CORE_RE = re.compile(r"\bcore=(?P<core>-?\d+)")
TRACE_HZ_RE = re.compile(r"\bHZ=(?P<hz>\d+)")
TRACE_HELD_MUTEXES_RE = re.compile(r"\|\s+held mutexes=(?P<held>.*)")
TRACE_WAITING_TO_LOCK_RE = re.compile(r"- waiting to lock <(?P<object>[^>]+)>.*held by thread (?P<owner_tid>\d+)")
TRACE_WAITING_ON_RE = re.compile(r"- waiting on <(?P<object>[^>]+)>")
TRACE_SLEEPING_ON_RE = re.compile(r"- sleeping on <(?P<object>[^>]+)>")
TRACE_LOCKED_RE = re.compile(r"- locked <(?P<object>[^>]+)>")
# Coffman-style monitor states from the ART thread header. We treat any of
# these as "actively blocked on a Java monitor", which is required for an
# edge to participate in deadlock cycle detection.
_DEADLOCK_BLOCKED_ART_STATES = {"BLOCKED", "MONITOR"}
TRACE_SIGNAL_PATTERNS = (
    "anr",
    "input dispatching",
    "focused window",
    "no focus window",
    "waiting",
    "blocked",
    "binder",
    "monitor",
    "lock",
)


def preprocess_trace_content(content: str, *, anchor_timestamp: str | None = None, max_lines: int = 40) -> dict[str, Any]:
    lines = [line for line in content.splitlines() if line.strip()]
    anchor_dt = _parse_timestamp(anchor_timestamp) if anchor_timestamp else None
    sections = split_trace_sections(lines)
    if not sections:
        compacted_lines = lines[: min(len(lines), max_lines)]
        compacted_content = "\n".join(compacted_lines)
        threads = extract_trace_threads(compacted_lines)
        primary_thread = _select_primary_thread(threads)
        owner_thread = _resolve_owner_thread(threads, primary_thread)
        binder_summary = _build_binder_summary(threads, primary_thread)
        render_summary = _build_render_summary(threads, primary_thread)
        suspend_summary = _build_suspend_summary(threads)
        cpu_summary = _build_cpu_summary(threads, primary_thread)
        suspicious_threads = _select_suspicious_threads(threads)
        # Deadlock detection: run on the same line set since there is no
        # separate "selected_section" here. Compaction loss is N/A.
        full_threads = threads
        lock_graph = _build_lock_graph(full_threads)
        deadlock_hints = _emit_deadlock_hints(lock_graph, full_threads)
        native_poll_hints = _emit_native_poll_hints(full_threads, cpu_summary)
        main_thread_pattern_hints = _emit_main_thread_pattern_hints(full_threads)
        trace_hints = list(deadlock_hints) + list(native_poll_hints) + list(main_thread_pattern_hints)
        return {
            "sectionCount": 0,
            "selectedSectionIndex": None,
            "compactedLines": compacted_lines,
            "compactedContent": compacted_content,
            "processName": _extract_process_name(compacted_lines),
            "pid": _extract_pid(compacted_lines),
            "threads": threads,
            "primaryThread": primary_thread,
            "ownerThread": owner_thread,
            "binderSummary": binder_summary,
            "renderSummary": render_summary,
            "suspendSummary": suspend_summary,
            "cpuSummary": cpu_summary,
            "suspiciousThreads": suspicious_threads,
            "lockGraph": lock_graph,
            "deadlockHints": deadlock_hints,
            "traceHints": trace_hints,
            "threadSummary": _build_thread_summary(threads, primary_thread, owner_thread, suspicious_threads),
        }

    ranked_sections = sorted(enumerate(sections), key=lambda item: _trace_section_rank(item[1], anchor_dt))
    selected_index, selected_section = ranked_sections[0]
    # Run lock graph + deadlock detection on the FULL selected section before
    # compaction strips threads — otherwise an owner in the cycle may be
    # dropped and we lose the edge.
    full_threads = extract_trace_threads(selected_section)
    full_primary_thread = _select_primary_thread(full_threads)
    full_cpu_summary = _build_cpu_summary(full_threads, full_primary_thread)
    lock_graph = _build_lock_graph(full_threads)
    deadlock_hints = _emit_deadlock_hints(lock_graph, full_threads)
    native_poll_hints = _emit_native_poll_hints(full_threads, full_cpu_summary)
    main_thread_pattern_hints = _emit_main_thread_pattern_hints(full_threads)
    trace_hints = list(deadlock_hints) + list(native_poll_hints) + list(main_thread_pattern_hints)
    # Pin every tid that participates in a cycle / chain so compaction never
    # silently drops a deadlock-graph member from the AI-visible view.
    priority_tids = _collect_priority_tids(lock_graph, deadlock_hints)
    compacted_lines = compact_trace_section(
        selected_section,
        max_lines=max_lines,
        priority_tids=priority_tids,
    )
    compacted_content = "\n".join(compacted_lines)
    threads = extract_trace_threads(compacted_lines)
    primary_thread = _select_primary_thread(threads)
    owner_thread = _resolve_owner_thread(threads, primary_thread)
    binder_summary = _build_binder_summary(threads, primary_thread)
    render_summary = _build_render_summary(threads, primary_thread)
    suspend_summary = _build_suspend_summary(threads)
    cpu_summary = _build_cpu_summary(threads, primary_thread)
    suspicious_threads = _select_suspicious_threads(threads)
    return {
        "sectionCount": len(sections),
        "selectedSectionIndex": selected_index,
        "compactedLines": compacted_lines,
        "compactedContent": compacted_content,
        "processName": _extract_process_name(compacted_lines),
        "pid": _extract_pid(compacted_lines),
        "threads": threads,
        "primaryThread": primary_thread,
        "ownerThread": owner_thread,
        "binderSummary": binder_summary,
        "renderSummary": render_summary,
        "suspendSummary": suspend_summary,
        "cpuSummary": cpu_summary,
        "suspiciousThreads": suspicious_threads,
        "lockGraph": lock_graph,
        "deadlockHints": deadlock_hints,
        "traceHints": trace_hints,
        "threadSummary": _build_thread_summary(threads, primary_thread, owner_thread, suspicious_threads),
    }


def split_trace_sections(lines: list[str]) -> list[list[str]]:
    sections: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        is_header = bool(TRACE_SECTION_HEADER_RE.search(line.lower()))
        if is_header and current:
            sections.append(current)
            current = [line]
            continue
        current.append(line)
    if current:
        sections.append(current)
    return sections


def compact_trace_section(
    lines: list[str],
    *,
    max_lines: int = 40,
    priority_tids: set[str] | None = None,
) -> list[str]:
    """Compact a single trace section down to ``max_lines``.

    ``priority_tids`` lets callers pin specific thread blocks (e.g. tids in a
    deadlock cycle) so they are kept in the compacted view even when the
    default heuristic ranking would have dropped them. Such blocks are
    always sorted right after the ``main`` thread.
    """

    pinned: set[str] = priority_tids or set()
    if len(lines) <= max_lines and not pinned:
        return lines
    thread_start = _first_thread_header_index(lines)
    if thread_start is not None:
        preamble = lines[:thread_start]
        thread_blocks = _extract_thread_blocks(lines[thread_start:])
        if thread_blocks:
            # Keep limit ≥ pinned-count so we never silently drop a
            # deadlock-graph member just because it sits below rank 4.
            limit = max(4, len(pinned) + 1) if pinned else 4
            selected_blocks = _select_thread_blocks(
                thread_blocks,
                limit=limit,
                priority_tids=pinned,
            )
            compacted: list[str] = preamble[: min(len(preamble), 8)]
            remaining = max_lines - len(compacted)
            for block in selected_blocks:
                if remaining <= 0:
                    break
                trimmed = block[:remaining]
                compacted.extend(trimmed)
                remaining = max_lines - len(compacted)
            if compacted:
                return compacted[:max_lines]
    keep_indices = set(range(min(len(lines), 3)))
    for index, line in enumerate(lines):
        lower = line.lower()
        if "cmd line:" in lower or TRACE_SECTION_HEADER_RE.search(lower):
            keep_indices.add(index)
        if _is_main_thread_header(line):
            keep_indices.update(range(max(0, index - 1), min(len(lines), index + 2)))
        if any(pattern in lower for pattern in TRACE_SIGNAL_PATTERNS):
            keep_indices.update(range(max(0, index - 1), min(len(lines), index + 2)))
    ordered = [lines[index] for index in sorted(keep_indices)]
    return ordered[:max_lines] if ordered else lines[:max_lines]


def extract_trace_threads(lines: list[str]) -> list[dict[str, Any]]:
    threads: list[dict[str, Any]] = []
    thread_blocks = _extract_thread_blocks(lines)
    if not thread_blocks:
        thread_blocks = [[line] for line in lines if _parse_thread_header(line) is not None]
    for block in thread_blocks:
        header = block[0]
        parsed = _parse_thread_header(header)
        if not parsed:
            continue
        thread_name = str(parsed.get("name"))
        tid = str(parsed.get("tid"))
        lower = "\n".join(block).lower()
        block_hint = _trace_block_hint(lower)
        thread_state = _trace_thread_state(lower)
        is_main_thread = thread_name == "main"
        suspicion_score, suspicion_reasons = _thread_suspicion(lower, block_hint, thread_state, is_main_thread)
        threads.append(
            {
                "threadName": thread_name,
                "tid": tid,
                "prio": parsed.get("prio"),
                "daemon": parsed.get("daemon"),
                "artThreadState": parsed.get("state"),
                "javaThreadState": _map_java_thread_state(parsed.get("state")),
                "sysTid": _extract_sys_tid(block),
                "group": _extract_group(block),
                "sCount": _extract_match_group(block, TRACE_SCOUNT_RE, "s_count"),
                "dsCount": _extract_match_group(block, TRACE_DSCOUNT_RE, "ds_count"),
                "ucsCount": _extract_match_group(block, TRACE_UCSCOUNT_RE, "ucs_count"),
                "flags": _extract_match_group(block, TRACE_FLAGS_RE, "flags"),
                "obj": _extract_match_group(block, TRACE_OBJ_RE, "obj"),
                "self": _extract_match_group(block, TRACE_SELF_RE, "self"),
                "nice": _extract_match_group(block, TRACE_NICE_RE, "nice"),
                "cgrp": _extract_match_group(block, TRACE_CGRP_RE, "cgrp"),
                "sched": _extract_match_group(block, TRACE_SCHED_RE, "sched"),
                "handle": _extract_match_group(block, TRACE_HANDLE_RE, "handle"),
                "linuxState": _extract_match_group(block, TRACE_LINUX_STATE_RE, "state"),
                "schedstat": _extract_match_group(block, TRACE_SCHEDSTAT_RE, "schedstat"),
                "schedstatParsed": _parse_schedstat(_extract_match_group(block, TRACE_SCHEDSTAT_RE, "schedstat")),
                "utm": _extract_match_group(block, TRACE_UTM_RE, "utm"),
                "stm": _extract_match_group(block, TRACE_STM_RE, "stm"),
                "core": _extract_match_group(block, TRACE_CORE_RE, "core"),
                "hz": _extract_match_group(block, TRACE_HZ_RE, "hz"),
                "heldMutexes": _extract_held_mutexes(block),
                "lockOwnerTid": _extract_lock_owner_tid(block),
                "waitObject": _extract_wait_object(block),
                "heldLocks": _extract_held_locks(block),
                "waitingLocks": _extract_waiting_locks(block),
                "threadRole": _classify_thread_role(thread_name, block_hint, lower),
                "threadState": thread_state,
                "blockHint": block_hint,
                "binderCallKind": _classify_binder_call_kind(lower, block_hint, thread_name),
                "binderDriverFrame": _extract_binder_driver_frame(block),
                "renderCallKind": _classify_render_call_kind(thread_name, block_hint, lower),
                "renderDriverFrame": _extract_render_driver_frame(block),
                "isMainThread": is_main_thread,
                "suspicionScore": suspicion_score,
                "suspicionReasons": suspicion_reasons,
                "nativeTopFrame": _extract_first_matching_line(block, "native: #00"),
                "javaTopFrame": _extract_first_java_frame(block),
                "looperFrame": _extract_looper_frame(block),
                "rawLine": header,
                "rawBlock": "\n".join(block),
            }
        )
    return threads


def _select_primary_thread(threads: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not threads:
        return None
    scored = sorted(threads, key=_trace_thread_priority)
    return scored[0]


def _select_suspicious_threads(threads: list[dict[str, Any]], *, limit: int = 5) -> list[dict[str, Any]]:
    suspicious = [thread for thread in threads if thread.get("suspicionScore", 0) > 0]
    ranked = sorted(
        suspicious,
        key=lambda thread: (-thread.get("suspicionScore", 0), thread.get("threadName", ""), thread.get("tid", "")),
    )
    return ranked[:limit]


def _build_thread_summary(
    threads: list[dict[str, Any]],
    primary_thread: dict[str, Any] | None,
    owner_thread: dict[str, Any] | None,
    suspicious_threads: list[dict[str, Any]],
) -> dict[str, Any]:
    thread_state_counts: dict[str, int] = {}
    block_hint_counts: dict[str, int] = {}
    for thread in threads:
        if thread.get("threadState"):
            state = thread["threadState"]
            thread_state_counts[state] = thread_state_counts.get(state, 0) + 1
        if thread.get("blockHint"):
            block_hint = thread["blockHint"]
            block_hint_counts[block_hint] = block_hint_counts.get(block_hint, 0) + 1
    dominant_block_hint = None
    if block_hint_counts:
        dominant_block_hint = sorted(block_hint_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
    return {
        "threadCount": len(threads),
        "suspiciousThreadCount": len(suspicious_threads),
        "mainThreadBlocked": bool(primary_thread and primary_thread.get("isMainThread") and primary_thread.get("blockHint")),
        "lockContentionDetected": bool(primary_thread and primary_thread.get("lockOwnerTid") and owner_thread),
        "ownerThreadTid": owner_thread.get("tid") if owner_thread else None,
        "ownerThreadName": owner_thread.get("threadName") if owner_thread else None,
        "dominantBlockHint": dominant_block_hint,
        "threadStateCounts": thread_state_counts,
        "blockHintCounts": block_hint_counts,
    }


def _trace_thread_priority(thread: dict[str, Any]) -> tuple[int, int, int, str]:
    lower = thread.get("rawBlock", thread["rawLine"]).lower()
    return (
        0 if thread.get("isMainThread") else 1,
        0 if any(pattern in lower for pattern in ("focused window", "input dispatching", "binder", "epoll", "pollonce", "waiting", "blocked")) else 1,
        0 if "native" in lower else 1,
        thread.get("threadName", ""),
    )


def _first_thread_header_index(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if _parse_thread_header(line) is not None:
            return index
    return None


def _extract_thread_blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        is_header = _parse_thread_header(line) is not None
        if is_header:
            if current:
                blocks.append(current)
            current = [line]
            continue
        if current:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _select_thread_blocks(
    blocks: list[list[str]],
    *,
    limit: int = 4,
    priority_tids: set[str] | None = None,
) -> list[list[str]]:
    pri = priority_tids or set()
    ranked = sorted(blocks, key=lambda b: _thread_block_priority(b, pri))
    return ranked[:limit]


def _thread_block_priority(
    block: list[str],
    priority_tids: set[str] | None = None,
) -> tuple[int, int, int, int, int, str]:
    parsed = _parse_thread_header(block[0])
    thread_name = parsed.get("name") if parsed else ""
    tid = parsed.get("tid") if parsed else ""
    joined = "\n".join(block).lower()
    is_main = thread_name == "main"
    is_priority = bool(priority_tids and tid in priority_tids)
    signal_hits = sum(1 for pattern in TRACE_SIGNAL_PATTERNS if pattern in joined)
    native_poll = 0 if any(pattern in joined for pattern in ("epoll", "pollonce", "poll once")) else 1
    binder = 0 if "binder" in joined else 1
    return (
        0 if is_main else 1,
        0 if is_priority else 1,
        native_poll,
        binder,
        -signal_hits,
        thread_name,
    )


def _parse_thread_header(line: str) -> dict[str, str | bool] | None:
    if " tid=" not in line:
        return None
    prefix, _, suffix = line.partition(" tid=")
    suffix_parts = suffix.split()
    if not suffix_parts:
        return None
    tid = suffix_parts[0]
    state = suffix_parts[1] if len(suffix_parts) > 1 else None
    prefix = prefix.strip()
    if prefix.startswith('"'):
        end_quote = prefix.find('"', 1)
        if end_quote != -1:
            rest = prefix[end_quote + 1 :]
            prio = None
            if "prio=" in rest:
                prio = rest.split("prio=", 1)[1].split()[0]
            return {"name": prefix[1:end_quote], "tid": tid, "state": state, "prio": prio, "daemon": "daemon" in rest}
    if " prio=" in prefix:
        name, rest = prefix.split(" prio=", 1)
        return {"name": name.strip(), "tid": tid, "state": state, "prio": rest.split()[0], "daemon": False}
    return {"name": prefix, "tid": tid, "state": state, "prio": None, "daemon": False} if prefix else None


def _is_main_thread_header(line: str) -> bool:
    parsed = _parse_thread_header(line)
    return bool(parsed and parsed.get("name") == "main")


def _extract_sys_tid(block: list[str]) -> str | None:
    for line in block:
        match = TRACE_SYSTID_RE.search(line)
        if match:
            return match.group("sys_tid")
    return None


def _extract_group(block: list[str]) -> str | None:
    for line in block:
        match = TRACE_GROUP_RE.search(line)
        if match:
            return match.group("group")
    return None


def _extract_match_group(block: list[str], regex: re.Pattern[str], name: str) -> str | None:
    for line in block:
        match = regex.search(line)
        if match:
            return match.group(name)
    return None


def _extract_held_mutexes(block: list[str]) -> str | None:
    for line in block:
        match = TRACE_HELD_MUTEXES_RE.search(line)
        if match:
            value = match.group("held").strip()
            return value or None
    return None


def _extract_lock_owner_tid(block: list[str]) -> str | None:
    for line in block:
        match = TRACE_WAITING_TO_LOCK_RE.search(line)
        if match:
            return match.group("owner_tid")
    return None


def _extract_wait_object(block: list[str]) -> str | None:
    for line in block:
        for regex in (TRACE_WAITING_TO_LOCK_RE, TRACE_WAITING_ON_RE, TRACE_SLEEPING_ON_RE):
            match = regex.search(line)
            if match:
                return match.group("object")
    return None


def _extract_held_locks(block: list[str]) -> list[str]:
    """Extract every Java monitor that this thread currently holds.

    Source lines look like ``- locked <0x0ca44263> (a com.foo.Bar)``.
    Used as the `lock_obj -> owning_tid` index when building the lock graph.
    """

    locks: list[str] = []
    for line in block:
        match = TRACE_LOCKED_RE.search(line)
        if not match:
            continue
        obj = match.group("object")
        if obj not in locks:
            locks.append(obj)
    return locks


def _extract_waiting_locks(block: list[str]) -> list[dict[str, str]]:
    """Extract every monitor this thread is currently waiting to enter.

    A single thread can have multiple ``- waiting to lock`` lines (rare but
    possible in nested synchronized blocks during stack unwind), so we return
    a list rather than a single value. Each entry carries both the lock
    object id and the owner tid that ART resolved at dump time.
    """

    waits: list[dict[str, str]] = []
    for line in block:
        match = TRACE_WAITING_TO_LOCK_RE.search(line)
        if not match:
            continue
        waits.append({"object": match.group("object"), "ownerTid": match.group("owner_tid")})
    return waits


def _is_thread_suspended(thread: dict[str, Any]) -> bool:
    art = (thread.get("artThreadState") or "").upper().rstrip(":")
    return art == "SUSPENDED"


def _is_thread_monitor_blocked(thread: dict[str, Any]) -> bool:
    """Conservative check: thread is in a Coffman-blocked monitor state.

    Only such threads are allowed to participate in deadlock cycle detection,
    so that the Signal-Catcher-induced ``SUSPENDED`` cluster (a Java dump
    artifact, not an ANR root cause) is excluded by construction.
    """

    art = (thread.get("artThreadState") or "").upper().rstrip(":")
    if art in _DEADLOCK_BLOCKED_ART_STATES:
        return True
    return thread.get("threadState") == "blocked"


def _build_lock_graph(threads: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a directed lock-wait graph + run Tarjan SCC for cycle detection.

    Edge convention: ``waiter_tid -> owner_tid`` annotated with the lock
    object. Cycles in this graph correspond directly to Coffman's
    "circular wait" condition for deadlock.
    """

    owner_of_lock: dict[str, str] = {}
    for thread in threads:
        tid = thread.get("tid")
        if not tid:
            continue
        for lock_obj in thread.get("heldLocks", []) or []:
            owner_of_lock.setdefault(lock_obj, tid)

    edges: list[dict[str, str]] = []
    nodes: set[str] = set()
    for thread in threads:
        if _is_thread_suspended(thread):
            continue
        waiter_tid = thread.get("tid")
        if not waiter_tid:
            continue
        for wait in thread.get("waitingLocks", []) or []:
            owner_tid = wait.get("ownerTid") or owner_of_lock.get(wait.get("object", ""))
            if not owner_tid:
                continue
            edges.append({
                "waiterTid": waiter_tid,
                "ownerTid": owner_tid,
                "lockObject": wait.get("object"),
            })
            nodes.add(waiter_tid)
            nodes.add(owner_tid)

    cycles = _detect_cycles(nodes, edges)
    return {
        "nodes": sorted(nodes, key=_tid_sort_key),
        "edges": edges,
        "ownerOfLock": owner_of_lock,
        "cycles": cycles,
    }


def _tid_sort_key(tid: str) -> tuple[int, str]:
    try:
        return (0, f"{int(tid):010d}")
    except (TypeError, ValueError):
        return (1, str(tid))


def _detect_cycles(nodes: set[str], edges: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Find strongly connected components with size > 1 and self-loops.

    Implementation is iterative Tarjan to avoid Python's recursion limit on
    pathological inputs. ANR traces have <100 threads in practice, so the
    iterative form is mostly defensive.
    """

    if not nodes:
        return []

    adj: dict[str, list[str]] = {node: [] for node in nodes}
    self_loops: set[str] = set()
    for edge in edges:
        waiter, owner = edge["waiterTid"], edge["ownerTid"]
        if waiter not in adj or owner not in adj:
            continue
        if waiter == owner:
            self_loops.add(waiter)
            continue
        adj[waiter].append(owner)

    index_counter = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    sccs: list[list[str]] = []

    for start in nodes:
        if start in indices:
            continue
        work: list[tuple[str, int]] = [(start, 0)]
        call_stack: list[str] = []
        while work:
            v, edge_idx = work[-1]
            if v not in indices:
                indices[v] = index_counter
                lowlinks[v] = index_counter
                index_counter += 1
                stack.append(v)
                on_stack.add(v)
                call_stack.append(v)
            successors = adj[v]
            if edge_idx < len(successors):
                work[-1] = (v, edge_idx + 1)
                w = successors[edge_idx]
                if w not in indices:
                    work.append((w, 0))
                elif w in on_stack:
                    lowlinks[v] = min(lowlinks[v], indices[w])
                continue
            # All successors visited: maybe close a SCC rooted at v
            if lowlinks[v] == indices[v]:
                component: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    component.append(w)
                    if w == v:
                        break
                sccs.append(component)
            work.pop()
            if call_stack:
                call_stack.pop()
            if work:
                parent_v, _ = work[-1]
                lowlinks[parent_v] = min(lowlinks[parent_v], lowlinks[v])

    cycles: list[dict[str, Any]] = []
    cycle_member_tids: set[str] = set()
    for scc in sccs:
        if len(scc) > 1:
            tids = sorted(scc, key=_tid_sort_key)
            cycles.append({"tids": tids, "size": len(tids), "selfLoop": False})
            cycle_member_tids.update(tids)
    for tid in self_loops:
        if tid in cycle_member_tids:
            continue
        cycles.append({"tids": [tid], "size": 1, "selfLoop": True})
    return cycles


def _follow_owner_chain(
    start_tid: str,
    edges: list[dict[str, str]],
    threads_by_tid: dict[str, dict[str, Any]],
    *,
    max_depth: int = 8,
) -> list[str]:
    """Walk waiter -> owner edges greedily until the chain ends or revisits.

    A revisit terminates the walk (caller treats that as cycle territory),
    and a non-blocked owner terminates the walk one step after the last
    blocked node. Used by `LOCK_OWNER_BLOCKED` chain detection.
    """

    waiter_to_owner: dict[str, str] = {}
    for edge in edges:
        waiter_to_owner.setdefault(edge["waiterTid"], edge["ownerTid"])

    chain: list[str] = [start_tid]
    visited = {start_tid}
    current = start_tid
    while len(chain) <= max_depth:
        next_owner = waiter_to_owner.get(current)
        if not next_owner:
            break
        chain.append(next_owner)
        if next_owner in visited:
            break
        visited.add(next_owner)
        owner_thread = threads_by_tid.get(next_owner)
        if not owner_thread or not _is_thread_monitor_blocked(owner_thread):
            break
        current = next_owner
    return chain


def _short_thread_label(thread: dict[str, Any] | None, tid: str) -> str:
    if thread and thread.get("threadName"):
        return f"{thread['threadName']}(tid={tid})"
    return f"tid={tid}"


def _collect_priority_tids(
    lock_graph: dict[str, Any],
    deadlock_hints: list[dict[str, Any]],
) -> set[str]:
    """Aggregate every tid that participates in any cycle / chain hint.

    Used to pin those threads through ``compact_trace_section`` so the
    AI-visible compacted view always retains the full deadlock graph.
    """

    pinned: set[str] = set()
    for cycle in (lock_graph or {}).get("cycles", []) or []:
        for tid in cycle.get("tids", []) or []:
            pinned.add(tid)
    for hint in deadlock_hints or []:
        for tid in hint.get("tids", []) or []:
            pinned.add(tid)
        for tid in hint.get("chain", []) or []:
            pinned.add(tid)
        anchor = hint.get("anchorTid")
        if anchor:
            pinned.add(anchor)
        for edge in hint.get("edges", []) or []:
            pinned.add(edge.get("waiterTid"))
            pinned.add(edge.get("ownerTid"))
    pinned.discard(None)
    pinned.discard("")
    return pinned


def _emit_main_thread_pattern_hints(threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run every catalog rule against the main thread stack.

    The actual rule definitions live in ``pattern_catalog.py`` so they
    can be appended to without touching engine code. A single trace can
    legitimately match multiple patterns (e.g. SP_APPLY_WAIT *and*
    IO_BLOCKED via fsync) — patterns are not mutually exclusive.
    """

    main_thread = next((t for t in threads if t.get("isMainThread")), None)
    if not main_thread:
        return []
    main_label = _short_thread_label(main_thread, main_thread.get("tid", "?"))
    return evaluate_main_thread_patterns(main_thread, main_label)


def _emit_native_poll_hints(
    threads: list[dict[str, Any]],
    cpu_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    """Disambiguate the NativePollOnce trap.

    A trace dump captured exactly while the main thread is parked in
    ``MessageQueue.nativePollOnce`` / ``epoll_pwait`` is by far the most
    common single ANR fingerprint, but it has two completely different
    root-cause classes:

    * **Truly idle** (NATIVE_POLL_IDLE_LIKELY, weak):
        the queue is empty, the dump caught the looper between messages.
        The trace is a "scapegoat" — the actual root cause is elsewhere
        (CPU starvation, system pressure) or the app simply wasn't ANRing.

    * **Busy but currently parked** (NATIVE_POLL_BUT_BUSY, strong):
        the looper has been processing slow messages for the entire ANR
        window; this snapshot caught it between two slow messages. AI
        must NOT report "main thread idle" — the real cause is a
        heavy-weight Looper handler / a stuck Choreographer barrier.

    Heuristic uses the schedstat already computed in ``cpu_summary``:
        runNs >> waitNs  → busy
        runNs ≈ 0 and waitNs ≈ 0 → genuinely idle
        Otherwise → idle-likely (weak)
    """

    main_thread = next((t for t in threads if t.get("isMainThread")), None)
    if not main_thread:
        return []

    block_lower = main_thread.get("rawBlock", "").lower()
    in_native_poll = (
        "nativepollonce" in block_lower
        or "epoll_pwait" in block_lower
        or "epoll_wait" in block_lower
    )
    if not in_native_poll:
        return []

    run_ns = int(cpu_summary.get("mainThreadRunNs", 0) or 0)
    wait_ns = int(cpu_summary.get("mainThreadWaitNs", 0) or 0)

    BUSY_RUN_NS = 200_000_000      # ≥200ms of CPU time captured ⇒ definitely working
    BUSY_WAIT_NS = 1_000_000_000   # ≥1s wait_ns with significant run_ns ⇒ scheduler-starved while busy
    IDLE_CEILING_NS = 50_000_000   # ≤50ms run_ns AND ≤50ms wait_ns ⇒ likely idle

    is_busy = run_ns >= BUSY_RUN_NS or (wait_ns >= BUSY_WAIT_NS and run_ns > IDLE_CEILING_NS)
    is_clearly_idle = run_ns <= IDLE_CEILING_NS and wait_ns <= IDLE_CEILING_NS

    main_label = _short_thread_label(main_thread, main_thread.get("tid", "?"))
    looper_frame = main_thread.get("looperFrame")

    if is_busy:
        return [{
            "id": "NATIVE_POLL_BUT_BUSY",
            "category": "main_block",
            "severity": "warning",
            "confidence": "strong",
            "scope": "thread",
            "anchorTid": main_thread.get("tid"),
            "schedstat": {"runNs": run_ns, "waitNs": wait_ns},
            "looperFrame": looper_frame,
            "message": (
                f"{main_label} 表面停在 nativePollOnce，但 schedstat 显示主线程"
                f" runNs={run_ns} / waitNs={wait_ns}，**有显著 CPU 消耗**："
                "看似空闲实则在执行历史消息（典型：Looper handler 慢、barrier 假死、消息洪水）。"
                "**不要**把主线程判为空闲。"
            ),
            "wikiRefs": [
                "wiki/ANR-trace文件分析.md",
                "wiki/MTK/swt/19.NativePollOnce.md",
            ],
            "nextActions": [
                "查 logcat 是否有 Looper 慢消息日志（Choreographer / Slow operation / Slow Looper）",
                "结合 EventLog 的 am_anr 时间戳计算 ANR 窗口实际消息处理时长",
            ],
        }]

    if is_clearly_idle:
        return [{
            "id": "NATIVE_POLL_IDLE_LIKELY",
            "category": "main_block",
            "severity": "info",
            "confidence": "weak",
            "scope": "thread",
            "anchorTid": main_thread.get("tid"),
            "schedstat": {"runNs": run_ns, "waitNs": wait_ns},
            "looperFrame": looper_frame,
            "message": (
                f"{main_label} 停在 nativePollOnce，且 schedstat (runNs={run_ns}, waitNs={wait_ns}) "
                "极小：**很可能消息队列空闲，trace 是替罪羊**。"
                "真实根因通常在系统压力 / 跨进程等待 / 渲染 GPU / 焦点窗口未就绪等其它源。"
            ),
            "wikiRefs": ["wiki/ANR-trace文件分析.md"],
            "nextActions": [
                "查 EventLog 的 am_anr reason 字段确认 ANR 类型",
                "查 AnrManager 块的 CPU usage 是否有其它进程吃 CPU",
                "查 logcat 是否有 InputDispatcher / WindowManager / SF 相关报错",
            ],
        }]

    return [{
        "id": "NATIVE_POLL_AMBIGUOUS",
        "category": "main_block",
        "severity": "info",
        "confidence": "weak",
        "scope": "thread",
        "anchorTid": main_thread.get("tid"),
        "schedstat": {"runNs": run_ns, "waitNs": wait_ns},
        "looperFrame": looper_frame,
        "message": (
            f"{main_label} 停在 nativePollOnce，schedstat (runNs={run_ns}, waitNs={wait_ns}) "
            "处于不明区间：既非典型空闲也非显著繁忙；建议结合旁证综合判断。"
        ),
        "wikiRefs": ["wiki/ANR-trace文件分析.md"],
        "nextActions": [
            "查 logcat / EventLog 找出 ANR 时刻的真实触发源",
        ],
    }]


def _emit_deadlock_hints(
    lock_graph: dict[str, Any],
    threads: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate structured deadlock hints from the lock graph.

    Hint vocabulary (see docs/trace_hint_annotation_design.md §4.3.1):
      * `DEADLOCK_CYCLE`           — SCC ≥2 nodes, every member monitor-blocked.
      * `DEADLOCK_LIKELY`          — SCC ≥2 nodes, some member not blocked.
      * `DEADLOCK_SELF`            — owner_tid == waiter_tid (self-loop).
      * `LOCK_OWNER_BLOCKED`       — chain ≥3 tids, no cycle (cascading block).
      * `LOCK_OWNER_SLEEPING`      — single edge, owner in Sleeping/TimedWaiting.
      * `LOCK_CONTENTION_BLOCKED`  — single edge, owner Runnable/Native.
    """

    hints: list[dict[str, Any]] = []
    threads_by_tid = {t.get("tid"): t for t in threads if t.get("tid")}
    edges = lock_graph.get("edges", [])
    cycles = lock_graph.get("cycles", [])

    cycle_tids: set[str] = set()
    for cycle in cycles:
        scc_tids = list(cycle.get("tids", []))
        scc_set = set(scc_tids)
        cycle_tids.update(scc_set)
        scc_edges = [
            edge for edge in edges
            if edge["waiterTid"] in scc_set and edge["ownerTid"] in scc_set
        ]
        states = {threads_by_tid.get(tid, {}).get("artThreadState") for tid in scc_tids}
        labels = [_short_thread_label(threads_by_tid.get(tid), tid) for tid in scc_tids]

        if cycle.get("selfLoop"):
            tid = scc_tids[0]
            hints.append({
                "id": "DEADLOCK_SELF",
                "category": "main_block",
                "severity": "warning",
                "confidence": "medium",
                "scope": "thread",
                "tids": [tid],
                "edges": scc_edges,
                "message": (
                    f"线程 {labels[0]} 等待自己已持有的 monitor，"
                    "多见于错误的 reentrant 释放或 unmatched unlock。"
                ),
                "wikiRefs": ["wiki/实例/ANR-死锁.md"],
                "nextActions": [
                    f"复核 tid={tid} 的加锁/解锁配对",
                    "排查异常路径下是否有 lock() 后没有 unlock() 的分支",
                ],
            })
            continue

        all_blocked = all(_is_thread_monitor_blocked(threads_by_tid.get(tid, {})) for tid in scc_tids)
        chain_repr = " → ".join(labels + [labels[0]])
        if all_blocked:
            hints.append({
                "id": "DEADLOCK_CYCLE",
                "category": "main_block",
                "severity": "critical",
                "confidence": "strong",
                "scope": "global",
                "tids": scc_tids,
                "edges": scc_edges,
                "message": (
                    f"检测到 {len(scc_tids)} 线程死锁环 ({chain_repr})；"
                    "环上所有线程处于 Blocked/Monitor 状态，符合 Coffman 四要件。"
                ),
                "wikiRefs": [
                    "wiki/实例/ANR-死锁.md",
                    "wiki/MTK/swt/17.Deadlock.md",
                ],
                "nextActions": [
                    "按环顺序追溯各 tid 的 Java 栈，定位加锁顺序冲突",
                    "在另一份 trace dump 上复核以升级为 CONFIRMED_DEADLOCK",
                ],
            })
        else:
            non_blocked = [
                tid for tid in scc_tids
                if not _is_thread_monitor_blocked(threads_by_tid.get(tid, {}))
            ]
            hints.append({
                "id": "DEADLOCK_LIKELY",
                "category": "main_block",
                "severity": "warning",
                "confidence": "medium",
                "scope": "global",
                "tids": scc_tids,
                "edges": scc_edges,
                "states": {tid: threads_by_tid.get(tid, {}).get("artThreadState") for tid in scc_tids},
                "message": (
                    f"等锁环 ({chain_repr}) 存在，但环上 tid={non_blocked} 当前不在 Blocked，"
                    "可能采样未对齐；建议跨 trace 复核。"
                ),
                "wikiRefs": ["wiki/实例/ANR-死锁.md"],
                "nextActions": [
                    f"复核 tid={non_blocked} 的当前状态",
                    "在另一份 trace dump 上验证锁图节点是否一致",
                ],
                "ignored_states": sorted({s for s in states if s}),
            })

    # CROSS_PROCESS_DEADLOCK_SUSPECTED — main thread is parked in
    # binder_wait_reply while a deadlock graph exists in THIS process. We
    # cannot prove cross-process deadlock from a single trace, so this is
    # always emitted with weak confidence and asks the AI / agent to re-probe
    # the remote process.
    main_thread = next((t for t in threads if t.get("isMainThread")), None)
    if main_thread and main_thread.get("binderCallKind") == "binder_wait_reply" and cycles:
        cycle_tid_lists = [list(c.get("tids", [])) for c in cycles]
        hints.append({
            "id": "CROSS_PROCESS_DEADLOCK_SUSPECTED",
            "category": "binder",
            "severity": "warning",
            "confidence": "weak",
            "scope": "global",
            "tids": [main_thread.get("tid")],
            "cycleTids": cycle_tid_lists,
            "binderDriverFrame": main_thread.get("binderDriverFrame"),
            "message": (
                "主线程阻塞在 binder_wait_reply，且本进程同时存在死锁环 "
                f"{cycle_tid_lists}：疑似跨进程死锁（本进程持锁 + 等对端 binder 返回）。"
                "单 trace 不可证，建议 re-probe 对端进程 trace。"
            ),
            "wikiRefs": [
                "wiki/MTK/swt/18.Binder Stuck.md",
                "wiki/实例/ANR-死锁.md",
            ],
            "nextActions": [
                "查 logcat / AnrManager 段确认主线程 binder 调用的对端进程名",
                "对对端进程 trace 重新 probe，确认其是否因等本进程的锁而阻塞",
            ],
        })

    # Single-edge / chain-style classifications for non-cycle waiters.
    for thread in threads:
        if _is_thread_suspended(thread):
            continue
        if not _is_thread_monitor_blocked(thread):
            continue
        waiter_tid = thread.get("tid")
        if not waiter_tid or waiter_tid in cycle_tids:
            continue
        outgoing = [edge for edge in edges if edge["waiterTid"] == waiter_tid]
        if not outgoing:
            continue
        chain = _follow_owner_chain(waiter_tid, edges, threads_by_tid)
        chain_set = set(chain)
        if any(tid in cycle_tids for tid in chain):
            continue
        if len(chain) >= 3 and len(chain) == len(chain_set):
            chain_labels = [_short_thread_label(threads_by_tid.get(tid), tid) for tid in chain]
            hints.append({
                "id": "LOCK_OWNER_BLOCKED",
                "category": "main_block",
                "severity": "warning",
                "confidence": "strong",
                "scope": "thread",
                "anchorTid": waiter_tid,
                "chain": chain,
                "message": (
                    f"等锁链 {' → '.join(chain_labels)}（{len(chain) - 1} 跳，未成环）："
                    "链式阻塞，先解最末端 owner。"
                ),
                "wikiRefs": ["wiki/实例/ANR-死锁.md"],
                "nextActions": [
                    f"先解 tid={chain[-1]} 的阻塞，链上其它锁会依次释放",
                ],
            })
            continue

        # Single direct edge: classify by owner's runtime state.
        edge = outgoing[0]
        owner = threads_by_tid.get(edge["ownerTid"], {})
        owner_state = owner.get("threadState")
        owner_label = _short_thread_label(owner, edge["ownerTid"])
        waiter_label = _short_thread_label(thread, waiter_tid)
        if owner_state in {"sleeping", "timed_waiting"}:
            hints.append({
                "id": "LOCK_OWNER_SLEEPING",
                "category": "main_block",
                "severity": "warning",
                "confidence": "strong",
                "scope": "thread",
                "anchorTid": waiter_tid,
                "edges": [edge],
                "message": (
                    f"{waiter_label} 等待 monitor <{edge['lockObject']}>，"
                    f"但 owner {owner_label} 正在 {owner_state}（持锁后异步耗时，典型：SP/IO/sleep）。"
                ),
                "wikiRefs": ["wiki/实例/ANR-死锁.md"],
                "nextActions": [
                    f"查看 tid={edge['ownerTid']} 的栈，定位持锁后耗时操作",
                ],
            })
        elif _is_thread_monitor_blocked(owner):
            # Owner also blocked but chain shorter than 3 (e.g. only 2 nodes
            # without forming a cycle). Surface a milder hint.
            hints.append({
                "id": "LOCK_OWNER_BLOCKED",
                "category": "main_block",
                "severity": "warning",
                "confidence": "strong",
                "scope": "thread",
                "anchorTid": waiter_tid,
                "chain": chain,
                "message": (
                    f"{waiter_label} 等 monitor，owner {owner_label} 也处于 Blocked；"
                    "存在二跳锁等待，建议追溯其等锁目标。"
                ),
                "wikiRefs": ["wiki/实例/ANR-死锁.md"],
                "nextActions": [
                    f"追溯 tid={edge['ownerTid']} 的 waitingLocks 目标",
                ],
            })
        else:
            hints.append({
                "id": "LOCK_CONTENTION_BLOCKED",
                "category": "main_block",
                "severity": "warning",
                "confidence": "strong",
                "scope": "thread",
                "anchorTid": waiter_tid,
                "edges": [edge],
                "message": (
                    f"{waiter_label} 等 monitor <{edge['lockObject']}>，"
                    f"owner {owner_label} 正在 {owner_state or 'unknown'}：普通锁竞争。"
                ),
                "wikiRefs": ["wiki/实例/ANR-死锁.md"],
                "nextActions": [
                    f"查看 tid={edge['ownerTid']} 的栈，评估持锁临界区耗时",
                ],
            })

    return hints


def consolidate_deadlock_across_traces(
    trace_contents: list[str],
    *,
    anchor_timestamp: str | None = None,
) -> dict[str, Any]:
    """Cross-trace consistency check for deadlock cycles (MTK SOP).

    A single trace is a sampling snapshot, so even a perfect ``DEADLOCK_CYCLE``
    is technically a single observation. When ANR collection captures ≥2
    traces 1–3s apart, this helper runs ``preprocess_trace_content`` on each,
    compares the cycle node-sets, and emits a ``DEADLOCK_CYCLE_CONFIRMED``
    hint for any cycle whose tid-set appears in ≥2 of the traces.

    Returns a structured report — does NOT mutate any trace's own hints.
    """

    if not trace_contents:
        return {
            "traceCount": 0,
            "perTraceLockGraphs": [],
            "perTraceDeadlockHints": [],
            "consistentCycles": [],
            "upgradedHints": [],
        }

    per_trace = [
        preprocess_trace_content(content, anchor_timestamp=anchor_timestamp)
        for content in trace_contents
    ]
    per_trace_graphs = [t.get("lockGraph") for t in per_trace]
    per_trace_hints = [t.get("deadlockHints", []) or [] for t in per_trace]

    occurrence_index: dict[tuple[frozenset[str], bool], list[int]] = {}
    for trace_index, trace_result in enumerate(per_trace):
        graph = trace_result.get("lockGraph") or {}
        for cycle in graph.get("cycles", []) or []:
            tid_set = frozenset(cycle.get("tids", []) or [])
            if not tid_set:
                continue
            key = (tid_set, bool(cycle.get("selfLoop", False)))
            occurrence_index.setdefault(key, []).append(trace_index)

    consistent_cycles: list[dict[str, Any]] = []
    upgraded_hints: list[dict[str, Any]] = []
    for (tid_set, self_loop), indices in occurrence_index.items():
        unique_indices = sorted(set(indices))
        if len(unique_indices) < 2:
            continue
        sorted_tids = sorted(tid_set, key=_tid_sort_key)
        consistent_cycles.append({
            "tids": sorted_tids,
            "selfLoop": self_loop,
            "traceIndices": unique_indices,
            "occurrences": len(unique_indices),
        })
        upgraded_hints.append({
            "id": "DEADLOCK_CYCLE_CONFIRMED",
            "category": "main_block",
            "severity": "critical",
            "confidence": "confirmed",
            "scope": "global",
            "tids": sorted_tids,
            "selfLoop": self_loop,
            "traceIndices": unique_indices,
            "occurrences": len(unique_indices),
            "message": (
                f"在 {len(unique_indices)} 份 trace（dump #{unique_indices}）中检测到相同的锁等待环 "
                f"{sorted_tids}，节点集合一致 → 跨 trace 一致性检查通过，升级为 CONFIRMED_DEADLOCK。"
            ),
            "wikiRefs": ["wiki/MTK/swt/17.Deadlock.md"],
            "nextActions": [
                "可作为根因结论提交，不再需要进一步复核",
                "按环顺序追溯各 tid 的 Java 栈，给出加锁顺序冲突的修复建议",
            ],
        })

    return {
        "traceCount": len(per_trace),
        "perTraceLockGraphs": per_trace_graphs,
        "perTraceDeadlockHints": per_trace_hints,
        "consistentCycles": consistent_cycles,
        "upgradedHints": upgraded_hints,
    }


def _resolve_owner_thread(threads: list[dict[str, Any]], primary_thread: dict[str, Any] | None) -> dict[str, Any] | None:
    if not primary_thread:
        return None
    owner_tid = primary_thread.get("lockOwnerTid")
    if not owner_tid:
        return None
    for thread in threads:
        if thread.get("tid") == owner_tid:
            return thread
    return None


def _build_binder_summary(threads: list[dict[str, Any]], primary_thread: dict[str, Any] | None) -> dict[str, Any]:
    binder_threads = [
        thread for thread in threads
        if thread.get("threadRole") == "binder" or thread.get("binderCallKind")
    ]
    kind_counts: dict[str, int] = {}
    for thread in binder_threads:
        kind = thread.get("binderCallKind")
        if kind:
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
    top_binder_threads = sorted(
        binder_threads,
        key=lambda thread: (
            0 if thread.get("isMainThread") else 1,
            0 if thread.get("binderCallKind") == "binder_wait_reply" else 1,
            thread.get("threadName", ""),
        ),
    )[:5]
    return {
        "binderThreadCount": len([thread for thread in threads if thread.get("threadRole") == "binder"]),
        "binderWaitChainDetected": bool(primary_thread and primary_thread.get("binderCallKind")) or bool(binder_threads),
        "mainThreadBinderBlocked": bool(primary_thread and primary_thread.get("isMainThread") and primary_thread.get("binderCallKind")),
        "mainThreadBinderCallKind": primary_thread.get("binderCallKind") if primary_thread else None,
        "mainThreadBinderDriverFrame": primary_thread.get("binderDriverFrame") if primary_thread else None,
        "binderThreadPoolCount": kind_counts.get("binder_thread_pool", 0),
        "binderReplyWaitCount": kind_counts.get("binder_wait_reply", 0),
        "binderBacklogCount": kind_counts.get("binder_backlog", 0),
        "binderDriverIoCount": kind_counts.get("binder_driver_io", 0),
        "topBinderThreads": [
            {
                "threadName": thread.get("threadName"),
                "tid": thread.get("tid"),
                "sysTid": thread.get("sysTid"),
                "threadRole": thread.get("threadRole"),
                "binderCallKind": thread.get("binderCallKind"),
                "blockHint": thread.get("blockHint"),
                "binderDriverFrame": thread.get("binderDriverFrame"),
                "isMainThread": thread.get("isMainThread"),
            }
            for thread in top_binder_threads
        ],
    }


def _build_render_summary(threads: list[dict[str, Any]], primary_thread: dict[str, Any] | None) -> dict[str, Any]:
    render_threads = [
        thread for thread in threads
        if thread.get("threadRole") == "render" or thread.get("renderCallKind")
    ]
    kind_counts: dict[str, int] = {}
    for thread in render_threads:
        kind = thread.get("renderCallKind")
        if kind:
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
    top_render_threads = sorted(
        render_threads,
        key=lambda thread: (
            0 if thread.get("isMainThread") else 1,
            0 if thread.get("renderCallKind") in {"main_do_frame", "egl_swap_wait", "render_gpu_wait"} else 1,
            thread.get("threadName", ""),
        ),
    )[:5]
    return {
        "renderWaitChainDetected": bool(primary_thread and primary_thread.get("renderCallKind")) or bool(render_threads),
        "mainThreadRenderBlocked": bool(primary_thread and primary_thread.get("isMainThread") and primary_thread.get("renderCallKind")),
        "mainThreadRenderCallKind": primary_thread.get("renderCallKind") if primary_thread else None,
        "mainThreadRenderDriverFrame": primary_thread.get("renderDriverFrame") if primary_thread else None,
        "renderThreadCount": len([thread for thread in threads if thread.get("threadRole") == "render"]),
        "renderGpuWaitCount": kind_counts.get("render_gpu_wait", 0) + kind_counts.get("egl_swap_wait", 0),
        "renderDoFrameCount": kind_counts.get("main_do_frame", 0),
        "topRenderThreads": [
            {
                "threadName": thread.get("threadName"),
                "tid": thread.get("tid"),
                "sysTid": thread.get("sysTid"),
                "threadRole": thread.get("threadRole"),
                "renderCallKind": thread.get("renderCallKind"),
                "renderDriverFrame": thread.get("renderDriverFrame"),
                "blockHint": thread.get("blockHint"),
                "isMainThread": thread.get("isMainThread"),
            }
            for thread in top_render_threads
        ],
    }


def _build_suspend_summary(threads: list[dict[str, Any]]) -> dict[str, Any]:
    suspended = [thread for thread in threads if thread.get("artThreadState") == "SUSPENDED"]
    vmwait = [thread for thread in threads if thread.get("artThreadState") == "VMWAIT"]
    debugger_touched = [thread for thread in threads if (thread.get("dsCount") and thread.get("dsCount") not in {"0", 0})]
    total = len(threads)
    suspended_count = len(suspended)
    vmwait_count = len(vmwait)
    suspended_ratio = (suspended_count / total) if total else 0.0
    stw_pause_detected = suspended_count >= 2 and suspended_ratio >= 0.5
    vm_wait_cluster_detected = vmwait_count >= 2
    debugger_suspicion = bool(debugger_touched)
    return {
        "stwPauseDetected": stw_pause_detected,
        "vmWaitClusterDetected": vm_wait_cluster_detected,
        "debuggerSuspicion": debugger_suspicion,
        "suspendedThreadCount": suspended_count,
        "vmWaitThreadCount": vmwait_count,
        "debuggerTouchedThreadCount": len(debugger_touched),
        "topSuspendedThreads": [_thread_brief(thread) for thread in suspended[:5]],
        "topVmWaitThreads": [_thread_brief(thread) for thread in vmwait[:5]],
    }


def _build_cpu_summary(threads: list[dict[str, Any]], primary_thread: dict[str, Any] | None) -> dict[str, Any]:
    runnable_threads = [thread for thread in threads if _thread_is_runnable_like(thread)]
    top_runnable_threads = sorted(
        runnable_threads,
        key=lambda thread: (
            0 if thread.get("isMainThread") else 1,
            -_thread_run_ns(thread),
            _thread_wait_ns(thread),
            thread.get("threadName", ""),
        ),
    )[:5]

    main_run_ns = _thread_run_ns(primary_thread) if primary_thread else 0
    main_wait_ns = _thread_wait_ns(primary_thread) if primary_thread else 0
    main_wait_run_ratio = None
    if main_run_ns > 0:
        main_wait_run_ratio = round(main_wait_ns / main_run_ns, 2)

    main_runnable_like = bool(primary_thread and _thread_is_runnable_like(primary_thread))
    scheduler_pressure_detected = bool(
        primary_thread
        and main_runnable_like
        and main_wait_ns >= 1_000_000_000
        and main_wait_ns > (main_run_ns * 2 if main_run_ns > 0 else 0)
    )
    cpu_busy_execution_detected = bool(
        primary_thread
        and main_runnable_like
        and main_run_ns >= 1_000_000_000
        and main_run_ns > (main_wait_ns * 2 if main_wait_ns > 0 else 0)
    )

    return {
        "schedulerPressureDetected": scheduler_pressure_detected,
        "cpuBusyExecutionDetected": cpu_busy_execution_detected,
        "mainThreadRunnableLike": main_runnable_like,
        "mainThreadRunNs": main_run_ns,
        "mainThreadWaitNs": main_wait_ns,
        "mainThreadWaitRunRatio": main_wait_run_ratio,
        "runnableThreadCount": len(runnable_threads),
        "topRunnableThreads": [_thread_cpu_brief(thread) for thread in top_runnable_threads],
    }


def _extract_first_matching_line(block: list[str], prefix: str) -> str | None:
    for line in block:
        stripped = line.strip()
        if stripped.startswith(prefix):
            return stripped
    return None


def _extract_first_java_frame(block: list[str]) -> str | None:
    for line in block:
        stripped = line.strip()
        if stripped.startswith("at "):
            return stripped
    return None


def _extract_looper_frame(block: list[str]) -> str | None:
    for line in block:
        stripped = line.strip()
        if any(token in stripped for token in ("MessageQueue.nativePollOnce", "MessageQueue.next", "Looper.loopOnce", "Looper.loop", "ActivityThread.main")):
            return stripped
    return None


def _thread_brief(thread: dict[str, Any]) -> dict[str, Any]:
    return {
        "threadName": thread.get("threadName"),
        "tid": thread.get("tid"),
        "sysTid": thread.get("sysTid"),
        "artThreadState": thread.get("artThreadState"),
        "javaThreadState": thread.get("javaThreadState"),
        "threadRole": thread.get("threadRole"),
    }


def _thread_cpu_brief(thread: dict[str, Any]) -> dict[str, Any]:
    return {
        "threadName": thread.get("threadName"),
        "tid": thread.get("tid"),
        "sysTid": thread.get("sysTid"),
        "threadRole": thread.get("threadRole"),
        "linuxState": thread.get("linuxState"),
        "artThreadState": thread.get("artThreadState"),
        "runNs": _thread_run_ns(thread),
        "waitNs": _thread_wait_ns(thread),
    }


def _extract_binder_driver_frame(block: list[str]) -> str | None:
    binder_markers = (
        "android::IPCThreadState::talkWithDriver",
        "android::IPCThreadState::joinThreadPool",
        "android::IPCThreadState::getAndExecuteCommand",
        "android::IPCThreadState::waitForResponse",
        "android.os.BinderProxy.transact",
        "BinderProxy.transact",
        "transactNative",
        "__ioctl",
        "ioctl+",
    )
    for line in block:
        stripped = line.strip()
        if any(marker.lower() in stripped.lower() for marker in binder_markers):
            return stripped
    return None


def _extract_render_driver_frame(block: list[str]) -> str | None:
    render_markers = (
        "Choreographer.doFrame",
        "ViewRootImpl.doTraversal",
        "ViewRootImpl.performTraversals",
        "ThreadedRenderer",
        "RenderThread",
        "DrawFrameTask",
        "eglSwapBuffers",
        "CanvasContext",
    )
    for line in block:
        stripped = line.strip()
        if any(marker.lower() in stripped.lower() for marker in render_markers):
            return stripped
    return None


def _classify_thread_role(thread_name: str, block_hint: str | None, lower: str) -> str:
    name_lower = thread_name.lower()
    if thread_name == "main":
        return "main"
    if "binder" in name_lower:
        return "binder"
    if "signal catcher" in name_lower:
        return "signal_catcher"
    if "renderthread" in name_lower or "hwui" in name_lower:
        return "render"
    if "jdwp" in name_lower:
        return "jdwp"
    if "finalizer" in name_lower:
        return "finalizer"
    if "referencequeue" in name_lower:
        return "reference_queue"
    if block_hint in {"gpu_wait"}:
        return "render"
    return "worker"


def _classify_binder_call_kind(lower: str, block_hint: str | None, thread_name: str) -> str | None:
    if block_hint == "binder_backlog" or "transaction backlog" in lower:
        return "binder_backlog"
    if any(token in lower for token in ("waitforresponse", "transactnative", "binderproxy.transact")) or block_hint == "binder_reply_wait":
        return "binder_wait_reply"
    if "jointhreadpool" in lower or "getandexecutecommand" in lower:
        return "binder_thread_pool"
    if "talkwithdriver" in lower or "__ioctl" in lower or "ioctl+" in lower or block_hint == "binder_wait":
        return "binder_driver_io" if thread_name == "main" else "binder_thread_pool"
    return None


def _classify_render_call_kind(thread_name: str, block_hint: str | None, lower: str) -> str | None:
    if any(token in lower for token in ("choreographer.doframe", "doframe(", "doframe ", "viewrootimpl.dotraversal", "viewrootimpl.performtraversals")):
        return "main_do_frame" if thread_name == "main" else "render_do_frame"
    if "eglswapbuffers" in lower:
        return "egl_swap_wait"
    if block_hint == "gpu_wait":
        return "render_gpu_wait"
    if "renderthread" in thread_name.lower() or "drawframetask" in lower or "canvascontext" in lower:
        return "render_thread_active"
    return None


def _map_java_thread_state(art_state: str | None) -> str | None:
    if not art_state:
        return None
    normalized = art_state.upper()
    mapping = {
        "RUNNABLE": "RUNNABLE",
        "RUNNING": "RUNNABLE",
        "NATIVE": "RUNNABLE",
        "SUSPENDED": "RUNNABLE",
        "BLOCKED": "BLOCKED",
        "MONITOR": "BLOCKED",
        "WAITING": "WAITING",
        "WAIT": "WAITING",
        "VMWAIT": "WAITING",
        "TIMED_WAITING": "TIMED_WAITING",
        "TIMED_WAIT": "TIMED_WAITING",
        "SLEEPING": "TIMED_WAITING",
        "ZOMBIE": "TERMINATED",
        "INITIALIZING": "NEW",
        "STARTING": "NEW",
        "UNKNOWN": "UNKNOWN",
    }
    return mapping.get(normalized, normalized)


def _thread_is_runnable_like(thread: dict[str, Any] | None) -> bool:
    if not thread:
        return False
    return (thread.get("javaThreadState") == "RUNNABLE") or (thread.get("linuxState") == "R")


def _parse_schedstat(raw: str | None) -> dict[str, int] | None:
    if not raw:
        return None
    parts = raw.split()
    if len(parts) != 3:
        return None
    try:
        run_ns, wait_ns, slices = (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None
    return {"runNs": run_ns, "waitNs": wait_ns, "timeSlices": slices}


def _thread_run_ns(thread: dict[str, Any] | None) -> int:
    if not thread:
        return 0
    parsed = thread.get("schedstatParsed") or {}
    return int(parsed.get("runNs", 0))


def _thread_wait_ns(thread: dict[str, Any] | None) -> int:
    if not thread:
        return 0
    parsed = thread.get("schedstatParsed") or {}
    return int(parsed.get("waitNs", 0))


def _thread_suspicion(lower: str, block_hint: str | None, thread_state: str | None, is_main_thread: bool) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    if is_main_thread:
        score += 2
        reasons.append("main-thread")
    if block_hint:
        score += 2
        reasons.append(block_hint)
    if thread_state in {"blocked", "native_waiting", "waiting"}:
        score += 1
        reasons.append(thread_state)
    if "input dispatching" in lower or "focused window" in lower:
        score += 3
        reasons.append("anr-signal")
    return score, reasons


def _trace_section_rank(section: list[str], anchor_dt: datetime | None) -> tuple[int, float, int, int, str]:
    lowered = [line.lower() for line in section]
    signal_hits = sum(1 for line in lowered if any(pattern in line for pattern in TRACE_SIGNAL_PATTERNS))
    main_hits = sum(1 for line in lowered if "main tid=" in line)
    first_timestamp = next((_extract_timestamp(line) for line in section if _extract_timestamp(line) is not None), None)
    anchor_distance = abs((first_timestamp - anchor_dt).total_seconds()) if first_timestamp and anchor_dt else float("inf")
    anchor_bucket = 0 if anchor_distance <= 5 else 1 if anchor_distance <= 30 else 2 if anchor_distance != float("inf") else 3
    timestamp_key = _timestamp_to_raw(first_timestamp) if first_timestamp else "99-99 99:99:99.999"
    return (
        0 if signal_hits else 1,
        anchor_bucket,
        anchor_distance,
        0 if main_hits else 1,
        timestamp_key,
    )


def _extract_process_name(lines: list[str]) -> str | None:
    for line in lines:
        match = TRACE_PROCESS_RE.search(line)
        if match:
            return match.group("process")
    return None


def _extract_pid(lines: list[str]) -> str | None:
    for line in lines:
        match = TRACE_PID_RE.search(line)
        if match:
            return match.group("pid")
    return None


def _trace_thread_state(lower: str) -> str | None:
    if "blocked" in lower:
        return "blocked"
    if "timed waiting" in lower:
        return "timed_waiting"
    if "parking" in lower or "parked" in lower:
        return "parking"
    if "native" in lower and "waiting" in lower:
        return "native_waiting"
    if "sleeping" in lower:
        return "sleeping"
    if "waiting" in lower:
        return "waiting"
    if "runnable" in lower:
        return "runnable"
    return None


def _trace_block_hint(lower: str) -> str | None:
    if "focused window" in lower or "no focus window" in lower:
        return "focus_window_wait"
    if "input dispatching" in lower:
        return "input_dispatch_wait"
    if "binder" in lower and ("transaction backlog" in lower or "backlog" in lower):
        return "binder_backlog"
    if "binder" in lower and "reply" in lower:
        return "binder_reply_wait"
    if "binder" in lower:
        return "binder_wait"
    if "monitor contention" in lower or "waiting to lock" in lower:
        return "monitor_contention"
    if "monitor" in lower or "lock" in lower:
        return "lock_contention"
    if "epoll" in lower or "pollonce" in lower or "poll once" in lower:
        return "native_poll_wait"
    if "futex" in lower:
        return "futex_wait"
    if "gpu" in lower and "wait" in lower:
        return "gpu_wait"
    if "native" in lower and "waiting" in lower:
        return "native_wait"
    if "waiting" in lower or "blocked" in lower:
        return "generic_wait_or_block"
    return None


def _extract_timestamp(line: str) -> datetime | None:
    match = TIMESTAMP_RE.search(line)
    if not match:
        return None
    return _parse_timestamp(match.group("ts"))


def _parse_timestamp(raw_timestamp: str | None) -> datetime | None:
    if not raw_timestamp:
        return None
    return datetime.strptime(f"2026-{raw_timestamp}", "%Y-%m-%d %H:%M:%S.%f")


def _timestamp_to_raw(timestamp: datetime) -> str:
    return timestamp.strftime("%m-%d %H:%M:%S.%f")[:-3]
