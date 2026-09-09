"""Conservative D1/D2 Input → WMS/AMS/Recents lifecycle observations.

The module creates a derived, source-coordinate-preserving view over filtered
logs.  A lifecycle event changes the selected ANR group's state only when it
has explicit target PID/process identity or a token scoped by an already
associated Input trigger.  Timestamp proximity, the same TID, a generic
system-server line, or a shared Recents handler alone never establish group
association or blame.
"""
from __future__ import annotations

from collections import defaultdict
import copy
from datetime import datetime, timedelta
import re
from typing import Any, Iterable

from .dump_quality import dump_lifecycle_projection
from .log_filter import parse_log_timestamp, timestamp_to_raw
from .log_identity import parse_line_identity


_SCHEMA_VERSION = "input-lifecycle-v1"
_NO_FOCUS_RE = re.compile(
    r"(?:does\s+not\s+have\s+a\s+focused\s+window|no\s+focus(?:ed)?\s+window|"
    r"no\s+window\s+has\s+focus|mcurrentfocus\s*=\s*null|mfocusedapp\s*=\s*null)",
    re.IGNORECASE,
)
_INPUT_TIMEOUT_RE = re.compile(r"input\s+dispatch(?:ing)?\s+time(?:d\s*out|out)", re.IGNORECASE)
_UNACKED_RE = re.compile(
    r"(?:touched\s+window\s+has\s+not\s+finished\s+processing|"
    r"unack(?:nowledged|ed)|wait(?:ing)?\s+for\s+(?:an\s+)?(?:input\s+)?ack)",
    re.IGNORECASE,
)
_FOCUS_ENTER_RE = re.compile(
    r"(?:focus\s+entering|request(?:ing)?\s+to\s+set\s+focus|focus\s+request)\s*[:=]?\s*(?P<target>[^,\]\s]+)",
    re.IGNORECASE,
)
_FOCUS_LEAVE_RE = re.compile(r"focus\s+leaving\s*[:=]?\s*(?P<target>[^,\]\s]+)?", re.IGNORECASE)
_PENDING_FOCUS_RE = re.compile(r"\b(?:mPendingFocus|pending[\s_-]+focus)\b", re.IGNORECASE)
_WINDOW_COMPONENT_RE = re.compile(r"(?P<package>[A-Za-z][\w.]*(?::[\w.-]+)?)/(?P<component>[^\s,}\]]+)")
_FOR_PROCESS_RE = re.compile(r"\bfor\s+(?P<process>[A-Za-z][\w.]*(?::[\w.-]+)?)\b", re.IGNORECASE)
_RECENTS_CONSUMER_RE = re.compile(r"\b(?P<consumer>recents_animation_input_consumer)\b", re.IGNORECASE)
_HANDLER_RE = re.compile(r"\b(?:handler|h)\s*[=:]\s*(?P<value>0x[0-9a-f]+|[A-Za-z0-9_.-]+)", re.IGNORECASE)
_LOG_ID_RE = re.compile(r"\b(?:logId|gestureId)\s*[=:]\s*(?P<value>[A-Za-z0-9_.-]+)", re.IGNORECASE)
_TOKEN_RE = re.compile(r"\b(?:windowToken|activityToken|token)\s*[=:]\s*(?P<value>[A-Za-z0-9_.:@/-]+)", re.IGNORECASE)
_DISPLAY_RE = re.compile(r"\b(?:displayId|display)\s*[=:]\s*(?P<value>\d+)\b", re.IGNORECASE)
_EXPLICIT_YEAR_PREFIX_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\b")
_RECENTS_LIFECYCLE_KINDS = frozenset({
    "recents_started",
    "recents_start_pending",
    "recents_callback",
    "recents_finished",
    "recents_cancelled",
})


# These statuses mean a literal observed event only.  They deliberately do not
# identify a root cause, a responsible process, or the state of an omitted
# queue/controller.
def _kinds_for_line(line: str) -> tuple[str, ...]:
    lowered = line.lower()
    kinds: list[str] = []
    if _NO_FOCUS_RE.search(line):
        kinds.extend(("trigger:no_focus", "focus_absent"))
    elif _INPUT_TIMEOUT_RE.search(line):
        kinds.append("trigger:unacked_input" if _UNACKED_RE.search(line) else "trigger:input_timeout")

    if _FOCUS_ENTER_RE.search(line):
        kinds.append("focus_entered")
    if _FOCUS_LEAVE_RE.search(line):
        kinds.append("focus_left")
    if _PENDING_FOCUS_RE.search(line):
        kinds.append("focus_pending")
    if "not_visible" in lowered or "not visible" in lowered:
        kinds.append("window_not_visible")
    if "reportdrawfinished" in lowered or "firstwindowdrawn" in lowered or "alldrawn" in lowered:
        kinds.append("window_drawn")
    if "wm_on_resume_called" in lowered or "top_resumed_gained" in lowered or "resumed=true" in lowered:
        kinds.append("activity_resumed")

    if "recents_animation_input_consumer" in lowered:
        kinds.append("recents_input_consumer")
    if "startrecentsanimation" in lowered:
        kinds.append("recents_started")
    if "mrecentsanimationstartpending" in lowered and "true" in lowered:
        kinds.append("recents_start_pending")
    if "ontasksappeared" in lowered or "onanimationstart" in lowered or "onrecentsanimationstart" in lowered:
        kinds.append("recents_callback")
    if any(token in lowered for token in ("finishrecentsanimation", "finishrunningrecentsanimation", "cleanuprecentsanimation", "onanimationfinished")):
        kinds.append("recents_finished")
    if "recents" in lowered and any(token in lowered for token in ("cancel", "cancelled", "canceled")):
        kinds.append("recents_cancelled")

    if "input" in lowered and any(token in lowered for token in ("cancel pending", "input cancelled", "input canceled", "cancel input")):
        kinds.append("input_cancelled")
    if "input" in lowered and any(token in lowered for token in ("input dispatch recovered", "input recovered", "dispatch resumed")):
        kinds.append("input_recovered")
    if "input" in lowered and any(token in lowered for token in ("suppressed", "suppression", "drop pending")):
        kinds.append("queue_suppressed")
    return tuple(dict.fromkeys(kinds))


def _correlation_key(line: str) -> str | None:
    log_id = _LOG_ID_RE.search(line)
    if log_id:
        return f"logId:{log_id.group('value')}"
    handler = _HANDLER_RE.search(line)
    if handler:
        return f"handler:{handler.group('value')}"
    return None


def _timestamp_for_anchor(line: str, anchor_timestamp: datetime | None) -> datetime | None:
    """Parse a log timestamp relative to the selected ANR's calendar year.

    Threadtime logcat usually omits a year.  The nearest calendar year to the
    anchor is used, including the December/January rollover; an explicit year
    in the line always wins in ``parse_log_timestamp``.
    """
    timestamp = parse_log_timestamp(line, year=anchor_timestamp.year if anchor_timestamp else datetime.now().year)
    if timestamp is None or anchor_timestamp is None or _EXPLICIT_YEAR_PREFIX_RE.match(line):
        return timestamp
    if timestamp - anchor_timestamp > timedelta(days=183):
        return timestamp.replace(year=timestamp.year - 1)
    if anchor_timestamp - timestamp > timedelta(days=183):
        return timestamp.replace(year=timestamp.year + 1)
    return timestamp


def _identity_fields(line: str) -> tuple[set[str], set[str]]:
    identity = parse_line_identity(line)
    pids = set(identity.target_pids)
    processes = set(identity.target_processes)
    process = _FOR_PROCESS_RE.search(line)
    if process:
        processes.add(process.group("process"))
    component = _WINDOW_COMPONENT_RE.search(line)
    if component:
        processes.add(component.group("package"))
    return pids, processes


def _entity_from_line(line: str) -> dict[str, Any] | None:
    consumer = _RECENTS_CONSUMER_RE.search(line)
    if consumer:
        return {"kind": "input_consumer", "name": consumer.group("consumer"), "pid": None}
    pids, processes = _identity_fields(line)
    if len(processes) == 1:
        return {
            "kind": "process",
            "name": next(iter(processes)),
            "pid": next(iter(pids)) if len(pids) == 1 else None,
        }
    focus = _FOCUS_ENTER_RE.search(line) or _FOCUS_LEAVE_RE.search(line)
    if focus and focus.groupdict().get("target"):
        return {"kind": "window", "name": focus.group("target"), "pid": None}
    return None


def _event(source: str, line_index: int, line: str, *, anchor_timestamp: datetime | None) -> dict[str, Any] | None:
    kinds = _kinds_for_line(line)
    if not kinds:
        return None
    timestamp = _timestamp_for_anchor(line, anchor_timestamp)
    token = _TOKEN_RE.search(line)
    display = _DISPLAY_RE.search(line)
    pids, processes = _identity_fields(line)
    return {
        "id": f"{source}:{line_index}",
        "source": source,
        "lineIndex": line_index,
        "timestamp": timestamp_to_raw(timestamp) if timestamp else None,
        "_timestamp": timestamp,
        "_line": line,
        "kinds": list(kinds),
        "entity": _entity_from_line(line),
        "targetPids": sorted(pids, key=int),
        "targetProcesses": sorted(processes),
        "token": token.group("value") if token else None,
        "displayId": display.group("value") if display else None,
        "correlationKey": _correlation_key(line),
        "associationStatus": "unknown",
    }


def _events_for_source(source: str, lines: Iterable[str], *, anchor_timestamp: datetime | None) -> list[dict[str, Any]]:
    return [
        item for index, line in enumerate(lines)
        if (item := _event(source, index, line, anchor_timestamp=anchor_timestamp)) is not None
    ]


def filter_input_lifecycle_windows(
    lines: Iterable[str],
    *,
    windows: Iterable[tuple[datetime | None, float, float]],
) -> list[list[str]]:
    """Select D1/D2 lifecycle signals for many ANR windows in one pass.

    The function admits explicit system-server/Launcher lifecycle events to a
    window as *context*; later group-identity/token checks decide whether they
    may update target/blame/lifecycle state.  This permits WMS/Recents evidence
    without allowing package filtering to silently discard it, while avoiding
    a full logcat scan per group.
    """
    normalized_windows = list(windows)
    retained = [[] for _ in normalized_windows]
    for line in lines:
        if not _kinds_for_line(line):
            continue
        for index, (anchor_timestamp, before_seconds, after_seconds) in enumerate(normalized_windows):
            if anchor_timestamp is None:
                continue
            timestamp = _timestamp_for_anchor(line, anchor_timestamp)
            if timestamp is None:
                continue
            delta = (timestamp - anchor_timestamp).total_seconds()
            if -before_seconds <= delta <= after_seconds:
                retained[index].append(line)
    return retained


def filter_input_lifecycle_lines(
    lines: Iterable[str],
    *,
    anchor_timestamp: datetime | None,
    before_seconds: float,
    after_seconds: float,
) -> list[str]:
    """Single-window convenience wrapper for ``filter_input_lifecycle_windows``."""
    return filter_input_lifecycle_windows(lines, windows=[(anchor_timestamp, before_seconds, after_seconds)])[0]


def _has_kind(event: dict[str, Any], kind: str) -> bool:
    return kind in event["kinds"]


def _event_at_or_before(event: dict[str, Any], anchor_timestamp: datetime | None) -> bool:
    timestamp = event["_timestamp"]
    return timestamp is not None and (anchor_timestamp is None or timestamp <= anchor_timestamp)


def _event_after(trigger: dict[str, Any], event: dict[str, Any], anchor_timestamp: datetime | None) -> bool:
    trigger_timestamp = trigger["_timestamp"]
    event_timestamp = event["_timestamp"]
    return (
        trigger_timestamp is not None
        and event_timestamp is not None
        and event_timestamp >= trigger_timestamp
        and (anchor_timestamp is None or event_timestamp <= anchor_timestamp)
    )


def _edge(kind: str, from_id: str, to: str, evidence_ids: list[str]) -> dict[str, Any]:
    return {"kind": kind, "from": from_id, "to": to, "evidenceEventIds": sorted(set(evidence_ids))}


def _group_identity(dump_quality: dict[str, Any], anchor: dict[str, Any] | None) -> tuple[set[str], set[str]]:
    target = dump_quality.get("target") or {}
    pids = {str(target["pid"])} if target.get("pid") else set()
    processes = {str(target["processName"])} if target.get("processName") else set()
    if anchor:
        if anchor.get("packageName"):
            processes.add(str(anchor["packageName"]))
        line = anchor.get("line")
        if isinstance(line, str):
            line_pids, line_processes = _identity_fields(line)
            pids.update(line_pids)
            processes.update(line_processes)
    return pids, processes


def _identity_association(event: dict[str, Any], target_pids: set[str], target_processes: set[str]) -> str:
    if event.get("isAnchor"):
        return "matched"
    event_pids = set(event["targetPids"])
    event_processes = set(event["targetProcesses"])
    pid_matches = target_pids & event_pids
    process_matches = target_processes & event_processes
    # A line that explicitly names both the selected PID and a different
    # process (or vice versa) is internally contradictory for this group.
    # Do not let one matching field silently override the incompatible one.
    if (event_pids and not pid_matches) or (event_processes and not process_matches):
        return "wrong_identity"
    if pid_matches or process_matches:
        return "matched"
    return "unknown"


def _apply_association(
    events: list[dict[str, Any]],
    *,
    target_pids: set[str],
    target_processes: set[str],
    scoped_token: str | None,
) -> None:
    for event in events:
        identity_status = _identity_association(event, target_pids, target_processes)
        if identity_status == "wrong_identity":
            event["associationStatus"] = "wrong_identity"
        elif identity_status == "matched":
            event["associationStatus"] = "matched"
        elif scoped_token and event.get("token") == scoped_token:
            event["associationStatus"] = "matched"
        else:
            event["associationStatus"] = "unknown"


def _entity_with_sources(entity: dict[str, Any], event_ids: list[str]) -> dict[str, Any]:
    return {**entity, "sourceEventIds": sorted(set(event_ids))}


def _resolve_entity(candidates: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, bool]:
    if not candidates:
        return None, False
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        entity = candidate["entity"]
        assert isinstance(entity, dict)
        grouped[(entity["kind"], entity["name"])].append(candidate)
    if len(grouped) != 1:
        return None, True
    matches = next(iter(grouped.values()))
    pids = {entry["entity"]["pid"] for entry in matches if entry["entity"]["pid"] is not None}
    if len(pids) > 1:
        return None, True
    entity = dict(matches[0]["entity"])
    entity["pid"] = next(iter(pids), None)
    return _entity_with_sources(entity, [entry["id"] for entry in matches]), False


def _recents_terminal_kind(event: dict[str, Any]) -> str | None:
    if _has_kind(event, "recents_cancelled"):
        return "cancelled"
    if _has_kind(event, "recents_finished"):
        return "finished"
    return None


def _record_sort_key(record: dict[str, Any]) -> tuple[bool, datetime, str]:
    return (record["_timestamp"] is None, record["_timestamp"] or datetime.max, record["id"])


def _instance_status(records: list[dict[str, Any]], anchor_timestamp: datetime | None) -> tuple[str, dict[str, Any] | None]:
    start = next((record for record in records if _has_kind(record, "recents_started")), None)
    terminal = next((record for record in records if _recents_terminal_kind(record)), None)
    if start is None:
        return "malformed_missing_start", terminal
    if terminal is None:
        if start["_timestamp"] is not None and anchor_timestamp is not None and start["_timestamp"] <= anchor_timestamp:
            return "pending_at_anchor", None
        return "pending_time_unknown", None
    kind = _recents_terminal_kind(terminal)
    assert kind is not None
    if terminal["_timestamp"] is None or anchor_timestamp is None:
        return f"{kind}_time_unknown", terminal
    return (f"{kind}_before_anchor" if terminal["_timestamp"] <= anchor_timestamp else f"{kind}_after_anchor"), terminal


def _recents_lifecycle(events: list[dict[str, Any]], anchor_timestamp: datetime | None) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    signals = [event for event in events if any(kind in _RECENTS_LIFECYCLE_KINDS for kind in event["kinds"])]
    seed_keys = {
        event["correlationKey"] for event in signals
        if event["correlationKey"] and event["associationStatus"] == "matched"
    }
    keyed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unkeyed_any = False
    for event in signals:
        key = event["correlationKey"]
        if not key:
            # Keep the diagnostic even for unknown context, but never
            # promote it into a keyed lifecycle transition.
            unkeyed_any = True
        elif key in seed_keys and event["associationStatus"] != "wrong_identity":
            # A previously target/token-associated start may bind its later
            # callbacks/terminal record by the same key.  A key alone cannot
            # seed a group association.
            keyed[key].append(event)

    instances: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    gaps: list[str] = []
    for key, records in sorted(keyed.items()):
        records.sort(key=_record_sort_key)
        active: list[dict[str, Any]] | None = None
        segmented: list[tuple[list[dict[str, Any]], bool]] = []
        for record in records:
            if _has_kind(record, "recents_started"):
                if active is not None:
                    segmented.append((active, True))
                active = [record]
            elif active is not None:
                active.append(record)
                if _recents_terminal_kind(record):
                    segmented.append((active, False))
                    active = None
            else:
                gaps.append("orphan_recents_callback")
        if active is not None:
            segmented.append((active, False))

        for records_for_instance, overlapping_start in segmented:
            status, terminal = _instance_status(records_for_instance, anchor_timestamp)
            if overlapping_start:
                status = "malformed_overlapping_start"
                gaps.append("overlapping_recents_start")
            start = records_for_instance[0]
            if terminal is not None and start["_timestamp"] is not None and terminal["_timestamp"] is not None and start["_timestamp"] <= terminal["_timestamp"]:
                terminal_kind = _recents_terminal_kind(terminal)
                assert terminal_kind is not None
                edges.append(_edge(f"recents_{terminal_kind}", start["id"], terminal["id"], [start["id"], terminal["id"]]))
            pending = next((record for record in records_for_instance if _has_kind(record, "recents_start_pending")), None)
            if pending is not None and start["_timestamp"] is not None and pending["_timestamp"] is not None and start["_timestamp"] <= pending["_timestamp"]:
                edges.append(_edge("recents_start_pending", start["id"], pending["id"], [start["id"], pending["id"]]))
            instances.append({
                "correlationKey": key,
                "status": status,
                "eventIds": [record["id"] for record in records_for_instance],
            })

    # ``records`` are timestamp-sorted within a correlation key.  Do not
    # lexically re-sort event IDs (for example, ``logcat:10`` vs
    # ``logcat:2``), which can reverse a segmented animation history.
    active_statuses = [
        instance["status"] for instance in instances
        if instance["status"] in {"pending_at_anchor", "finished_after_anchor", "cancelled_after_anchor"}
    ]
    if len(active_statuses) == 1:
        lifecycle_status = active_statuses[0]
    elif len(active_statuses) > 1:
        lifecycle_status = "ambiguous_at_anchor"
        gaps.append("multiple_recents_instances_active_at_anchor")
    elif instances:
        lifecycle_status = instances[-1]["status"]
    else:
        lifecycle_status = "unknown"
    if unkeyed_any:
        gaps.append("unkeyed_recents_events_unlinked")
    return {"status": lifecycle_status, "instances": instances}, edges, list(dict.fromkeys(gaps))


def _dump_view(dump_quality: dict[str, Any]) -> dict[str, Any]:
    result = {
        "pid": (dump_quality.get("dump") or {}).get("pid"),
        "timestamp": (dump_quality.get("dump") or {}).get("timestamp"),
        "identityStatus": dump_quality.get("identityStatus"),
        "timeStatus": dump_quality.get("timeStatus"),
        "associationStatus": dump_quality.get("associationStatus"),
        "provenanceStatus": dump_quality.get("provenanceStatus"),
        # A read-only DQ1 projection; D1 never reclassifies wrong PID/stale.
        "dumpLifecycle": dump_lifecycle_projection(dump_quality),
    }
    snapshot = dump_quality.get("dumpsysSnapshot")
    if isinstance(snapshot, dict):
        result["dumpsysSnapshot"] = copy.deepcopy(snapshot)
    return result


def refresh_input_lifecycle_dump_quality(lifecycle: dict[str, Any], dump_quality: dict[str, Any]) -> dict[str, Any]:
    """Refresh the DQ1 snapshot after immutable field references are rebound."""
    result = copy.deepcopy(lifecycle)
    dump = _dump_view(dump_quality)
    result["dump"] = dump
    source_findings = result.get("sourceFindings")
    if isinstance(source_findings, dict) and isinstance(source_findings.get("trace"), dict):
        source_findings["trace"]["dump"] = dump
    if dump["associationStatus"] != "associated":
        if "dump_not_associated" not in result["gaps"]:
            result["gaps"].append("dump_not_associated")
    else:
        result["gaps"] = [gap for gap in result["gaps"] if gap != "dump_not_associated"]
    return result


def _source_findings(events: list[dict[str, Any]], trigger: dict[str, Any], original_target: dict[str, Any] | None, blame_target: dict[str, Any] | None, dump: dict[str, Any]) -> dict[str, Any]:
    return {
        "trace": {"dump": dump},
        "eventLog": {
            "triggerEventIds": [event["id"] for event in events if event["source"] == "eventLog" and event["id"] in trigger["sourceEventIds"]],
            "originalTarget": original_target,
        },
        "logcatAnrManager": {
            "blameTarget": blame_target,
            "lifecycleEventIds": [event["id"] for event in events if event["source"] in {"logcat", "anrManager"} and event["associationStatus"] == "matched"],
        },
    }


def build_input_lifecycle(
    *,
    group_id: str,
    anr_type: str,
    anchor: dict[str, Any] | None,
    event_log_lines: Iterable[str],
    logcat_lines: Iterable[str],
    anrmanager_lines: Iterable[str],
    dump_quality: dict[str, Any],
) -> dict[str, Any]:
    """Build a fail-closed D1/D2 view from selected baseline evidence."""
    anchor_timestamp_value = anchor.get("timestamp") if anchor else None
    anchor_timestamp = (
        anchor_timestamp_value if isinstance(anchor_timestamp_value, datetime)
        else _timestamp_for_anchor(anchor_timestamp_value, None) if isinstance(anchor_timestamp_value, str)
        else None
    )
    anchor_line = anchor.get("line") if anchor else None
    events = (
        _events_for_source("eventLog", event_log_lines, anchor_timestamp=anchor_timestamp)
        + _events_for_source("logcat", logcat_lines, anchor_timestamp=anchor_timestamp)
        + _events_for_source("anrManager", anrmanager_lines, anchor_timestamp=anchor_timestamp)
    )
    for event in events:
        event["isAnchor"] = event["source"] == "eventLog" and anchor_line is not None and event["_line"] == anchor_line

    target_pids, target_processes = _group_identity(dump_quality, anchor)
    _apply_association(events, target_pids=target_pids, target_processes=target_processes, scoped_token=None)
    trigger_rank = {"unacked_input": 3, "input_timeout": 2, "no_focus": 1}
    trigger_candidates = [
        event for event in events
        if event["associationStatus"] == "matched" and any(kind.startswith("trigger:") for kind in event["kinds"])
    ]
    trigger_event = max(
        trigger_candidates,
        key=lambda event: (
            max(trigger_rank.get(kind.split(":", 1)[1], 0) for kind in event["kinds"] if kind.startswith("trigger:")),
            # An explicit target-associated trigger with a token provides the
            # narrowest permitted bridge to WMS/Recents records.  Retain the
            # EventLog am_anr trigger only when no stronger observed Input
            # trigger exists; time proximity by itself is never a tie-breaker
            # for association.
            event.get("token") is not None,
            event["source"] != "eventLog",
            event["_timestamp"] is not None,
            event["_timestamp"] or datetime.min,
        ),
        default=None,
    )
    scoped_token = trigger_event.get("token") if trigger_event else None
    _apply_association(events, target_pids=target_pids, target_processes=target_processes, scoped_token=scoped_token)
    trigger_kind = next((kind.split(":", 1)[1] for kind in trigger_event["kinds"] if kind.startswith("trigger:")), "unknown") if trigger_event else "unknown"
    trigger = {"status": "observed" if trigger_event else "unknown", "kind": trigger_kind, "sourceEventIds": [trigger_event["id"]] if trigger_event else []}

    anchor_candidates = [event for event in events if event.get("isAnchor") and event["entity"] and event["associationStatus"] == "matched"]
    original_candidates = anchor_candidates or ([trigger_event] if trigger_event and trigger_event["entity"] else [])
    original_target, original_conflict = _resolve_entity(original_candidates)

    at_anchor_focus = [
        event for event in events
        if event["associationStatus"] == "matched" and _event_at_or_before(event, anchor_timestamp)
        and any(kind in event["kinds"] for kind in ("focus_entered", "focus_left", "focus_absent", "focus_pending"))
    ]
    blame_candidates = [event for event in at_anchor_focus if event["entity"] and _has_kind(event, "focus_entered")]
    blame_target, blame_conflict = _resolve_entity(blame_candidates)
    post_anchor_focus = [
        event for event in events
        if event["associationStatus"] == "matched" and event["_timestamp"] is not None and anchor_timestamp is not None
        and event["_timestamp"] > anchor_timestamp and any(kind in event["kinds"] for kind in ("focus_entered", "focus_left", "focus_absent", "focus_pending"))
    ]
    focus_status = "unknown"
    if at_anchor_focus:
        latest = max(at_anchor_focus, key=_record_sort_key)
        if _has_kind(latest, "focus_absent"):
            focus_status = "focus_absent"
        elif _has_kind(latest, "focus_entered"):
            focus_status = "focus_present"
        elif _has_kind(latest, "focus_left"):
            focus_status = "focus_left"
        elif _has_kind(latest, "focus_pending"):
            # A WMS pending-focus record is an observed state, not a committed
            # focus owner or a qualified responsibility/blame transfer.
            focus_status = "pending_focus"

    explicit_cancel = next((event for event in events if trigger_event and event["associationStatus"] == "matched" and _has_kind(event, "input_cancelled") and _event_after(trigger_event, event, anchor_timestamp)), None)
    explicit_recovery = next((event for event in events if trigger_event and event["associationStatus"] == "matched" and _has_kind(event, "input_recovered") and _event_after(trigger_event, event, anchor_timestamp)), None)
    suppression = next((event for event in events if trigger_event and event["associationStatus"] == "matched" and _has_kind(event, "queue_suppressed") and _event_after(trigger_event, event, anchor_timestamp)), None)
    if explicit_cancel:
        input_status, input_event = "cancelled", explicit_cancel
    elif explicit_recovery:
        input_status, input_event = "recovered", explicit_recovery
    elif trigger_event:
        input_status, input_event = ("timed_out_at_anchor" if anchor_timestamp is not None else "trigger_observed"), None
    else:
        input_status, input_event = "unknown", None
    queue_status = "cancelled" if explicit_cancel else "suppressed" if suppression else "unknown"

    recents_lifecycle, recents_edges, recents_gaps = _recents_lifecycle(events, anchor_timestamp)
    edges: list[dict[str, Any]] = []
    if trigger_event:
        edges.append(_edge("input_trigger_observed", trigger_event["id"], "inputLifecycle", [trigger_event["id"]]))
    if original_target:
        for event_id in original_target["sourceEventIds"]:
            edges.append(_edge("original_target_observed", event_id, "originalTarget", [event_id]))
    if blame_target:
        for event_id in blame_target["sourceEventIds"]:
            edges.append(_edge("wms_focus_target_observed", event_id, "blameTarget", [event_id]))
    if trigger_event and input_event:
        edges.append(_edge(f"input_{input_status}", trigger_event["id"], input_event["id"], [trigger_event["id"], input_event["id"]]))
    edges.extend(recents_edges)

    dump = _dump_view(dump_quality)
    pending_focus_observed = any(_has_kind(event, "focus_pending") for event in at_anchor_focus)
    gaps = [
        "input_lifecycle_not_causal_proof",
        "queue_lifecycle_unavailable" if queue_status == "unknown" else "",
        "missing_group_associated_input_trigger" if not trigger_event else "",
        "pending_focus_not_blame_proof" if pending_focus_observed else "",
        "missing_wms_blame_mapping" if trigger_event and blame_target is None and not pending_focus_observed else "",
        "conflicting_original_target" if original_conflict else "",
        "conflicting_wms_blame_target" if blame_conflict else "",
        *recents_gaps,
    ]
    if dump["associationStatus"] != "associated":
        gaps.append("dump_not_associated")
    return {
        "schemaVersion": _SCHEMA_VERSION,
        "groupId": group_id,
        "anrType": anr_type,
        "anchor": {"timestamp": timestamp_to_raw(anchor_timestamp) if anchor_timestamp else None, "sourceEventIds": [event["id"] for event in events if event.get("isAnchor")]},
        "trigger": trigger,
        "originalTarget": original_target,
        # An observed WMS focus owner, not a causally established responsible process.
        "blameTarget": blame_target,
        "dump": dump,
        "inputLifecycle": {"status": input_status, "eventIds": [input_event["id"]] if input_event else []},
        "focusLifecycle": {"status": focus_status, "eventIds": [event["id"] for event in at_anchor_focus], "postAnchorEventIds": [event["id"] for event in post_anchor_focus]},
        "queueLifecycle": {"status": queue_status, "eventIds": [event["id"] for event in (explicit_cancel, suppression) if event]},
        "recentsLifecycle": recents_lifecycle,
        "events": [{key: value for key, value in event.items() if key not in {"_timestamp", "_line", "isAnchor"}} for event in events],
        "edges": edges,
        "sourceFindings": _source_findings(events, trigger, original_target, blame_target, dump),
        "gaps": [gap for gap in dict.fromkeys(gaps) if gap],
        "eligibleForHighCausalConfidence": False,
        "finalJudgment": False,
        "notRootCauseYet": True,
        "requiresHumanConfirmation": True,
    }
