"""Deterministic Input/Window/Surface evidence digest selection.

The integer score produced here is a retrieval heuristic.  It ranks lines for
the compact AI-context digest and must not be interpreted as causal confidence.
The caller retains the complete filtered logcat separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
import re
from typing import Any, Iterable

from .log_filter import parse_log_timestamp, timestamp_to_raw


SUPPORTED_ANR_TYPES = frozenset({"input_dispatching_timeout", "no_focus_window"})
FAMILIES = ("input_trigger", "window_state", "surface_state")

_DIRECT_TRIGGER_RE = re.compile(
    r"(?:"
    r"input\s+dispatch(?:ing)?\s+time(?:d\s*out|out)"
    r"|does\s+not\s+have\s+a\s+focused\s+window"
    r"|no\s+focus(?:ed)?\s+window"
    r"|application\s+not\s+responding"
    r"|\banr\s+in\s+\S+"
    r")",
    re.IGNORECASE,
)
_WINDOW_SOURCE_RE = re.compile(
    r"(?:\bwindowmanager\b|\bwindowmanagerservice\b|\bwms\b|\bwm_focus\b|"
    r"\binput_focus\b|\bmcurrentfocus\b|\bmfocusedapp\b)",
    re.IGNORECASE,
)
_WINDOW_FAILURE_RE = re.compile(
    r"(?:"
    r"no\s+focus(?:ed)?\s+window|focus(?:ed)?\s*(?:=|:)?\s*(?:null|none)"
    r"|mcurrentfocus\s*=\s*null|mfocusedapp\s*=\s*null"
    r"|not[_\s-]?visible|visibility\s*(?:=|:)\s*(?:false|gone|invisible)"
    r"|not[_\s-]?drawn|drawn\s*(?:=|:)\s*false|waiting\s+(?:for|to\s+be)\s+drawn"
    r"|not[_\s-]?resumed|resumed\s*(?:=|:)\s*false"
    r"|focus(?:ed)?(?:\s+window)?\s+(?:changed|lost|request|transfer)"
    r")",
    re.IGNORECASE,
)
_SURFACE_SOURCE_RE = re.compile(
    r"(?:\bsurfaceflinger\b|\bsurfacecontrol\b|\bbufferqueue\b|\bgralloc\b|"
    r"\bhwc\b|\bfence\b|\blatch\b|\bpresent\b|\bbuffer\b|\bsurface\b)",
    re.IGNORECASE,
)
_SURFACE_FAILURE_RE = re.compile(
    r"(?:"
    r"time(?:d\s*out|out)|hang|hung|stuck|block(?:ed|ing)?|failed|failure"
    r"|not\s+signaled|unsignaled|missed|dropped|abandon(?:ed)?|no\s+buffer"
    r")",
    re.IGNORECASE,
)

_TIMESTAMP_PREFIX_RE = re.compile(
    r"^\s*(?:(?:\d{4}-)?\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?)\s*"
)
_THREADTIME_PREFIX_RE = re.compile(
    r"^(?:\d+\s+\d+\s+)?[VDIWEF]\s+(?:[\w.$/-]+)(?:\(\s*\d+\))?\s*:\s*",
    re.IGNORECASE,
)
_BRIEF_PREFIX_RE = re.compile(
    r"^[VDIWEF]/[\w.$/-]+(?:\(\s*\d+\))?\s*:\s*",
    re.IGNORECASE,
)
_TAG_PREFIX_RE = re.compile(
    r"^[\w.$/-]+(?:\(\s*\d+\))?\s*:\s*",
    re.IGNORECASE,
)
_LOGCAT_THREADTIME_IDENTITY_RE = re.compile(
    r"^\s*(?:(?:\d{4}-)?\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?)\s+"
    r"(?:(?:\S+)\s+)?(?P<pid>\d+)\s+\d+\s+[VDIWEF]\s+",
    re.IGNORECASE,
)
_LOGCAT_TAG_PID_RE = re.compile(
    r"^[VDIWEF](?:/|\s+)[\w.$/-]+\(\s*(?P<pid>\d+)\s*\)\s*:",
    re.IGNORECASE,
)
_EXPLICIT_PID_RE = re.compile(r"\bpid\s*[:=]\s*(?P<pid>\d+)\b", re.IGNORECASE)
_PROCESS_RECORD_PID_RE = re.compile(
    r"\bProcessRecord\{[^}\n]*?\b(?P<pid>\d+):",
    re.IGNORECASE,
)
_PID_PROCESS_NAME_RE = re.compile(
    r"(?<!\d)(?P<pid>\d+)/[A-Za-z][A-Za-z0-9_.]*(?::[A-Za-z0-9_.-]+)?(?=:|\s|$)"
)


@dataclass(frozen=True)
class _Candidate:
    line_index: int
    raw_line: str
    normalized_payload: str
    family: str
    timestamp: datetime | None
    delta_t_seconds: float | None
    relevance_score: int
    score_reasons: tuple[str, ...]

    @property
    def rank_key(self) -> tuple[float, float, int]:
        distance = abs(self.delta_t_seconds) if self.delta_t_seconds is not None else math.inf
        return (-self.relevance_score, distance, self.line_index)

    @property
    def dedupe_key(self) -> tuple[float, int]:
        distance = abs(self.delta_t_seconds) if self.delta_t_seconds is not None else math.inf
        return (distance, self.line_index)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": "logcat",
            "rawLine": self.raw_line,
            "timestamp": timestamp_to_raw(self.timestamp) if self.timestamp else None,
            "deltaTSeconds": self.delta_t_seconds,
            "family": self.family,
            "relevanceScore": self.relevance_score,
            "scoreReasons": list(self.score_reasons),
            "lineIndex": self.line_index,
        }


def build_input_window_digest(
    logcat_lines: Iterable[str],
    *,
    anr_type: str,
    anchor_timestamp: datetime | str | None,
    package_name: str | None = None,
    target_pid: int | str | None = None,
    max_items: int = 12,
    max_per_family: int = 4,
) -> dict[str, Any]:
    """Build a bounded digest of InputDispatcher, WMS, and SF log evidence.

    Unsupported ANR types return immediately without consuming ``logcat_lines``.
    Matching and deduplication affect only the returned digest; input lines are
    never mutated.  Limits are clamped to the Milestone A hard caps.
    """

    if anr_type not in SUPPORTED_ANR_TYPES:
        return _empty_digest()
    if max_items < 0:
        raise ValueError("max_items must be non-negative")
    if max_per_family < 1:
        raise ValueError("max_per_family must be at least 1")

    item_limit = min(max_items, 12)
    family_limit = min(max_per_family, 4)
    anchor = _parse_anchor(anchor_timestamp)
    target_pid_text = (str(target_pid).strip() or None) if target_pid is not None else None

    candidates: list[_Candidate] = []
    for line_index, raw_line in enumerate(logcat_lines):
        family = _classify_family(raw_line)
        if family is None:
            continue
        candidates.append(
            _build_candidate(
                raw_line,
                line_index=line_index,
                family=family,
                anchor=anchor,
                package_name=package_name,
                target_pid=target_pid_text,
            )
        )

    unique_by_payload: dict[str, _Candidate] = {}
    for candidate in candidates:
        current = unique_by_payload.get(candidate.normalized_payload)
        if current is None or candidate.dedupe_key < current.dedupe_key:
            unique_by_payload[candidate.normalized_payload] = candidate

    ranked = sorted(unique_by_payload.values(), key=lambda candidate: candidate.rank_key)
    present_families = tuple(
        family for family in FAMILIES if any(candidate.family == family for candidate in ranked)
    )
    if item_limit < len(present_families):
        raise ValueError("max_items is too small to retain every present family")

    selected: list[_Candidate] = []
    selected_indexes: set[int] = set()
    family_counts = {family: 0 for family in FAMILIES}

    for family in present_families:
        best = next(candidate for candidate in ranked if candidate.family == family)
        selected.append(best)
        selected_indexes.add(best.line_index)
        family_counts[family] += 1

    for candidate in ranked:
        if len(selected) >= item_limit:
            break
        if candidate.line_index in selected_indexes:
            continue
        if family_counts[candidate.family] >= family_limit:
            continue
        selected.append(candidate)
        selected_indexes.add(candidate.line_index)
        family_counts[candidate.family] += 1

    selected.sort(key=lambda candidate: candidate.rank_key)
    return {
        "items": [candidate.to_dict() for candidate in selected],
        "countsByFamily": family_counts,
        "candidateLineCount": len(candidates),
        "duplicateCount": len(candidates) - len(ranked),
        "truncatedCount": len(ranked) - len(selected),
    }


def _empty_digest() -> dict[str, Any]:
    return {
        "items": [],
        "countsByFamily": {family: 0 for family in FAMILIES},
        "candidateLineCount": 0,
        "duplicateCount": 0,
        "truncatedCount": 0,
    }


def _parse_anchor(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return parse_log_timestamp(value)
    return None


def _classify_family(line: str) -> str | None:
    if _DIRECT_TRIGGER_RE.search(line):
        return "input_trigger"
    if _WINDOW_SOURCE_RE.search(line) and _WINDOW_FAILURE_RE.search(line):
        return "window_state"
    if _SURFACE_SOURCE_RE.search(line) and _SURFACE_FAILURE_RE.search(line):
        return "surface_state"
    return None


def _build_candidate(
    raw_line: str,
    *,
    line_index: int,
    family: str,
    anchor: datetime | None,
    package_name: str | None,
    target_pid: str | None,
) -> _Candidate:
    timestamp = parse_log_timestamp(raw_line)
    delta_t_seconds = None
    if timestamp is not None and anchor is not None:
        delta_t_seconds = round((timestamp - anchor).total_seconds(), 6)

    score_reasons: list[str] = []
    score = 0
    if family == "input_trigger":
        score += 50
        score_reasons.append("direct_trigger")
    elif family == "window_state":
        score += 35
        score_reasons.append("window_state_failure")
    else:
        score += 35
        score_reasons.append("surface_state_failure")

    if _contains_target_identity(raw_line, package_name=package_name, target_pid=target_pid):
        score += 20
        score_reasons.append("target_identity")

    if delta_t_seconds is not None:
        distance = abs(delta_t_seconds)
        if distance <= 1:
            score += 15
            score_reasons.append("anchor_proximity_1s")
        elif distance <= 5:
            score += 8
            score_reasons.append("anchor_proximity_5s")

    return _Candidate(
        line_index=line_index,
        raw_line=raw_line,
        normalized_payload=_normalized_payload(raw_line),
        family=family,
        timestamp=timestamp,
        delta_t_seconds=delta_t_seconds,
        relevance_score=min(score, 100),
        score_reasons=tuple(score_reasons),
    )


def _contains_target_identity(
    line: str,
    *,
    package_name: str | None,
    target_pid: str | None,
) -> bool:
    if package_name and _contains_exact_package(line, package_name):
        return True
    if target_pid and target_pid.isdigit() and target_pid in _structured_pids(line):
        return True
    return False


def _contains_exact_package(line: str, package_name: str) -> bool:
    package = package_name.strip()
    if not package:
        return False
    return bool(re.search(
        rf"(?<![A-Za-z0-9_.]){re.escape(package)}(?![A-Za-z0-9_.])",
        line,
        re.IGNORECASE,
    ))


def _structured_pids(line: str) -> set[str]:
    pids: set[str] = set()

    threadtime = _LOGCAT_THREADTIME_IDENTITY_RE.match(line)
    if threadtime:
        pids.add(threadtime.group("pid"))

    without_timestamp = _TIMESTAMP_PREFIX_RE.sub("", line, count=1)
    tag_pid = _LOGCAT_TAG_PID_RE.match(without_timestamp)
    if tag_pid:
        pids.add(tag_pid.group("pid"))

    for pattern in (_EXPLICIT_PID_RE, _PROCESS_RECORD_PID_RE, _PID_PROCESS_NAME_RE):
        pids.update(match.group("pid") for match in pattern.finditer(line))

    return pids


def _normalized_payload(line: str) -> str:
    payload = _TIMESTAMP_PREFIX_RE.sub("", line, count=1)
    payload = _THREADTIME_PREFIX_RE.sub("", payload, count=1)
    payload = _BRIEF_PREFIX_RE.sub("", payload, count=1)
    payload = _TAG_PREFIX_RE.sub("", payload, count=1)
    return payload.strip()
