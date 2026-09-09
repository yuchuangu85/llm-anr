"""Narrow, source-preserving adapters for ``dumpsys input`` and ``dumpsys window``.

This module deliberately recognises only labelled snapshot fields that the
D1/D2 lifecycle projection can consume today.  It does not decide ANR-group
identity, freshness, process lifetime, staleness, responsibility, or causality.
Those decisions remain in DQ1 and ``input_lifecycle``.

The parser is deliberately fail-closed: a transfer is ``matched`` only when an
InputDispatcher focused-window snapshot and a WindowManager record agree on
window identity, input-channel token, and display ID.  Missing source fields
remain ``unknown``; a mismatch is ``rejected`` rather than repaired by a
package-name or time heuristic.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

_SCHEMA_VERSION = "dumpsys-input-window-adapter-v1"
_MAX_SOURCE_BYTES = 2 * 1024 * 1024
_MAX_SOURCE_LINES = 10_000

_INPUT_HEADER_RE = re.compile(r"^\s*Input Dispatcher State:\s*$", re.IGNORECASE)
_INPUT_FOCUSED_WINDOW_RE = re.compile(r"^\s*FocusedWindow:\s*(?P<value>.+?)\s*$", re.IGNORECASE)
_INPUT_FOCUSED_APP_RE = re.compile(r"^\s*FocusedApplication:\s*(?P<value>.+?)\s*$", re.IGNORECASE)
_INPUT_PENDING_RE = re.compile(r"^\s*PendingEvents?\s*:\s*(?P<value>.*?)\s*$", re.IGNORECASE)
_WINDOW_HEADER_RE = re.compile(r"^\s*WINDOW MANAGER WINDOWS\b", re.IGNORECASE)
_WINDOW_CURRENT_FOCUS_RE = re.compile(r"^\s*mCurrentFocus=(?P<value>.+?)\s*$", re.IGNORECASE)
_WINDOW_FOCUSED_APP_RE = re.compile(r"^\s*mFocusedApp=(?P<value>.+?)\s*$", re.IGNORECASE)
_WINDOW_BLOCK_RE = re.compile(r"^\s*Window\s+#\d+\s+(?P<value>Window\{.+?\})\s*:\s*$", re.IGNORECASE)
_DISPLAY_RE = re.compile(r"\bmDisplayId=(?P<value>\d+)\b")
_INPUT_TOKEN_RE = re.compile(r"\b(?:mInputChannelToken|inputChannelToken)=(?P<value>[^\s,]+)")
_SESSION_PID_RE = re.compile(r"\bmSession=Session\{[^}]*?\s(?P<pid>\d+):")
_COMPONENT_RE = re.compile(r"\bu\d+\s+(?P<process>[A-Za-z][\w.]*(?::[\w.-]+)?)/[^\s,}\]]+")
_WINDOW_ID_RE = re.compile(r"\bWindow\{(?P<value>[^\s}]+)")
_VISIBLE_RE = re.compile(r"\bmHasSurface=true\b")
_VIEW_VISIBLE_RE = re.compile(r"\bmViewVisibility=0x0\b")
_NONE_RE = re.compile(r"^(?:<none>|none|null)$", re.IGNORECASE)


def _source_lines(content: str | None, *, source: str) -> tuple[list[str], list[str]]:
    """Return bounded source lines and parser gaps without coercing bad inputs."""
    if content is None:
        return [], [f"{source}_source_absent"]
    if not isinstance(content, str):
        return [], [f"{source}_source_invalid"]
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_SOURCE_BYTES:
        return [], [f"{source}_source_byte_budget_exceeded"]
    lines = content.splitlines()
    if len(lines) > _MAX_SOURCE_LINES:
        return [], [f"{source}_source_line_budget_exceeded"]
    return lines, []


def _normalise_token(value: str | None) -> str | None:
    if not value:
        return None
    normalised = value.strip().strip("'\"").rstrip(",")
    return None if _NONE_RE.fullmatch(normalised) else normalised


def _window_id(value: str) -> str | None:
    match = _WINDOW_ID_RE.search(value)
    return match.group("value") if match else None


def _process_name(value: str) -> str | None:
    match = _COMPONENT_RE.search(value)
    return match.group("process") if match else None


def _input_focus_record(value: str, line_index: int) -> dict[str, Any] | None:
    if _NONE_RE.fullmatch(value.strip()):
        return None
    token = _INPUT_TOKEN_RE.search(value)
    display = _DISPLAY_RE.search(value)
    return {
        "lineIndex": line_index,
        "windowId": _window_id(value),
        "inputChannelToken": _normalise_token(token.group("value")) if token else None,
        "displayId": display.group("value") if display else None,
        "processName": _process_name(value),
        "rawValue": value,
    }


def _parse_dumpsys_input(content: str | None) -> dict[str, Any]:
    lines, gaps = _source_lines(content, source="dumpsys_input")
    result: dict[str, Any] = {
        "status": "absent" if content is None else "unsupported",
        "focusedWindow": None,
        "focusedApplication": None,
        "queue": {"status": "unknown", "lineIndices": []},
        "lineIndices": [],
        "gaps": gaps,
    }
    if gaps:
        return result
    if not any(_INPUT_HEADER_RE.fullmatch(line) for line in lines):
        result["gaps"].append("input_dispatcher_header_missing")
        return result

    result["status"] = "observed"
    pending_values: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        focus = _INPUT_FOCUSED_WINDOW_RE.fullmatch(line)
        if focus:
            result["focusedWindow"] = _input_focus_record(focus.group("value"), index)
            result["lineIndices"].append(index)
            continue
        app = _INPUT_FOCUSED_APP_RE.fullmatch(line)
        if app:
            value = app.group("value")
            result["focusedApplication"] = {
                "lineIndex": index,
                "processName": _process_name(value),
                "rawValue": value,
            }
            result["lineIndices"].append(index)
            continue
        pending = _INPUT_PENDING_RE.fullmatch(line)
        if pending:
            pending_values.append((index, pending.group("value")))

    if pending_values:
        line_indices = [index for index, _ in pending_values]
        values = [value.strip() for _, value in pending_values]
        queue_status = "empty" if all(_NONE_RE.fullmatch(value) or value in {"0", "[]"} for value in values) else "pending"
        result["queue"] = {"status": queue_status, "lineIndices": line_indices}
        result["lineIndices"].extend(line_indices)
    else:
        result["gaps"].append("input_queue_snapshot_missing")
    if result["focusedWindow"] is None:
        result["gaps"].append("input_focused_window_missing")
    return result


def _finalise_window(record: dict[str, Any] | None, records: list[dict[str, Any]]) -> None:
    if record is None:
        return
    record["visible"] = bool(record.pop("_hasSurface", False) and record.pop("_viewVisible", False))
    record.pop("_hasSurface", None)
    record.pop("_viewVisible", None)
    records.append(record)


def _parse_dumpsys_window(content: str | None) -> dict[str, Any]:
    lines, gaps = _source_lines(content, source="dumpsys_window")
    result: dict[str, Any] = {
        "status": "absent" if content is None else "unsupported",
        "currentFocus": None,
        "focusedApplication": None,
        "windows": [],
        "lineIndices": [],
        "gaps": gaps,
    }
    if gaps:
        return result
    if not any(_WINDOW_HEADER_RE.match(line) for line in lines):
        result["gaps"].append("window_manager_header_missing")
        return result

    result["status"] = "observed"
    current: dict[str, Any] | None = None
    for index, line in enumerate(lines):
        block = _WINDOW_BLOCK_RE.fullmatch(line)
        if block:
            _finalise_window(current, result["windows"])
            value = block.group("value")
            current = {
                "lineIndex": index,
                "windowId": _window_id(value),
                "inputChannelToken": None,
                "displayId": None,
                "pid": None,
                "processName": _process_name(value),
                "visible": False,
                "rawValue": value,
                "_hasSurface": False,
                "_viewVisible": False,
            }
            result["lineIndices"].append(index)
            continue
        focus = _WINDOW_CURRENT_FOCUS_RE.fullmatch(line)
        if focus:
            value = focus.group("value")
            result["currentFocus"] = {
                "lineIndex": index,
                "windowId": _window_id(value),
                "processName": _process_name(value),
                "rawValue": value,
            }
            result["lineIndices"].append(index)
            continue
        app = _WINDOW_FOCUSED_APP_RE.fullmatch(line)
        if app:
            value = app.group("value")
            result["focusedApplication"] = {
                "lineIndex": index,
                "processName": _process_name(value),
                "rawValue": value,
            }
            result["lineIndices"].append(index)
            continue
        if current is None:
            continue
        display = _DISPLAY_RE.search(line)
        if display:
            current["displayId"] = display.group("value")
            result["lineIndices"].append(index)
        token = _INPUT_TOKEN_RE.search(line)
        if token:
            current["inputChannelToken"] = _normalise_token(token.group("value"))
            result["lineIndices"].append(index)
        session = _SESSION_PID_RE.search(line)
        if session:
            current["pid"] = session.group("pid")
            result["lineIndices"].append(index)
        process = _process_name(line)
        if process:
            current["processName"] = process
            result["lineIndices"].append(index)
        if _VISIBLE_RE.search(line):
            current["_hasSurface"] = True
            result["lineIndices"].append(index)
        if _VIEW_VISIBLE_RE.search(line):
            current["_viewVisible"] = True
            result["lineIndices"].append(index)
    _finalise_window(current, result["windows"])
    result["lineIndices"] = sorted(set(result["lineIndices"]))
    if result["currentFocus"] is None:
        result["gaps"].append("window_current_focus_missing")
    return result


def _transfer(input_result: Mapping[str, Any], window_result: Mapping[str, Any]) -> dict[str, Any]:
    unknown = {
        "status": "unknown",
        "inputLineIndex": None,
        "windowLineIndex": None,
        "windowId": None,
        "inputChannelToken": None,
        "displayId": None,
        "pid": None,
        "processName": None,
    }
    input_focus = input_result.get("focusedWindow")
    current_focus = window_result.get("currentFocus")
    if not isinstance(input_focus, Mapping) or not isinstance(current_focus, Mapping):
        return unknown
    input_window_id = input_focus.get("windowId")
    window_id = current_focus.get("windowId")
    if not input_window_id or not window_id:
        return unknown
    if input_window_id != window_id:
        return {**unknown, "status": "rejected", "windowId": str(input_window_id)}
    candidates = [
        record for record in window_result.get("windows", [])
        if isinstance(record, Mapping) and record.get("windowId") == input_window_id
    ]
    if len(candidates) != 1:
        return {**unknown, "status": "conflicted" if len(candidates) > 1 else "unknown", "windowId": str(input_window_id)}
    record = candidates[0]
    input_token = input_focus.get("inputChannelToken")
    window_token = record.get("inputChannelToken")
    input_display = input_focus.get("displayId")
    window_display = record.get("displayId")
    result = {
        "status": "unknown",
        "inputLineIndex": input_focus.get("lineIndex"),
        "windowLineIndex": record.get("lineIndex"),
        "windowId": str(input_window_id),
        "inputChannelToken": window_token,
        "displayId": window_display,
        "pid": record.get("pid"),
        "processName": record.get("processName"),
    }
    if not input_token or not window_token or not input_display or not window_display:
        return result
    if input_token != window_token or input_display != window_display:
        return {**result, "status": "rejected"}
    return {**result, "status": "matched"}


def parse_dumpsys_input_window(
    *,
    dumpsys_input: str | None,
    dumpsys_window: str | None,
) -> dict[str, Any]:
    """Parse the bounded, supported dumpsys input/window snapshot fields.

    This returns source-local observations.  The caller must route ANR-group
    identity and time decisions through DQ1 / the lifecycle consumer.
    """
    input_result = _parse_dumpsys_input(dumpsys_input)
    window_result = _parse_dumpsys_window(dumpsys_window)
    transfer = _transfer(input_result, window_result)
    gaps = list(dict.fromkeys([
        *input_result["gaps"],
        *window_result["gaps"],
        "input_window_transfer_unavailable" if transfer["status"] == "unknown" else "",
        "input_window_transfer_conflicted" if transfer["status"] == "conflicted" else "",
        "input_window_transfer_rejected" if transfer["status"] == "rejected" else "",
        "dumpsys_capture_time_unavailable",
    ]))
    return {
        "schemaVersion": _SCHEMA_VERSION,
        "input": input_result,
        "window": window_result,
        "transfer": transfer,
        "gaps": [gap for gap in gaps if gap],
        "finalJudgment": False,
        "notRootCauseYet": True,
        "requiresHumanConfirmation": True,
    }
