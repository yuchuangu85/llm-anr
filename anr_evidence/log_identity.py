"""Conservative line identities, not inferred cross-process ownership.

Only recognized fields provide numeric identities. An emitter is distinct
from a target mentioned in the payload; unrecognized lines stay unknown.
Process names match exactly, including any explicit subprocess suffix.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


_TIMESTAMP = re.compile(r"^\s*(?:\d{4}-)?\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?\s+")
_THREADTIME = re.compile(r"^(?:\S+\s+)?(?P<pid>\d+)\s+(?P<tid>\d+)\s+[VDIWEF]\s+[^:]+:\s*", re.I)
_BRIEF = re.compile(r"^[VDIWEF](?:/|\s+)[\w.$/-]+\(\s*(?P<pid>\d+)\s*\)\s*:\s*", re.I)
_EXPLICIT = re.compile(r"(?<![\w])(?P<kind>pid|tid|uid)(?:\s*[:=]\s*|\s+)(?P<value>\d+)(?![\w.])", re.I)
_AM_ANR = re.compile(r"\bam_anr\s*:?\s*\[\s*\d+\s*,\s*(?P<pid>\d+)\s*,\s*(?P<process>[^,\]\s]+)", re.I)
_PROCESS_RECORD = re.compile(r"\bProcessRecord\{[^}\n]*?\b(?P<pid>\d+):(?P<process>[\w.]+(?::[\w.-]+)?)/", re.I)
_PID_NAME = re.compile(r"(?<![\w.])(?P<pid>\d+)/(?P<process>[A-Za-z][\w.]*(?::[\w.-]+)?)(?=:|\s|$)")
_PROCESS_FIELD = re.compile(r"\b(?:process|package|owner)\s*[:=]\s*(?P<process>[A-Za-z][\w.]*(?::[\w.-]+)?)\b", re.I)


@dataclass(frozen=True)
class LineIdentity:
    emitter_pid: str | None = None
    emitter_tid: str | None = None
    target_pids: tuple[str, ...] = ()
    target_tids: tuple[str, ...] = ()
    uids: tuple[str, ...] = ()
    target_processes: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return "parsed" if any((self.emitter_pid, self.emitter_tid, self.target_pids,
                                self.target_tids, self.uids, self.target_processes)) else "unknown"


def contains_exact_process(line: str, process_name: str) -> bool:
    """A subprocess is a distinct process, never a prefix match for its parent."""
    if not process_name or not process_name.strip():
        return False
    return re.search(rf"(?<![\w.:-]){re.escape(process_name.strip())}(?![\w.:-])", line) is not None


def parse_line_identity(line: str) -> LineIdentity:
    """Parse threadtime/brief emitters and explicit payload target fields.

    UID mentions are inventory, not proof of the target process's UID. Bare
    numbers, timestamps, durations and unknown field names are not identities.
    """
    without_timestamp = _TIMESTAMP.sub("", line, count=1)
    header = _THREADTIME.match(without_timestamp) or _BRIEF.match(without_timestamp)
    pid = header.group("pid") if header else None
    tid = header.groupdict().get("tid") if header else None
    payload = without_timestamp[header.end():] if header else without_timestamp
    values: dict[str, set[str]] = {"pid": set(), "tid": set(), "uid": set()}
    processes: set[str] = set()
    for match in _EXPLICIT.finditer(payload):
        values[match.group("kind").lower()].add(match.group("value"))
    # am_anr's target lives in the record, not in its system-server header.
    for pattern in (_AM_ANR, _PROCESS_RECORD, _PID_NAME):
        for match in pattern.finditer(without_timestamp if pattern is _AM_ANR else payload):
            values["pid"].add(match.group("pid"))
            processes.add(match.group("process"))
    for match in _PROCESS_FIELD.finditer(payload):
        # ``owner=ProcessRecord{...}`` is a framework object wrapper, not a
        # process name.  Its embedded ``pid:process/`` form is parsed by
        # ``_PROCESS_RECORD`` above; accepting the wrapper would manufacture a
        # contradictory second target process.
        if payload[match.end():].lstrip().startswith("{"):
            continue
        processes.add(match.group("process"))
    return LineIdentity(
        emitter_pid=pid, emitter_tid=tid,
        target_pids=tuple(sorted(values["pid"], key=int)),
        target_tids=tuple(sorted(values["tid"], key=int)),
        uids=tuple(sorted(values["uid"], key=int)),
        target_processes=tuple(sorted(processes)),
    )
